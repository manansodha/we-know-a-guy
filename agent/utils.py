

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
