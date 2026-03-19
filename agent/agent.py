import os
import json
import time
import re
import random
import numpy as np
import paho.mqtt.client as mqtt
from datetime import datetime
from urllib import request, error

# --- CONFIGURATION ---
NODE_ID = os.getenv("NODE_ID", "DEV_NODE")
ZONE = os.getenv("ZONE", "Unknown")
BROKER = os.getenv("BROKER", "localhost")
LEADER_SLOT = int(os.getenv("LEADER_SLOT", "1"))
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://host.docker.internal:5555/api/report")
TOPIC_GOSSIP = "belimo/swarm/gossip"
TOPIC_LEADER = "belimo/swarm/leader"
TOPIC_CONTROL = "belimo/swarm/control"

class BelimoAgent:
    def __init__(self):
        # In scaled compose deployments, HOSTNAME contains a stable service slot suffix.
        self.node_id = os.getenv("HOSTNAME", NODE_ID)
        self.zone = self._resolve_zone()
        self.slot = self._extract_slot(self.node_id)
        self.torque_memory = []
        self.started_at = time.time()
        self.power_on = True
        self.status = "OPTIMAL"
        self.role = "LEADER_ACTIVE" if self.slot == LEADER_SLOT else "FOLLOWER"
        self.current_leader = None
        self.leader_timeout = 0
        self.next_leader_announce_at = 0
        self.next_leader_claim_at = time.time() + random.uniform(4, 8)
        
        # Connect to the decentralized MQTT bus (with retries)
        self.client = mqtt.Client(client_id=self.node_id)
        self.client.on_message = self.on_message
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        
        # Retry connection logic
        max_retries = 10
        for attempt in range(max_retries):
            try:
                print(f"[{self.node_id}] Attempting to connect to MQTT broker at {BROKER}:1883 (attempt {attempt+1}/{max_retries})")
                self.client.connect(BROKER, 1883, 60)
                self.client.loop_start()
                print(f"[{self.node_id}] Connected to MQTT broker!")
                break
            except Exception as e:
                print(f"[{self.node_id}] Connection failed: {e}. Retrying in 2s...")
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    print(f"[{self.node_id}] Failed to connect after {max_retries} attempts. Exiting.")
                    raise
        
        if self.role == "LEADER_ACTIVE":
            self.current_leader = self.node_id

        print(f"[{self.node_id}] Agent initialized in zone {self.zone}. Slot: {self.slot}. Role: {self.role}")

    def _extract_slot(self, node_id):
        match = re.search(r"[-_](\d+)$", node_id)
        if match:
            return int(match.group(1))
        return -1

    def _resolve_zone(self):
        if ZONE and ZONE != "Unknown":
            return ZONE

        slot = self._extract_slot(os.getenv("HOSTNAME", NODE_ID))
        if slot > 0:
            return f"Zone_{slot:02d}"
        return "Zone_Unknown"

    def _send_dashboard_report(self, msg):
        payload = {
            "hostname": msg["sender"],
            "group": msg["zone"],
            "ip": "mqtt",
            "cpu_percent": round(msg.get("cpu_percent", 0.0), 1),
            "mem_percent": round(msg.get("mem_percent", 0.0), 1),
            "disk_percent": round(msg.get("disk_percent", 0.0), 1),
            "cpu_temp": round(msg.get("cpu_temp", msg.get("torque", 0.0)), 2),
            "uptime": msg.get("uptime", "swarm"),
            "load_1m": round(msg.get("load_1m", 0.0), 2),
            "swarm_status": msg.get("status", "UNKNOWN"),
            "detail": msg.get("detail", ""),
            "leader_id": self.current_leader or self.node_id,
            "reported_at": msg.get("timestamp", datetime.now().isoformat()),
        }

        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            DASHBOARD_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=2):
                pass
        except (error.URLError, TimeoutError) as exc:
            print(f"[{self.node_id}] Dashboard forward failed: {exc}")

    def _announce_leader(self):
        if self.role != "LEADER_ACTIVE":
            return

        self.current_leader = self.node_id
        msg = {
            "timestamp": datetime.now().isoformat(),
            "node_id": self.node_id,
            "role": "LEADER_ACTIVE",
            "zone": self.zone,
        }
        self.client.publish(TOPIC_LEADER, json.dumps(msg))
        print(f"[{self.node_id}] Announced myself as leader for slot {LEADER_SLOT}")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[{self.node_id}] Successfully connected to MQTT broker")
            self.client.subscribe([(TOPIC_GOSSIP, 0), (TOPIC_LEADER, 0), (TOPIC_CONTROL, 0)])
            self._announce_leader()
        else:
            print(f"[{self.node_id}] Connection failed with code {rc}")

    def on_disconnect(self, client, userdata, rc):
        if rc != 0:
            print(f"[{self.node_id}] Unexpected disconnection ({rc}). Reconnecting...")
            for attempt in range(1, 11):
                try:
                    time.sleep(2)
                    self.client.reconnect()
                    print(f"[{self.node_id}] Reconnected to MQTT broker")
                    return
                except Exception as exc:
                    print(f"[{self.node_id}] Reconnect attempt {attempt}/10 failed: {exc}")
            print(f"[{self.node_id}] Could not reconnect to MQTT broker, continuing retry via MQTT loop")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
        except Exception as exc:
            print(f"[{self.node_id}] Ignoring invalid message payload on {msg.topic}: {exc}")
            return
        
        # 1. Handle Leader Election (Human-in-the-Loop)
        if msg.topic == TOPIC_LEADER:
            if payload['role'] == "LEADER_ACTIVE" and payload['node_id'] != self.node_id:
                self.current_leader = payload['node_id']
                self.role = "FOLLOWER"
                self.leader_timeout = time.time() + 30 # Deadman switch: 30 seconds
                print(f"[{self.node_id}] User connected at {self.current_leader}. Yielding authority. I am now a FOLLOWER.")

        elif msg.topic == TOPIC_CONTROL:
            target = payload.get("target")
            command = payload.get("command")
            if target in (self.node_id, "ALL"):
                if command == "POWER_OFF" and self.power_on:
                    self.power_on = False
                    self.status = "OFFLINE"
                    print(f"[{self.node_id}] Power command received: OFF")
                    self.gossip("Powered off by dashboard", 0.0)
                elif command == "POWER_ON" and not self.power_on:
                    self.power_on = True
                    self.status = "OPTIMAL"
                    print(f"[{self.node_id}] Power command received: ON")
                    self.gossip("Powered on by dashboard", 20.0)
                
        # 2. Handle Swarm Gossip
        elif msg.topic == TOPIC_GOSSIP and payload['sender'] != self.node_id:
            # If we are the leader, we orchestrate. If peer, we just log.
            if self.role == "LEADER_ACTIVE" and payload['status'] == "STRUGGLING":
                print(f"\n*** [ORCHESTRATION] {self.node_id} (Leader) sees {payload['sender']} is struggling. Adjusting system pressure! ***\n")
            elif self.role == "FOLLOWER":
                print(f"[{self.node_id}] Overheard {payload['sender']} state: {payload['status']}")

            if self.role == "LEADER_ACTIVE":
                self._send_dashboard_report(payload)

    def simulate_hardware_loop(self):
        """Simulates the InfluxDB data stream and runs our Statistical AI"""
        while True:
            try:
                if not self.power_on:
                    time.sleep(1)
                    continue

                # Simulate a normal torque value with some noise
                current_torque = np.random.normal(loc=20.0, scale=2.0)

                # Hackathon Demo Trick: Make slot 2 randomly fail so we can see the swarm react
                if self.slot == 2 and np.random.rand() > 0.85:
                    current_torque = 85.0 # Massive torque spike!

                self.analyze_physics(current_torque)
                self.gossip("Telemetry heartbeat", current_torque)

                if self.role == "LEADER_ACTIVE" and time.time() >= self.next_leader_announce_at:
                    self._announce_leader()
                    self.next_leader_announce_at = time.time() + 15

                self.check_leader_timeout()
                time.sleep(1)
            except Exception as exc:
                print(f"[{self.node_id}] Loop error (continuing): {exc}")
                time.sleep(1)

    def analyze_physics(self, current_torque):
        """Agentic Edge Intelligence (Statistical Anomaly Detection)"""
        self.torque_memory.append(current_torque)
        if len(self.torque_memory) > 30:
            self.torque_memory.pop(0)

        # Need at least 10 data points to establish a baseline
        if len(self.torque_memory) >= 10:
            mean = np.mean(self.torque_memory)
            std = max(np.std(self.torque_memory), 1.0) # Prevent division by zero
            z_score = (current_torque - mean) / std

            # If anomaly detected
            if z_score > 3.0:
                if self.status != "STRUGGLING":
                    self.status = "STRUGGLING"
                    self.gossip("Mechanical Blockage Detected!", current_torque)
            else:
                if self.status != "OPTIMAL":
                    self.status = "OPTIMAL"
                    self.gossip("Back to Normal.", current_torque)

    def _format_uptime(self):
        elapsed = int(time.time() - self.started_at)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _build_telemetry_features(self, torque):
        # Derive stable, demo-friendly metrics from current simulated torque.
        torque_norm = max(0.0, min((float(torque) - 15.0) / 70.0, 1.0))
        cpu_percent = 18.0 + (torque_norm * 72.0) + np.random.normal(0, 2.0)
        mem_percent = 28.0 + (torque_norm * 50.0) + np.random.normal(0, 1.8)
        disk_percent = 42.0 + ((self.slot if self.slot > 0 else 1) % 5) * 3.0

        try:
            load_1m = os.getloadavg()[0]
        except Exception:
            load_1m = max(0.1, cpu_percent / 100.0)

        return {
            "cpu_percent": max(0.0, min(cpu_percent, 100.0)),
            "mem_percent": max(0.0, min(mem_percent, 100.0)),
            "disk_percent": max(0.0, min(disk_percent, 100.0)),
            "cpu_temp": float(torque),
            "load_1m": float(load_1m),
            "uptime": self._format_uptime(),
        }

    def gossip(self, detail, torque):
        """Broadcast state to the swarm"""
        telemetry = self._build_telemetry_features(torque)
        msg = {
            "timestamp": datetime.now().isoformat(),
            "sender": self.node_id,
            "zone": self.zone,
            "status": self.status,
            "detail": detail,
            "torque": float(torque),
            **telemetry,
        }
        print(f"[{self.node_id}] Gossiping my status: {self.status} - {detail}")
        result = self.client.publish(TOPIC_GOSSIP, json.dumps(msg))
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"[{self.node_id}] Publish failed with rc={result.rc}")

        if self.role == "LEADER_ACTIVE":
            self._send_dashboard_report(msg)

    def check_leader_timeout(self):
        """Deadman switch: Revert to peer if human unplugs"""
        now = time.time()

        if self.role == "FOLLOWER" and self.current_leader and now > self.leader_timeout:
            print(f"[{self.node_id}] Leader {self.current_leader} timed out. Waiting for next leader announce.")
            self.current_leader = None
            self.next_leader_claim_at = now + random.uniform(2, 5)

        # Compose-scaled containers do not expose replica slot via HOSTNAME.
        # If no leader has been observed, one follower self-elects after jitter.
        if self.role == "FOLLOWER" and self.current_leader is None and now >= self.next_leader_claim_at:
            self.role = "LEADER_ACTIVE"
            self.current_leader = self.node_id
            self._announce_leader()
            self.next_leader_announce_at = now + 15

if __name__ == "__main__":
    # Small delay to ensure MQTT broker boots first in Docker
    time.sleep(2) 
    agent = BelimoAgent()
    agent.simulate_hardware_loop()