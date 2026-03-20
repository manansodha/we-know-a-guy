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

# Track anomalies in the sensor
active_anomalies = {s: 0 for s in sensors} 

while True:
    for sensor_id in sensors:
        for field, meta in stats.items():
            value = random.gauss(meta['mean'], meta['std'])
           
           # Simulate anomaly
            if active_anomalies[sensor_id] == 0 and random.random() > 0.99:
                active_anomalies[sensor_id] = 10 
                print(f"{sensor_id}")

            if active_anomalies[sensor_id] > 0:
                value *= 3.0 
                active_anomalies[sensor_id] -= 1

            payload = {"node_id": sensor_id, "_field": field, "_value": value}
            client.publish(f"belimo/telemetry/{sensor_id}", json.dumps(payload))
    
    time.sleep(1)