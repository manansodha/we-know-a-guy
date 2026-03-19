#!/usr/bin/env python3
"""
Standalone startup script for local development
Runs the entire swarm without Docker
"""

import os
import sys
import subprocess
import time
from pathlib import Path


def check_mosquitto():
    """Check if mosquitto is installed"""
    try:
        result = subprocess.run(["mosquitto", "--version"], capture_output=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def start_broker(config_path):
    """Start Mosquitto broker"""
    print("Starting MQTT broker...")
    try:
        proc = subprocess.Popen(
            ["mosquitto", "-c", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(1)
        if proc.poll() is None:
            print("✓ MQTT broker started")
            return proc
        else:
            print("✗ MQTT broker failed to start")
            return None
    except FileNotFoundError:
        print("✗ Mosquitto not found. Install with: `choco install mosquitto` (Windows) or `brew install mosquitto` (macOS)")
        return None


def start_agent(agent_id, num_agents=3, duration=120):
    """Start a single agent"""
    print(f"Starting {agent_id}...")
    
    env = os.environ.copy()
    env.update({
        "AGENT_ID": agent_id,
        "MQTT_BROKER": "localhost",
        "MQTT_PORT": "1883",
        "NUM_AGENTS": str(num_agents),
        "SIMULATION_DURATION": str(duration),
    })
    
    code = f"""
import os
from agent import SwarmAgent

agent_id = os.getenv("AGENT_ID", "pi-node-0")
broker_host = os.getenv("MQTT_BROKER", "localhost")
broker_port = int(os.getenv("MQTT_PORT", 1883))
num_agents = int(os.getenv("NUM_AGENTS", 3))
duration = int(os.getenv("SIMULATION_DURATION", 120))

node_ids = [f"pi-node-{{i}}" for i in range(num_agents)]
agent = SwarmAgent(agent_id, broker_host, broker_port, node_ids)
agent.connect()
agent.run_simulation(duration=duration, gossip_interval=3.0)
"""
    
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    
    return proc


def main():
    """Main startup routine"""
    print("\n" + "="*80)
    print("Belimo Hackathon - Local Development Startup")
    print("="*80 + "\n")

    # Configuration
    NUM_AGENTS = 3
    SIMULATION_DURATION = 120
    
    # Check for agent.py
    agent_path = Path(__file__).parent / "agent.py"
    if not agent_path.exists():
        print(f"✗ agent.py not found at {agent_path}")
        sys.exit(1)

    # Check for mosquitto
    if not check_mosquitto():
        print("⚠ Mosquitto not installed. Starting broker would fail.")
        print("  Install with:")
        print("    Windows: choco install mosquitto")
        print("    macOS: brew install mosquitto")
        print("    Linux: apt-get install mosquitto")
        sys.exit(1)

    # Start broker
    config_path = Path(__file__).parent / "mosquitto.conf"
    broker_proc = start_broker(config_path)
    if not broker_proc:
        sys.exit(1)

    time.sleep(2)

    # Start agents
    agent_procs = []
    agent_ids = [f"pi-node-{i}" for i in range(NUM_AGENTS)]
    
    for agent_id in agent_ids:
        proc = start_agent(agent_id, NUM_AGENTS, SIMULATION_DURATION)
        agent_procs.append(proc)
        time.sleep(0.5)

    print(f"\n✓ Swarm started with {NUM_AGENTS} agents")
    print(f"  Running for {SIMULATION_DURATION} seconds")
    print("  Press Ctrl+C to stop\n")

    # Monitor processes
    try:
        while any(p.poll() is None for p in agent_procs) or broker_proc.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        for proc in agent_procs:
            if proc.poll() is None:
                proc.terminate()
        if broker_proc.poll() is None:
            broker_proc.terminate()
        
        # Wait for graceful shutdown
        for proc in agent_procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        
        try:
            broker_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            broker_proc.kill()

        print("✓ All processes terminated")


if __name__ == "__main__":
    main()
