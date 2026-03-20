import paho.mqtt.client as mqtt
import json
import os
import time
import threading
import numpy as np
from datetime import datetime

NODE_ID = os.getenv("NODE_ID", "TLM0100")
ZONE = os.getenv("ZONE", "Floor_1") # The agent's specific sub-zone
WINDOW_SIZE = 5
ANOMALY_FIELD = os.getenv("ANOMALY_FIELD", "").strip()
PEER_TTL_SECONDS = float(os.getenv("PEER_TTL_SECONDS", "10"))
ZONE_ALERT_RATIO = float(os.getenv("ZONE_ALERT_RATIO", "0.30"))

class SubZoneAgent:
    def __init__(self):
        self.is_leader = False
        self.status = "HEALTHY"
        self.detail = "Nominal"
        self.history = {"torque": []}
        self.anomaly_field = ANOMALY_FIELD or None
        
        self.zone_peers = {} # Only track agents in the same zone
        self.registry = {}   # Zone-wide status for the leader
        self.system_alert_active = False
        
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self.on_message

    def connect(self):
        try:
            # Connect to local MQTT broker (assuming a mesh bridge is configured)
            self.client.connect("mosquitto", 1883, 60)
            self.client.subscribe("belimo/gossip")
            self.client.subscribe(f"belimo/telemetry/{NODE_ID}")
            self.client.loop_start()
            print(f"{NODE_ID} online in {ZONE}")
        except Exception as e:
            print(f"Connection error: {e}")
            return

        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self.zonal_election, daemon=True).start()

    def heartbeat_loop(self):
        while True:
            payload = {
                "node_id": NODE_ID,
                "zone": ZONE,
                "is_leader": self.is_leader,
                "status": self.status,
                "detail": self.detail,
                "timestamp": time.time(),
                "time_str": datetime.now().strftime("%H:%M:%S")
            }
            # Broadcast to the whole swarm
            self.client.publish("belimo/gossip", json.dumps(payload))
            time.sleep(3)

    def on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            sender_zone = data.get('zone')
            sender_id = data.get('node_id')

            # listen to peers in my zone
            if msg.topic == "belimo/gossip" and sender_zone == ZONE:
                self.zone_peers[sender_id] = data.get('timestamp')
                
                # If I am the zone leader, record their data
                if self.is_leader:
                    self.registry[sender_id] = data
                    self.prune_stale_registry()
                    self.evaluate_zone_health()
                    self.print_zone_status()

            # Anomaly detection
            if msg.topic.endswith(NODE_ID):
                self.perform_anomaly_detection(data.get('_field'), data.get('_value'))
                    
        except Exception as e:
            pass

    def zonal_election(self):
        """Elects a leader specifically for this ZONE."""
        while True:
            now = time.time()
            # Only count peers in my zone seen in last 10s
            active_zone_nodes = [id for id, t in self.zone_peers.items() if (now - t) < 10]
            if NODE_ID not in active_zone_nodes: active_zone_nodes.append(NODE_ID)
            
            # The highest ID in the ZONE wins
            highest_zone_id = max(active_zone_nodes)
            
            if NODE_ID == highest_zone_id:
                if not self.is_leader:
                    self.is_leader = True
                    print(f"\n{NODE_ID}: I am now LEADER of {ZONE}")
            else:
                if self.is_leader:
                    self.is_leader = False
                    self.system_alert_active = False
                    print(f"\n{NODE_ID}: Stepping down. {highest_zone_id} leads {ZONE}")
            
            time.sleep(2)

    def prune_stale_registry(self):
        now = time.time()
        pruned = {}
        for node, info in self.registry.items():
            try:
                ts = float(info.get("timestamp", 0.0))
            except (TypeError, ValueError):
                ts = 0.0
            if (now - ts) < PEER_TTL_SECONDS:
                pruned[node] = info
        self.registry = pruned

    def evaluate_zone_health(self):
        self.prune_stale_registry()
        self.registry[NODE_ID] = {
            "node_id": NODE_ID,
            "zone": ZONE,
            "is_leader": self.is_leader,
            "status": self.status,
            "detail": self.detail,
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S"),
        }

        total_nodes = len(self.registry)
        anomaly_nodes = sum(
            1 for info in self.registry.values() if info.get("status") == "ANOMALY"
        )
        ratio = (anomaly_nodes / total_nodes) if total_nodes else 0.0

        if ratio >= ZONE_ALERT_RATIO and not self.system_alert_active:
            print(
                f"[LEADER {NODE_ID}] WARNING zone anomaly: "
                f"{anomaly_nodes}/{total_nodes} nodes ({ratio:.0%}) exceed threshold "
                f"{ZONE_ALERT_RATIO:.0%}"
            )
            self.system_alert_active = True
        elif ratio < ZONE_ALERT_RATIO and self.system_alert_active:
            print(f"[LEADER {NODE_ID}] RECOVERY zone anomaly ratio back to {ratio:.0%}")
            self.system_alert_active = False

    def print_zone_status(self):
        """Leader table limited to current Zone."""
        if os.isatty(0): os.system('clear')
        print(f"=== {ZONE} LEADER: {NODE_ID} | {datetime.now().strftime('%H:%M:%S')} ===")
        print(f"{'ID':<10} | {'STATUS':<10} | {'DETAIL'}")
        print("-" * 50)
        for node in sorted(self.registry.keys()):
            info = self.registry[node]
            print(f"{node:<10} | {info['status']:<8} | {info['detail']}")

    def check_anomaly(self, current_torque):
        """3-sigma anomaly check against the previous 5 torque readings."""
        if len(self.history["torque"]) < WINDOW_SIZE:
            self.history["torque"].append(current_torque)
            return "CALIBRATING"

        # Baseline from last 5 points only (before current sample)
        window = self.history["torque"][-WINDOW_SIZE:]
        mean = np.mean(window)
        std = np.std(window)

        is_anomaly = abs(current_torque - mean) > (3 * std)

        # Slide window forward with current sample, keeping only 5 points
        self.history["torque"].append(current_torque)
        self.history["torque"] = self.history["torque"][-WINDOW_SIZE:]

        return "ANOMALY" if is_anomaly else "HEALTHY"

    def perform_anomaly_detection(self, field, value):
        if value is None:
            return

        if self.anomaly_field is None and field:
            self.anomaly_field = field

        if self.anomaly_field and field and field != self.anomaly_field:
            return

        try:
            current_torque = float(value)
        except (TypeError, ValueError):
            return

        state = self.check_anomaly(current_torque)
        self.status = state

        tracked_field = self.anomaly_field or field or "value"
        if state == "CALIBRATING":
            self.detail = f"Calibrating {len(self.history['torque'])}/{WINDOW_SIZE} on {tracked_field}"
        elif state == "ANOMALY":
            self.detail = f"3-sigma breach on {tracked_field}: {current_torque:.4f}"
        else:
            self.detail = f"Nominal {tracked_field}: {current_torque:.4f}"

        if self.is_leader:
            self.evaluate_zone_health()

if __name__ == "__main__":
    agent = SubZoneAgent()
    agent.connect()
    while True: time.sleep(1)