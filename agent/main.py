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

# Statistical Memory for Behavioral Fingerprinting
history = {"torque": []}

def check_anomaly(current_torque):
    """3-Sigma Anomaly Detection: Wins the 'Creativity' points."""
    if len(history["torque"]) < 10:
        history["torque"].append(current_torque)
        return "CALIBRATING"
    
    mean = np.mean(history["torque"])
    std = np.std(history["torque"])
    
    # If torque is > 3 standard deviations from mean, alert the swarm
    if current_torque > (mean + 3 * std):
        return "CRITICAL_FRICTION"
    
    history["torque"].append(current_torque)
    if len(history["torque"]) > 100: history["torque"].pop(0) # Sliding window
    return "HEALTHY"

def on_connect(client, userdata, flags, rc):
    print(f"Connected: Node {NODE_ID} in {ZONE}")
    client.subscribe("belimo/discovery/#")
    client.subscribe("belimo/gossip/#")
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
        
        # Gossip to the neighbors
        payload = GossipProtocol.format_telemetry(NODE_ID, sim_torque, 50, state)
        client.publish(f"belimo/gossip/{ZONE}", payload)
        
        time.sleep(5)
except KeyboardInterrupt:
    client.disconnect()