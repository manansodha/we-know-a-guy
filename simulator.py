import pandas as pd
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import random

df = pd.read_csv('air-sensor-data-annotated.csv', comment='#')
stats = df.groupby('_field')['_value'].agg(['mean', 'std']).to_dict('index')
sensors = df['sensor_id'].unique().tolist()

client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.connect("mosquitto", 1883)

# Track if a sensor is currently in a "Failure Burst"
active_anomalies = {s: 0 for s in sensors} 

while True:
    for sensor_id in sensors:
        for field, meta in stats.items():
            value = random.gauss(meta['mean'], meta['std'])
            
            # Start a new anomaly burst (1% chance)
            if active_anomalies[sensor_id] == 0 and random.random() > 0.99:
                active_anomalies[sensor_id] = 10 # Lasts for 10 cycles
                print(f"🔥 STARTING ANOMALY BURST: {sensor_id}")

            # If in a burst, amplify the value significantly
            if active_anomalies[sensor_id] > 0:
                value *= 3.0 # 3x is high enough to bypass any Z-score threshold
                active_anomalies[sensor_id] -= 1

            payload = {"node_id": sensor_id, "_field": field, "_value": value}
            client.publish(f"belimo/telemetry/{sensor_id}", json.dumps(payload))
    
    time.sleep(1)