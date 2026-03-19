# Belimo Hackathon: Multi-Agent Swarm Intelligence

A distributed multi-agent swarm system with statistical anomaly detection, gossip protocol-based information dissemination, and dynamic human-in-the-loop leader election.

## Features

### 🤖 Multi-Agent Swarm Architecture
- **Distributed agents** running on simulated nodes with independent sensors
- **Vector clock** implementation for causal ordering of events
- **Asynchronous communication** via MQTT broker

### 📡 Gossip Protocol
- **Epidemic dissemination** of sensor readings across the swarm
- **Selective peer-to-peer gossip** reducing message overhead
- **Causally-ordered delivery** via vector clocks
- **State reconciliation** for eventual consistency

### 📊 Statistical Anomaly Detection
- **Z-score based detection** for identifying outlier readings
- **Rolling window statistics** (mean, std, min, max)
- **Distributed consensus** on anomalous agents
- **Configurable detection thresholds**

### 🗳️ Dynamic Leader Election
- **Multi-round voting protocol** for leader selection
- **Human-in-the-loop override** for manual leadership decisions
- **Automatic consensus** when majority is reached
- **Periodic re-elections** to handle node failures

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│                    MQTT Broker                      │
│              (Mosquitto - Central Hub)              │
└──────────────┬──────────────┬──────────────┬────────┘
               │              │              │
        ┌──────▼─┐     ┌──────▼─┐     ┌─────▼──┐
        │Pi-Node-0│     │Pi-Node-1│     │Pi-Node-2│
        │ Agent   │     │ Agent   │     │ Agent   │
        │ ├─ Sensor──   │ ├─ Sensor──   │ ├─ Sensor──
        │ ├─ Gossip──   │ ├─ Gossip──   │ ├─ Gossip──
        │ ├─ Anomaly──  │ ├─ Anomaly──  │ ├─ Anomaly──
        │ └─ Election──  │ └─ Election──  │ └─ Election──
        └────────┘     └────────┘     └────────┘
```

## Project Structure

```
belimo-hackathon/
├── docker-compose.yml       # Orchestrates broker + 3 Pi nodes
├── mosquitto.conf           # MQTT configuration (password-less)
├── Dockerfile               # Container blueprint for agents
├── agent.py                 # Core multi-agent logic
├── requirements.txt         # Python dependencies
└── README.md               # This file
```

## Quick Start

### Prerequisites
- Docker & Docker Compose
- OR Python 3.10+ with pip

### Option 1: Docker (Recommended)

```bash
# Build and start broker + N agent nodes (example: 5)
docker compose up -d --build --scale pi-node=5

# Optional: choose which replica slot is leader (default is slot 1)
LEADER_SLOT=1 docker compose up -d --build --scale pi-node=5

# In another terminal, view logs
docker compose logs -f

# Stop the swarm
docker compose down
```

Leader behavior:
- Only one node is leader (`LEADER_SLOT`).
- All nodes gossip local state to MQTT.
- The leader forwards node reports to the dashboard endpoint (`DASHBOARD_URL`, default `http://host.docker.internal:5555/api/report`).

### Option 2: Local Python

```bash
# Install dependencies
pip install -r requirements.txt

# Terminal 1: Start MQTT broker
mosquitto -c mosquitto.conf

# Terminal 2: Start agents
python agent.py
```

## Core Components

### SwarmAgent Class
Main agent implementation with:
- **MQTT connectivity** for message passing
- **Gossip protocol** for information dissemination
- **Anomaly detection** for outage identification
- **Leader election** for swarm coordination

#### Key Methods
```python
agent.gossip_round()          # Broadcast to random peer
agent.start_election_round()  # Initiate leader election
agent.add_reading()           # Simulate sensor data
agent.report_anomaly()        # Report suspected anomalies
agent.get_status()            # Get current state
```

### AnomalyDetector Class
Statistical analysis for detecting outliers:
- Z-score based detection (default threshold: 3.0)
- Rolling window statistics
- Configurable window size (default: 100)

### Message Types

#### Gossip Message
```json
{
  "origin_id": "pi-node-0",
  "timestamp": 1626789012.34,
  "readings": [
    {"agent_id": "pi-node-0", "value": 22.5, "timestamp": ...},
    {"agent_id": "pi-node-1", "value": 21.8, "timestamp": ...}
  ],
  "vector_clock": {"pi-node-0": 15, "pi-node-1": 12, "pi-node-2": 10},
  "leader_id": "pi-node-0",
  "anomaly_votes": {"pi-node-2": 2}
}
```

#### Election Message
```json
{
  "round": 1,
  "candidate_id": "pi-node-0",
  "votes": 1,
  "timestamp": 1626789012.34
}
```

## MQTT Topics

| Topic | Purpose |
|-------|---------|
| `swarm/gossip/{node_id}` | Gossip protocol messages to specific node |
| `swarm/election` | Leader election messages |
| `swarm/anomaly` | Anomaly detection consensus |
| `swarm/human-input` | Human operator commands |

## Configuration

### Environment Variables (Docker)
```bash
AGENT_ID=pi-node-0          # Agent identifier
MQTT_BROKER=mosquitto       # Broker hostname
MQTT_PORT=1883              # Broker port
NUM_AGENTS=3                # Number of agents in swarm
SIMULATION_DURATION=120     # Runtime in seconds
```

### Gossip Parameters
- **Gossip interval**: 3 seconds
- **Election round interval**: 20 seconds
- **Anomaly detection window**: 100 readings
- **Z-score threshold**: 3.0 (3 standard deviations)

## Monitoring & Debugging

### View Agent Status
```python
status = agent.get_status()
print(status)
# Output:
# {
#   'agent_id': 'pi-node-0',
#   'state': 'leader',
#   'leader_id': 'pi-node-0',
#   'gossip_round': 42,
#   'election_round': 2,
#   'readings_count': 128,
#   'suspected_anomalies': ['pi-node-2'],
#   'anomaly_detector_stats': {...}
# }
```

### Monitor MQTT Traffic
```bash
# Subscribe to all swarm topics
mosquitto_sub -h localhost -t 'swarm/#' -v

# Monitor gossip only
mosquitto_sub -h localhost -t 'swarm/gossip/#' -v
```

### Docker Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f pi-node-0

# Follow with timestamps
docker-compose logs -f --timestamps
```

## Protocol Details

### Gossip Protocol
1. Each round, agent selects random peer from swarm
2. Sends recent sensor readings with vector clock
3. Recipient integrates readings into local state
4. Vector clocks ensure causal ordering
5. Provides eventual consistency across swarm

### Leader Election
1. Agent initiates election by voting for itself
2. Broadcasts its candidacy to all agents
3. Agents track votes via distributed messaging
4. When candidate receives > 50% votes, elected leader
5. Human operator can override via `swarm/human-input` topic

### Anomaly Detection
1. Each agent maintains rolling window of sensor values
2. New readings tested against z-score threshold
3. Anomalous readings reported to swarm
4. Distributed voting accumulates anomaly evidence
5. Consensus builds as more evidence arrives

## Advanced Usage

### Custom Sensor Data
```python
agent = SwarmAgent("pi-node-0", "localhost", 1883, ["pi-node-0", "pi-node-1"])

# Add custom reading
reading = SensorReading(
    agent_id="pi-node-0",
    timestamp=time.time(),
    value=25.3,
    sensor_type="humidity"
)
agent.add_reading(reading)
```

### Human-in-the-Loop Commands
```bash
# Force specific agent as leader
mosquitto_pub -h localhost -t 'swarm/human-input' -m '{
  "command": "elect_leader",
  "target": "pi-node-1"
}'

# Mark agent as anomalous
mosquitto_pub -h localhost -t 'swarm/human-input' -m '{
  "command": "mark_anomaly",
  "target": "pi-node-2"
}'
```

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Leader election latency | ~20 seconds |
| Information dissemination time | O(log n) gossip rounds |
| Anomaly detection latency | ~3-5 seconds |
| Message overhead | ~10KB per gossip round |
| Swarm scalability | Tested up to 10 agents |

## Failure Modes & Recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Agent crash | Missing in gossip | Re-election triggered |
| Network partition | Message loss | Eventual re-convergence |
| Anomalous sensor | Z-score spike | Consensus exclusion |
| Leader failure | No updates | Election round starts |

## References

- **Gossip Protocols**: Epidemic algorithms for distributed information systems
- **Vector Clocks**: Causality tracking in distributed systems (Lamport, Fidge)
- **Anomaly Detection**: Statistical outlier detection via z-score methods
- **Leader Election**: Bully algorithm variant for distributed consensus

## Future Enhancements

- [ ] Byzantine fault tolerance (consensus on anomalies)
- [ ] Machine learning for anomaly detection (isolation forest)
- [ ] Adaptive gossip intervals based on network latency
- [ ] Raft consensus for stronger consistency guarantees
- [ ] Web dashboard for real-time monitoring
- [ ] Persistent state storage (RocksDB)
- [ ] Compression for large message payloads

## Troubleshooting

### MQTT Connection Refused
```bash
# Ensure broker is running
docker-compose logs mosquitto

# Check broker health
docker-compose ps
```

### No Messages Appearing
```bash
# Verify MQTT connectivity
mosquitto_pub -h localhost -t 'test' -m 'hello'
mosquitto_sub -h localhost -t 'test'

# Check agent logs
docker-compose logs -f pi-node-0
```

### Leader Election Not Converging
- Increase election timeout (currently 20s)
- Check network connectivity between agents
- Verify MQTT broker is receiving messages

## License

MIT License - Belimo Hackathon 2026
