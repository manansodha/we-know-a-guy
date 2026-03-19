FROM python:3.11-slim

WORKDIR /app

COPY pi_dashboard_server.py ./
RUN pip install --no-cache-dir flask paho-mqtt

ENV MQTT_BROKER_HOST=mosquitto
ENV MQTT_BROKER_PORT=1883

EXPOSE 5555

CMD ["python", "-u", "pi_dashboard_server.py"]