FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir paho-mqtt==1.6.1 numpy

# Copy the agent code
COPY agent.py .

# Run the agent
ENTRYPOINT ["sh", "-c", "echo Starting Belimo Agent: $NODE_ID; exec python -u agent.py"]

# Health check
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import paho.mqtt.client as mqtt; c=mqtt.Client(); c.connect('${BROKER:-localhost}', 1883, 5); c.disconnect()" || exit 1