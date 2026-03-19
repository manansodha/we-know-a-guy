import time
import json
import random
import os
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
IS_LEADER = os.getenv('IS_LEADER', 'false').lower() in {'1', 'true'}
ALERT_MIN_ANOMALIES = int(os.getenv('ALERT_MIN_ANOMALIES', '2'))
swarm_status = {}   # node_id -> latest status
alert_active = False

# Statistical Memory for Behavioral Fingerprinting
history = {"torque": []}

WINDOW_SIZE = 5
history = {"torque": []}

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
    # Announce presence
    client.publish("belimo/discovery/announcements", 
                   GossipProtocol.format_announcement(NODE_ID, ZONE, ["valve_1"]))

def on_message(client, userdata, msg):
    data = json.loads(msg.payload)
    if data.get("node_id") == NODE_ID:
        return

    msg_type = data.get("type", "UNKNOWN")
    if msg_type == "GOSSIP":
        payload = data.get("data", {})
        status = payload.get("status", "UNKNOWN")
        torque = payload.get("torque", "n/a")
        print(f"[*] Swarm Intel from {data.get('node_id')}: status={status}, torque={torque}")
        if IS_LEADER and data.get("node_id"):
            swarm_status[str(data.get("node_id"))] = status
    else:
        print(f"[*] Swarm Intel from {data.get('node_id')}: {msg_type}")

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
        sim_torque = random.uniform(40, 100) 

        state = check_anomaly(sim_torque)
        if IS_LEADER:
            swarm_status[str(NODE_ID)] = state

        payload = GossipProtocol.format_telemetry(NODE_ID, sim_torque, 50, state)
        client.publish(f"belimo/gossip/{ZONE}", payload)
        
        if IS_LEADER:
            anomaly_nodes = [nid for nid, st in swarm_status.items() if st == "ANOMALY"]
            system_alert = len(anomaly_nodes) >= ALERT_MIN_ANOMALIES
            if system_alert != alert_active:
                alert_active = system_alert
                client.publish(
                    "belimo/alerts/system",
                    json.dumps({
                        "type": "SYSTEM_ALERT",
                        "leader_id": NODE_ID,
                        "event": "ALERT" if system_alert else "CLEAR",
                        "anomaly_count": len(anomaly_nodes),
                        "anomaly_nodes": anomaly_nodes,
                        "threshold": ALERT_MIN_ANOMALIES,
                        "timestamp": time.time()
                    })
                )
        
        
        time.sleep(5)
except KeyboardInterrupt:
    client.disconnect()