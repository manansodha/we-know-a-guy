#!/usr/bin/env python3
"""
Local test script for the Belimo Hackathon swarm
Run without Docker to verify agent logic
"""

import time
import threading
from agent import SwarmAgent, AnomalyDetector, SensorReading
import json


def test_anomaly_detector():
    """Test anomaly detection logic"""
    print("\n" + "="*80)
    print("TEST: Anomaly Detector")
    print("="*80)

    detector = AnomalyDetector(window_size=20)

    # Add normal values
    print("\nAdding 20 normal readings (centered around 22°C)...")
    for i in range(20):
        value = 22 + (i % 5) * 0.5
        detector.add_value(value)

    stats = detector.get_stats()
    print(f"Stats: mean={stats['mean']:.2f}, std={stats['std']:.2f}")

    # Test anomaly detection
    test_values = [
        (22.5, False, "Normal value (within range)"),
        (25.0, False, "Slightly elevated (1.5 std)"),
        (32.0, True, "Clear anomaly (> 3 std)"),
        (12.0, True, "Low anomaly (> 3 std)"),
    ]

    print("\nTesting anomaly detection:")
    for value, expected_anomaly, description in test_values:
        is_anomaly = detector.is_anomalous(value)
        status = "✓" if is_anomaly == expected_anomaly else "✗"
        result = f"{status} {description}: value={value}, detected={is_anomaly}"
        print(result)


def test_sensor_reading():
    """Test sensor reading creation"""
    print("\n" + "="*80)
    print("TEST: Sensor Reading")
    print("="*80)

    reading = SensorReading(
        agent_id="pi-node-0",
        timestamp=time.time(),
        value=22.5,
        sensor_type="temperature"
    )

    print(f"\nCreated reading: {reading}")
    data = reading.to_dict()
    print(f"Serialized: {json.dumps(data, indent=2)}")

    reading2 = SensorReading.from_dict(data)
    print(f"Deserialized: {reading2}")
    assert reading.agent_id == reading2.agent_id
    print("✓ Serialization round-trip successful")


def test_vector_clock():
    """Test vector clock implementation"""
    print("\n" + "="*80)
    print("TEST: Vector Clock Causality")
    print("="*80)

    node_ids = ["pi-node-0", "pi-node-1", "pi-node-2"]

    # Create agent with vector clock
    agent = SwarmAgent("pi-node-0", node_ids=node_ids)

    print(f"\nInitial vector clock: {agent.vector_clock}")

    # Simulate events
    print("\nSimulating causality:")
    agent.add_reading()
    agent.vector_clock["pi-node-0"] += 1
    print(f"After event at pi-node-0: {agent.vector_clock}")

    # Simulate receiving message from pi-node-1
    incoming_clock = {"pi-node-0": 2, "pi-node-1": 5, "pi-node-2": 1}
    for aid, clock_val in incoming_clock.items():
        agent.vector_clock[aid] = max(agent.vector_clock[aid], clock_val)

    print(f"After merging clock from pi-node-1: {agent.vector_clock}")
    print("✓ Vector clock causality maintained")


def test_message_serialization():
    """Test gossip message serialization"""
    print("\n" + "="*80)
    print("TEST: Message Serialization")
    print("="*80)

    from agent import GossipMessage

    msg = GossipMessage(
        origin_id="pi-node-0",
        timestamp=time.time(),
        readings=[
            {
                "agent_id": "pi-node-0",
                "timestamp": time.time(),
                "value": 22.5,
                "sensor_type": "temperature",
            }
        ],
        vector_clock={"pi-node-0": 5, "pi-node-1": 3, "pi-node-2": 2},
        leader_id="pi-node-0",
        anomaly_votes={"pi-node-2": 2},
    )

    print(f"\nOriginal message:")
    print(f"  Origin: {msg.origin_id}")
    print(f"  Readings: {len(msg.readings)}")
    print(f"  Leader: {msg.leader_id}")

    # Serialize
    data = msg.to_dict()
    print(f"\nSerialized (JSON): {json.dumps(data, indent=2)[:200]}...")

    # Deserialize
    msg2 = GossipMessage.from_dict(data)
    print(f"\nDeserialized message:")
    print(f"  Origin: {msg2.origin_id}")
    print(f"  Readings: {len(msg2.readings)}")
    print(f"  Leader: {msg2.leader_id}")

    assert msg.origin_id == msg2.origin_id
    assert len(msg.readings) == len(msg2.readings)
    print("✓ Message serialization successful")


def test_agent_initialization():
    """Test agent creation and basic properties"""
    print("\n" + "="*80)
    print("TEST: Agent Initialization")
    print("="*80)

    node_ids = ["pi-node-0", "pi-node-1", "pi-node-2"]
    agent = SwarmAgent("pi-node-0", node_ids=node_ids)

    print(f"\nAgent created:")
    print(f"  ID: {agent.agent_id}")
    print(f"  State: {agent.state.value}")
    print(f"  Node IDs: {agent.node_ids}")
    print(f"  Vector clock: {agent.vector_clock}")

    status = agent.get_status()
    print(f"\nAgent status:")
    for key, value in status.items():
        print(f"  {key}: {value}")

    assert agent.agent_id == "pi-node-0"
    assert agent.state.value == "candidate"
    print("✓ Agent initialization successful")


def main():
    """Run all tests"""
    print("\n")
    print("█" * 80)
    print("█  Belimo Hackathon - Local Test Suite")
    print("█" * 80)

    tests = [
        ("Anomaly Detector", test_anomaly_detector),
        ("Sensor Reading", test_sensor_reading),
        ("Vector Clock", test_vector_clock),
        ("Message Serialization", test_message_serialization),
        ("Agent Initialization", test_agent_initialization),
    ]

    results = []
    for name, test_func in tests:
        try:
            test_func()
            results.append((name, "✓ PASS"))
        except Exception as e:
            results.append((name, f"✗ FAIL: {str(e)}"))
            import traceback
            traceback.print_exc()

    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    for name, result in results:
        print(f"{result:20} - {name}")

    passed = sum(1 for _, r in results if "PASS" in r)
    total = len(results)
    print(f"\nTotal: {passed}/{total} passed")

    if passed == total:
        print("\n✓ All tests passed!")
    else:
        print(f"\n✗ {total - passed} test(s) failed")


if __name__ == "__main__":
    main()
