FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# This allows the agent to find the shared/protocol.py
ENV PYTHONPATH="/app:/app/shared" 
CMD ["python", "-u", "main.py"]

# .devcontainer/devcontainer.json