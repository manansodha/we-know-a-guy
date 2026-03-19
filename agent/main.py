import time
import json
import random
import os
import threading
import yaml
import paho.mqtt.client as mqtt
import numpy as np
from protocol import GossipProtocol

# Load Config
with open("config.yaml", 'r') as f:
    config = yaml.safe_load(f)

NODE_ID = os.getenv('NODE_ID', config['node_id'])
ZONE = os.getenv('ZONE', config['zone'])
# NODE_ID = config['node_id']
# ZONE = config['zone']
BROKER = config.get('broker', 'localhost')

# Statistical Memory for Behavioral Fingerprinting
history = {"torque": []}

WINDOW_SIZE = 5
history = {"torque": []}


# --- Escalation Constants ---
STAGNATION_THRESHOLD = 2.0  # Degrees/Percent of allowable error
ZONE_HELP_DELAY = 10        # Seconds of stagnation before asking neighbors
GLOBAL_ALERT_DELAY = 30     # Seconds of stagnation before alerting user

# --- New State Trackers ---
stagnation_start_time = None
zone_help_requested = False
global_alert_sent = False
history["position_delta"] = []


def check_anomaly(current_torque):
    """3-sigma anomaly check against the previous 5 torque readings."""
    if len(history["torque"]) < WINDOW_SIZE:
        history["torque"].append(current_torque)
        return "CALIBRATING"

    # Baseline from last 5 points only (before current sample)
    window = history["torque"][-WINDOW_SIZE:]
    mean = np.mean(window)
    std = np.std(window)

    is_anomaly = abs(current_torque - mean) > (3 * std)

    # Slide window forward with current sample, keeping only 5 points
    history["torque"].append(current_torque)
    history["torque"] = history["torque"][-WINDOW_SIZE:]

    return "ANOMALY" if is_anomaly else "HEALTHY"

def on_connect(client, userdata, flags, rc):
    print(f"Connected: Node {NODE_ID} in {ZONE}")
    client.subscribe("belimo/discovery/#")
    client.subscribe("belimo/gossip/#")
    client.subscribe(CONTROL_TOPIC)
    # Announce presence
    client.publish("belimo/discovery/announcements", 
                   GossipProtocol.format_announcement(NODE_ID, ZONE, ["valve_1"]))

def on_message(client, userdata, msg):
    data = json.loads(msg.payload)
    if data['node_id'] != NODE_ID:
        print(f"[*] Swarm Intel from {data['node_id']}: {data['type']}")

# Setup MQTT
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

try:
    client.connect(BROKER, 1883, 60)
    print("DEBUG: Connection call successful!")
except Exception as e:
    print(f"DEBUG: Failed to connect! Error: {e}")

client.loop_start()
try:
    while True:
        # Simulate Reading Belimo Modbus/BACnet data
        sim_torque = random.uniform(40, 60) 
        state = check_anomaly(sim_torque)
        if IS_LEADER:
            swarm_status[str(NODE_ID)] = state

        # 3. STAGNATION & ESCALATION LOGIC
        if current_delta > STAGNATION_THRESHOLD:
            if stagnation_start_time is None:
                stagnation_start_time = time.time()
            
            elapsed = time.time() - stagnation_start_time

            # --- STEP 1: Local Zone Help (Request Peer Compensation) ---
            if elapsed >= ZONE_HELP_DELAY and not zone_help_requested:
                print(f"[ZONE COMMAND] {NODE_ID}: Valve stuck! Requesting help in {ZONE}.")
                help_payload = json.dumps({
                    "type": "ZONE_ASSIST",
                    "node_id": NODE_ID,
                    "zone": ZONE,
                    "command": "COMPENSATE_TEMP",
                    "detail": f"Valve stuck at {feedback}. Neighbors please increase output."
                })
                client.publish(f"belimo/control/{ZONE}", help_payload)
                zone_help_requested = True

            # --- STEP 2: Global Warning (User Alert) ---
            if elapsed >= GLOBAL_ALERT_DELAY and not global_alert_sent:
                print(f"[!!! GLOBAL ALERT !!!] {NODE_ID}: Hardware failure confirmed.")
                client.publish(
                    "belimo/alerts/system",
                    json.dumps({
                        "type": "HARDWARE_STAGNATION",
                        "node_id": NODE_ID,
                        "severity": "CRITICAL",
                        "delta": current_delta,
                        "timestamp": time.time()
                    })
                )
                global_alert_sent = True
                state = "CRITICAL_STUCK" # Override state for gossip
        else:
            # Reset trackers if the valve reaches the target
            stagnation_start_time = None
            zone_help_requested = False
            global_alert_sent = False

        # 4. Gossip & Leader Logic
        payload = GossipProtocol.format_telemetry(NODE_ID, sim_torque, 50, state)
        client.publish(f"belimo/gossip/{ZONE}", payload)
        
        time.sleep(5)
except KeyboardInterrupt:
    client.disconnect()