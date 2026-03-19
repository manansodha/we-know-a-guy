import paho.mqtt.client as mqtt
import json
import os
import time
import threading
import numpy as np
from datetime import datetime

NODE_ID = os.getenv("NODE_ID", "TLM0100")
ZONE = os.getenv("ZONE", "Floor_1") # The agent's specific sub-zone

class SubZoneAgent:
    def __init__(self):
        self.is_leader = False
        self.status = "HEALTHY"
        self.detail = "Nominal"
        
        self.zone_peers = {} # Only track peers in MY zone
        self.registry = {}   # Zone-wide status for the leader
        
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self.on_message

    def connect(self):
        try:
            # Connect to local MQTT broker (assuming a mesh bridge is configured)
            self.client.connect("mosquitto", 1883, 60)
            self.client.subscribe("belimo/gossip")
            self.client.subscribe(f"belimo/telemetry/{NODE_ID}")
            self.client.loop_start()
            print(f"🚀 {NODE_ID} online in {ZONE}")
        except Exception as e:
            print(f"❌ Connection error: {e}")
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

            # 1. ZONAL ELECTION: Only listen to peers in my zone
            if msg.topic == "belimo/gossip" and sender_zone == ZONE:
                self.zone_peers[sender_id] = data.get('timestamp')
                
                # If I am the zone leader, record their data
                if self.is_leader:
                    self.registry[sender_id] = data
                    self.print_zone_status()

            # 2. LOCAL SENSING (Anomaly detection - same as before)
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
                    print(f"\n👑 {NODE_ID}: I am now LEADER of {ZONE}")
            else:
                if self.is_leader:
                    self.is_leader = False
                    print(f"\n🛡️ {NODE_ID}: Stepping down. {highest_zone_id} leads {ZONE}")
            
            time.sleep(2)

    def print_zone_status(self):
        """Leader table limited to current Zone."""
        if os.isatty(0): os.system('clear')
        print(f"=== 👑 {ZONE} LEADER: {NODE_ID} | {datetime.now().strftime('%H:%M:%S')} ===")
        print(f"{'ID':<10} | {'STATUS':<10} | {'DETAIL'}")
        print("-" * 50)
        for node in sorted(self.registry.keys()):
            info = self.registry[node]
            icon = "✅" if info['status'] == "HEALTHY" else "⚠️"
            print(f"{node:<10} | {icon} {info['status']:<8} | {info['detail']}")

    def perform_anomaly_detection(self, field, value):
        # (Maintain your Z-score logic here)
        pass

if __name__ == "__main__":
    agent = SubZoneAgent()
    agent.connect()
    while True: time.sleep(1)