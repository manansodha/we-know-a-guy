#!/usr/bin/env python3
"""
Raspberry Pi Fleet Monitoring Dashboard
========================================
A Flask-based dashboard for monitoring 10+ Raspberry Pis.

Usage:
  1. pip install flask
  2. python pi_dashboard_server.py
  3. Open http://localhost:5555 in your browser
  4. Run pi_agent.py on each Raspberry Pi (see pi_agent.py)

The dashboard can ingest data from either:
  - MQTT gossip/discovery topics used by agent/main.py
  - POST /api/report JSON metrics (legacy/manual path)

It displays all nodes in a searchable, grouped web UI.
"""

import time
import json
import os
import threading
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
import paho.mqtt.client as mqtt
import paho.mqtt.publish as mqtt_publish

app = Flask(__name__)

# In-memory store: { hostname: { ...metrics, last_seen: timestamp } }
PI_DATA = {}
PI_ALIASES = {}
PI_POWER_STATES = {}
DATA_LOCK = threading.Lock()
STALE_SECONDS = 60  # Mark Pi as offline after 60s without a heartbeat
OFFLINE_PRUNE_MINUTES = int(os.getenv("OFFLINE_PRUNE_MINUTES", "10"))
OFFLINE_PRUNE_SECONDS = max(OFFLINE_PRUNE_MINUTES, 1) * 60
MQTT_CONTROL_TOPIC = "belimo/swarm/control"
MQTT_GOSSIP_TOPIC = "belimo/gossip/#"
MQTT_DISCOVERY_TOPIC = "belimo/discovery/#"
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_LISTENER_CLIENT_ID = os.getenv("DASHBOARD_MQTT_CLIENT_ID", "pi-dashboard-listener")
MQTT_CLIENT = None

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pi Fleet Monitor</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e4e4e7; --muted: #71717a; --green: #22c55e;
    --red: #ef4444; --amber: #f59e0b; --blue: #3b82f6;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: 'SF Mono', 'Fira Code', monospace; background: var(--bg); color: var(--text); padding: 24px; }
  h1 { font-size: 1.5rem; margin-bottom: 8px; }
  .meta { color: var(--muted); font-size: 0.8rem; margin-bottom: 20px; }
  .controls { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
  input[type=text] { background:var(--surface); border:1px solid var(--border); color:var(--text);
    padding:8px 14px; border-radius:6px; font-size:0.85rem; width:260px; }
  .stats { display:flex; gap:16px; margin-bottom:20px; font-size:0.85rem; }
  .stat { background:var(--surface); padding:10px 16px; border-radius:8px; border:1px solid var(--border); }
  .stat b { color:var(--blue); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; position:relative; }
  .card.offline { opacity:0.5; }
  .hostname { font-size:1rem; font-weight:700; margin-bottom:4px; }
  .hostname small { display:block; font-size:0.72rem; font-weight:500; color:var(--muted); margin-top:2px; }
  .ip { color:var(--muted); font-size:0.75rem; }
  .status { position:absolute; top:16px; right:16px; width:10px; height:10px; border-radius:50%; }
  .status.online { background:var(--green); box-shadow:0 0 6px var(--green); }
  .status.offline { background:var(--red); }
  .metrics { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:12px; }
  .metric { font-size:0.78rem; }
  .metric .label { color:var(--muted); }
  .metric .value { font-weight:600; }
  .bar-bg { height:6px; background:var(--border); border-radius:3px; margin-top:3px; }
  .bar-fill { height:100%; border-radius:3px; transition:width 0.3s; }
  .bar-fill.green { background:var(--green); }
  .bar-fill.amber { background:var(--amber); }
  .bar-fill.red { background:var(--red); }
  .group-label { color:var(--muted); font-size:0.75rem; text-transform:uppercase; letter-spacing:1px;
    margin:20px 0 8px; border-bottom:1px solid var(--border); padding-bottom:4px; }
  .empty { color:var(--muted); text-align:center; padding:60px; }
  .swarm-meta { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; font-size: 0.75rem; color: var(--muted); }
  .rename-row { margin-top: 10px; display:flex; gap:8px; }
  .rename-row input { flex:1; background:var(--bg); border:1px solid var(--border); color:var(--text); padding:7px 10px; border-radius:6px; font-size:0.75rem; }
  .rename-row button { background:var(--blue); color:white; border:0; border-radius:6px; padding:7px 10px; cursor:pointer; font-size:0.75rem; }
  .rename-row button:hover { filter:brightness(1.08); }
  .power-row { margin-top: 8px; display:flex; gap:8px; }
  .power-on { background: #22c55e; color: white; border: 0; border-radius: 6px; padding: 7px 10px; cursor: pointer; font-size: 0.75rem; }
  .power-off { background: #ef4444; color: white; border: 0; border-radius: 6px; padding: 7px 10px; cursor: pointer; font-size: 0.75rem; }
  .gauge-container { display:flex; align-items:center; gap:20px; margin-bottom:20px; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:20px; }
  svg.gauge { width:140px; height:140px; }
  .gauge-text { flex:1; }
  .gauge-text h2 { font-size:1rem; margin-bottom:8px; }
  .gauge-text .status-label { font-size:0.9rem; font-weight:600; }
  .gauge-text .status-desc { font-size:0.8rem; color:var(--muted); margin-top:6px; }
</style>
</head>
<body>
<h1>🍓 Pi Fleet Monitor</h1>
<p class="meta" id="metaText">Live updates every 1s &middot; {{ total }} Pi(s) registered</p>

<div class="controls">
  <input type="text" id="search" placeholder="Search hostname or IP..." oninput="filterCards()">
</div>

<div class="gauge-container">
  <svg class="gauge" viewBox="0 0 140 140" id="healthGauge">
    <!-- Background circle -->
    <circle cx="70" cy="70" r="65" fill="none" stroke="#2a2d3a" stroke-width="8"/>
    <!-- Gauge arc (will be filled dynamically) -->
    <circle cx="70" cy="70" r="65" fill="none" stroke-width="8" stroke-dasharray="0,408" stroke-dashoffset="0" id="gaugeArc" style="transition: all 0.8s ease-in-out"/>
    <!-- Center text -->
    <text x="70" y="65" text-anchor="middle" font-size="24" font-weight="bold" id="gaugePercent" fill="#e4e4e7">100%</text>
    <text x="70" y="85" text-anchor="middle" font-size="10" id="gaugeLabel" fill="#71717a">HEALTHY</text>
  </svg>
  <div class="gauge-text">
    <h2>System Health</h2>
    <div class="status-label" id="healthStatus">All Systems Online</div>
    <div class="status-desc" id="healthDesc">{{ total }} systems reporting</div>
  </div>
</div>

<div class="stats">
  <div class="stat">Online: <b id="onlineStat">{{ online }}</b></div>
  <div class="stat">Offline: <b id="offlineStat">{{ offline }}</b></div>
  <div class="stat">Avg Torque: <b id="avgTorqueStat">{{ avg_torque }}</b></div>
  <div class="stat">Avg Position: <b id="avgPositionStat">{{ avg_position }}</b></div>
</div>

<div id="fleetContent">
{% if pis|length == 0 %}
<div class="empty">No Pis reporting yet. Run <code>pi_agent.py</code> on your Raspberry Pis.</div>
{% else %}
{% for group, items in groups.items() %}
<div class="group-label">{{ group }} ({{ items|length }})</div>
<div class="grid">
{% for pi in items %}
<div class="card pi-card {{ 'offline' if not pi.online else '' }}" data-search="{{ pi.display_name|lower }} {{ pi.hostname|lower }} {{ pi.ip|lower }}">
  <div class="status {{ 'online' if pi.online else 'offline' }}"></div>
  <div class="hostname">
    {{ pi.display_name }}
    <small>Host: {{ pi.hostname }}</small>
  </div>
  <div class="ip">{{ pi.ip }} &middot; {{ pi.group }} &middot; seen {{ pi.last_seen_ago }}</div>
  <div class="metrics">
    <div class="metric">
      <div class="label">Torque</div>
      <div class="value">{{ pi.torque }}%</div>
      <div class="bar-bg"><div class="bar-fill {{ pi.torque_color }}" style="width:{{ pi.torque }}%"></div></div>
    </div>
    <div class="metric">
      <div class="label">Position</div>
      <div class="value">{{ pi.position }}%</div>
      <div class="bar-bg"><div class="bar-fill {{ pi.position_color }}" style="width:{{ pi.position }}%"></div></div>
    </div>
    <div class="metric">
      <div class="label">Disk</div>
      <div class="value">{{ pi.disk_percent }}%</div>
      <div class="bar-bg"><div class="bar-fill {{ pi.disk_color }}" style="width:{{ pi.disk_percent }}%"></div></div>
    </div>
    <div class="metric">
      <div class="label">Temp</div>
      <div class="value">{{ pi.cpu_temp }}°C</div>
      <div class="bar-bg"><div class="bar-fill {{ pi.temp_color }}" style="width:{{ [pi.cpu_temp, 100]|min }}%"></div></div>
    </div>

    <div class="metric">
      <div class="label">Load (1m)</div>
      <div class="value">{{ pi.load_1m }}</div>
    </div>
  </div>
  <div class="swarm-meta">
    <div>Status: <b>{{ pi.status }}</b></div>
    <div>Leader: {{ pi.leader_id }}</div>
    <div>Detail: {{ pi.detail }}</div>
    <div>Power: <b>{{ pi.power_state }}</b></div>
  </div>
  <div class="rename-row">
    <input id="rename-{{ pi.hostname }}" type="text" value="{{ pi.display_name }}" placeholder="Custom name">
    <button onclick="renamePi('{{ pi.hostname }}')">Save Name</button>
  </div>
  <div class="power-row">
    {% if pi.power_state == "ON" %}
    <button class="power-off" onclick="setPower('{{ pi.hostname }}', false)">Turn Off</button>
    {% else %}
    <button class="power-on" onclick="setPower('{{ pi.hostname }}', true)">Turn On</button>
    {% endif %}
  </div>
</div>
{% endfor %}
</div>
{% endfor %}
{% endif %}
</div>

<script>
function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function colorClass(value, warn = 70, crit = 90) {
  if (value >= crit) return 'red';
  if (value >= warn) return 'amber';
  return 'green';
}

function updateHealthGauge(percentage) {
  // Clamp percentage between 0 and 100
  percentage = Math.max(0, Math.min(100, percentage));
  
  // Calculate stroke-dasharray based on percentage (circumference is ~408 for radius 65)
  const maxDash = 408;
  const dashArray = (percentage / 100) * maxDash;
  
  // Determine color based on percentage
  let color, label, status, desc;
  if (percentage === 100) {
    color = '#22c55e'; // Green - all online
    label = 'OPTIMAL';
    status = 'All Systems Online';
  } else if (percentage >= 70) {
    color = '#f59e0b'; // Amber - 30% down
    label = 'DEGRADED';
    status = 'Some Systems Offline';
  } else {
    color = '#ef4444'; // Red - >30% down
    label = 'CRITICAL';
    status = 'Multiple Systems Down';
  }
  
  // Update gauge
  const arc = document.getElementById('gaugeArc');
  const percent = document.getElementById('gaugePercent');
  const gaugeLabel = document.getElementById('gaugeLabel');
  const healthStatus = document.getElementById('healthStatus');
  const healthDesc = document.getElementById('healthDesc');
  
  arc.setAttribute('stroke', color);
  arc.setAttribute('stroke-dasharray', `${dashArray},${maxDash - dashArray}`);
  percent.textContent = percentage + '%';
  percent.setAttribute('fill', color);
  gaugeLabel.textContent = label;
  gaugeLabel.setAttribute('fill', color);
  healthStatus.textContent = status;
  healthStatus.style.color = color;
  const onlineCount = document.getElementById('onlineStat').textContent;
  const totalCount = parseInt(onlineCount, 10) + parseInt(document.getElementById('offlineStat').textContent, 10);
  healthDesc.textContent = `${onlineCount} of ${totalCount} systems online`;
}

function renderFleet(pis) {
  const fleet = document.getElementById('fleetContent');
  if (!pis.length) {
    fleet.innerHTML = '<div class="empty">No Pis reporting yet. Run <code>pi_agent.py</code> on your Raspberry Pis.</div>';
    return;
  }

  const groups = {};
  for (const pi of pis) {
    if (!groups[pi.group]) groups[pi.group] = [];
    groups[pi.group].push(pi);
  }

  const groupNames = Object.keys(groups).sort();
  let html = '';
  for (const group of groupNames) {
    const items = groups[group].sort((a, b) => {
      if (a.online !== b.online) return a.online ? -1 : 1;
      return a.hostname.localeCompare(b.hostname);
    });

    html += `<div class="group-label">${escapeHtml(group)} (${items.length})</div><div class="grid">`;
    for (const pi of items) {
      const powerButton = pi.power_state === 'ON'
        ? `<button class="power-off" onclick="setPower('${escapeHtml(pi.hostname)}', false)">Turn Off</button>`
        : `<button class="power-on" onclick="setPower('${escapeHtml(pi.hostname)}', true)">Turn On</button>`;

      html += `
<div class="card pi-card ${pi.online ? '' : 'offline'}" data-search="${escapeHtml((pi.display_name + ' ' + pi.hostname + ' ' + pi.ip).toLowerCase())}">
  <div class="status ${pi.online ? 'online' : 'offline'}"></div>
  <div class="hostname">${escapeHtml(pi.display_name)}<small>Host: ${escapeHtml(pi.hostname)}</small></div>
  <div class="ip">${escapeHtml(pi.ip)} &middot; ${escapeHtml(pi.group)} &middot; seen ${escapeHtml(pi.last_seen_ago)}</div>
  <div class="metrics">
    <div class="metric"><div class="label">Torque</div><div class="value">${pi.torque}%</div><div class="bar-bg"><div class="bar-fill ${colorClass(pi.torque)}" style="width:${pi.torque}%"></div></div></div>
    <div class="metric"><div class="label">Position</div><div class="value">${pi.position}%</div><div class="bar-bg"><div class="bar-fill ${colorClass(pi.position, 80, 95)}" style="width:${pi.position}%"></div></div></div>
    <div class="metric"><div class="label">Disk</div><div class="value">${pi.disk_percent}%</div><div class="bar-bg"><div class="bar-fill ${colorClass(pi.disk_percent, 80, 95)}" style="width:${pi.disk_percent}%"></div></div></div>
    <div class="metric"><div class="label">Temp</div><div class="value">${pi.cpu_temp}°C</div><div class="bar-bg"><div class="bar-fill ${colorClass(pi.cpu_temp, 60, 75)}" style="width:${Math.min(pi.cpu_temp, 100)}%"></div></div></div>
    
    <div class="metric"><div class="label">Load (1m)</div><div class="value">${pi.load_1m}</div></div>
  </div>
  <div class="swarm-meta">
    <div>Status: <b>${escapeHtml(pi.status)}</b></div>
    <div>Leader: ${escapeHtml(pi.leader_id)}</div>
    <div>Detail: ${escapeHtml(pi.detail)}</div>
    <div>Power: <b>${escapeHtml(pi.power_state)}</b></div>
  </div>
  <div class="rename-row">
    <input id="rename-${escapeHtml(pi.hostname)}" type="text" value="${escapeHtml(pi.display_name)}" placeholder="Custom name">
    <button onclick="renamePi('${escapeHtml(pi.hostname)}')">Save Name</button>
  </div>
  <div class="power-row">${powerButton}</div>
</div>`;
    }
    html += '</div>';
  }

  fleet.innerHTML = html;
  filterCards();
}

async function refreshDashboardData() {
  try {
    const resp = await fetch('/api/summary', { cache: 'no-store' });
    if (!resp.ok) return;
    const data = await resp.json();

    document.getElementById('metaText').innerHTML = `Live updates every 1s &middot; ${data.total} Pi(s) registered`;
    document.getElementById('onlineStat').textContent = data.online;
    document.getElementById('offlineStat').textContent = data.offline;
    document.getElementById('avgTorqueStat').textContent = `${data.avg_torque}`;
    document.getElementById('avgPositionStat').textContent = `${data.avg_position}`;
    updateHealthGauge(data.health_percent);
    renderFleet(data.pis || []);
  } catch (_err) {
    // Keep previous frame if one poll fails.
  }
}

// Initialize gauge on page load
document.addEventListener('DOMContentLoaded', function() {
  updateHealthGauge({{ health_percent }});
  setInterval(refreshDashboardData, 1000);
});

function filterCards() {
  const q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.pi-card').forEach(c => {
    c.style.display = c.dataset.search.includes(q) ? '' : 'none';
  });
}

async function renamePi(hostname) {
  const input = document.getElementById(`rename-${hostname}`);
  const displayName = input.value.trim();
  if (!displayName) {
    alert('Name cannot be empty');
    return;
  }

  const resp = await fetch('/api/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hostname, display_name: displayName })
  });

  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    alert(data.error || 'Rename failed');
    return;
  }
  await refreshDashboardData();
}

async function setPower(hostname, powerOn) {
  const resp = await fetch('/api/power', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hostname, power_on: powerOn })
  });

  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    alert(data.error || 'Power command failed');
    return;
  }
  await refreshDashboardData();
}
</script>
</body>
</html>
"""


def color_class(value, warn=70, crit=90):
    if value >= crit: return "red"
    if value >= warn: return "amber"
    return "green"


def time_ago(ts):
    diff = time.time() - ts
    if diff < 60: return f"{int(diff)}s ago"
    if diff < 3600: return f"{int(diff//60)}m ago"
    return f"{int(diff//3600)}h ago"


def prune_offline_nodes(now):
    stale_hosts = []
    for hostname, d in PI_DATA.items():
        if (now - d.get("last_seen", 0)) >= OFFLINE_PRUNE_SECONDS:
            stale_hosts.append(hostname)

    for hostname in stale_hosts:
        PI_DATA.pop(hostname, None)
        PI_ALIASES.pop(hostname, None)
        PI_POWER_STATES.pop(hostname, None)

    return len(stale_hosts)


def build_dashboard_snapshot(now):
    with DATA_LOCK:
        prune_offline_nodes(now)
        pis = []
        for hostname, d in PI_DATA.items():
            power_state = PI_POWER_STATES.get(hostname, d.get("power_state", "ON"))
            online = (now - d["last_seen"]) < STALE_SECONDS and power_state == "ON"
            display_name = PI_ALIASES.get(hostname, d.get("display_name", hostname))
            pis.append({
                "hostname": hostname,
                "display_name": display_name,
                "ip": d.get("ip", "?"),
                "group": d.get("group", "default"),
                "online": online,
                "node_id": d.get("node_id", hostname),
                "torque": round(float(d.get("torque", d.get("cpu_percent", 0))), 1),
                "position": round(float(d.get("position", d.get("mem_percent", 0))), 1),
                "status": d.get("status", d.get("swarm_status", "UNKNOWN")),
                "cpu_percent": round(float(d.get("cpu_percent", 0)), 1),
                "mem_percent": round(float(d.get("mem_percent", 0)), 1),
                "disk_percent": round(float(d.get("disk_percent", 0)), 1),
                "cpu_temp": round(float(d.get("cpu_temp", 0)), 1),
                "load_1m": round(float(d.get("load_1m", 0)), 2),
                "swarm_status": d.get("swarm_status", "UNKNOWN"),
                "leader_id": d.get("leader_id", "-"),
                "power_state": power_state,
                "detail": d.get("detail", ""),
                "last_seen_ago": time_ago(d["last_seen"]),
                "torque_color": color_class(d.get("torque", d.get("cpu_percent", 0))),
                "position_color": color_class(d.get("position", d.get("mem_percent", 0)), 80, 95),
                "cpu_color": color_class(d.get("cpu_percent", 0)),
                "mem_color": color_class(d.get("mem_percent", 0)),
                "disk_color": color_class(d.get("disk_percent", 0), 80, 95),
                "temp_color": color_class(d.get("cpu_temp", 0), 60, 75),
            })

    pis.sort(key=lambda p: (not p["online"], p["group"], p["hostname"]))
    groups = {}
    for p in pis:
        groups.setdefault(p["group"], []).append(p)

    online = sum(1 for p in pis if p["online"])
    offline = len(pis) - online
    health_percent = round((online / len(pis) * 100) if pis else 100)
    avg_torque = round(sum(p["torque"] for p in pis) / len(pis), 1) if pis else 0
    avg_position = round(sum(p["position"] for p in pis) / len(pis), 1) if pis else 0
    avg_cpu = round(sum(p["cpu_percent"] for p in pis) / len(pis), 1) if pis else 0
    avg_temp = round(sum(p["cpu_temp"] for p in pis) / len(pis), 1) if pis else 0

    return {
        "pis": pis,
        "groups": groups,
        "total": len(pis),
        "online": online,
        "offline": offline,
        "health_percent": health_percent,
        "avg_torque": avg_torque,
        "avg_position": avg_position,
        "avg_cpu": avg_cpu,
        "avg_temp": avg_temp,
    }


def _topic_zone(topic):
  parts = topic.split("/")
  if len(parts) >= 3 and parts[0] == "belimo" and parts[1] == "gossip":
    return parts[2]
  return "default"


def _safe_float(value, default=0.0):
  try:
    return float(value)
  except (TypeError, ValueError):
    return float(default)


def _handle_discovery_message(data):
  node_id = str(data.get("node_id", "")).strip()
  if not node_id:
    return

  with DATA_LOCK:
    existing = PI_DATA.get(node_id, {})
    PI_DATA[node_id] = {
      **existing,
      "hostname": node_id,
      "display_name": existing.get("display_name", node_id),
      "ip": existing.get("ip", "mqtt"),
      "group": data.get("zone", existing.get("group", "default")),
      "node_id": node_id,
      "status": "DISCOVERED",
      "swarm_status": "DISCOVERED",
      "leader_id": existing.get("leader_id", "-"),
      "detail": f"sensors={','.join(data.get('sensors', []))}",
      "last_seen": time.time(),
    }


def _handle_gossip_message(data, topic):
  node_id = str(data.get("node_id", "")).strip()
  if not node_id:
    return

  payload = data.get("data", {}) if isinstance(data.get("data", {}), dict) else {}
  torque = _safe_float(payload.get("torque", 0.0))
  position = _safe_float(payload.get("position", 0.0))
  status = str(payload.get("status", "UNKNOWN"))
  group = _topic_zone(topic)

  # The dashboard expects CPU/memory style fields; map gossip telemetry into those slots.
  mapped_data = {
    "hostname": node_id,
    "node_id": node_id,
    "display_name": node_id,
    "ip": "mqtt",
    "group": group,
    "torque": max(0.0, min(100.0, torque)),
    "position": max(0.0, min(100.0, position)),
    "status": status,
    "cpu_percent": max(0.0, min(100.0, torque)),
    "mem_percent": max(0.0, min(100.0, position)),
    "disk_percent": 0.0,
    "cpu_temp": 0.0,
    "load_1m": round(torque / 100.0, 2),
    "swarm_status": status,
    "leader_id": "-",
    "detail": f"torque={torque:.2f}, position={position:.2f}",
    "last_seen": time.time(),
  }

  with DATA_LOCK:
    existing = PI_DATA.get(node_id, {})
    PI_DATA[node_id] = {**existing, **mapped_data}
    if node_id in PI_ALIASES:
      PI_DATA[node_id]["display_name"] = PI_ALIASES[node_id]
    if node_id in PI_POWER_STATES:
      PI_DATA[node_id]["power_state"] = PI_POWER_STATES[node_id]


def _mqtt_on_connect(client, _userdata, _flags, rc):
  if rc == 0:
    client.subscribe(MQTT_DISCOVERY_TOPIC)
    client.subscribe(MQTT_GOSSIP_TOPIC)
    print(f"MQTT listener connected to {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
  else:
    print(f"MQTT listener failed to connect with rc={rc}")


def _mqtt_on_message(_client, _userdata, msg):
  try:
    data = json.loads(msg.payload.decode("utf-8"))
  except Exception as exc:
    print(f"MQTT decode error on {msg.topic}: {exc}")
    return

  if not isinstance(data, dict):
    return

  msg_type = str(data.get("type", "")).upper()
  try:
    if msg_type == "DISCOVERY":
      _handle_discovery_message(data)
      print(f"MQTT discovery received: node={data.get('node_id')} topic={msg.topic}")
    elif msg_type == "GOSSIP":
      _handle_gossip_message(data, msg.topic)
      print(f"MQTT gossip received: node={data.get('node_id')} topic={msg.topic}")
  except Exception as exc:
    print(f"MQTT handler error on {msg.topic}: {exc}")


def start_mqtt_listener():
  global MQTT_CLIENT
  if MQTT_CLIENT is not None:
    return

  client = mqtt.Client(client_id=MQTT_LISTENER_CLIENT_ID)
  client.on_connect = _mqtt_on_connect
  client.on_message = _mqtt_on_message

  try:
    client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
  except Exception as exc:
    print(f"MQTT listener connection error: {exc}")
    return

  client.loop_start()
  MQTT_CLIENT = client


@app.route("/")
def dashboard():
    now = time.time()
    snapshot = build_dashboard_snapshot(now)
    return render_template_string(HTML_TEMPLATE, **snapshot)


@app.route("/api/summary")
def api_summary():
    snapshot = build_dashboard_snapshot(time.time())
    return jsonify(snapshot)


@app.route("/api/report", methods=["POST"])
def report():
    data = request.get_json(force=True)
    hostname = data.get("hostname") or data.get("node_id")
    if not hostname:
     return jsonify({"error": "hostname or node_id required"}), 400

  # Allow agent-style payloads to be posted directly to this endpoint.
    if str(data.get("type", "")).upper() == "GOSSIP" and isinstance(data.get("data"), dict):
      gossip = data["data"]
      topic = data.get("topic", "")
      data = {
        "hostname": str(hostname),
        "display_name": str(hostname),
        "ip": "http",
        "group": _topic_zone(topic) if topic else "default",
        "cpu_percent": max(0.0, min(100.0, _safe_float(gossip.get("torque", 0.0)))),
        "mem_percent": max(0.0, min(100.0, _safe_float(gossip.get("position", 0.0)))),
        "disk_percent": 0.0,
        "cpu_temp": 0.0,
        "load_1m": round(_safe_float(gossip.get("torque", 0.0)) / 100.0, 2),
        "swarm_status": str(gossip.get("status", "UNKNOWN")),
        "leader_id": "-",
        "detail": f"torque={_safe_float(gossip.get('torque', 0.0)):.2f}, position={_safe_float(gossip.get('position', 0.0)):.2f}",
      }

    with DATA_LOCK:
        existing = PI_DATA.get(hostname, {})
        PI_DATA[hostname] = {**existing, **data, "last_seen": time.time()}
        if hostname in PI_ALIASES:
            PI_DATA[hostname]["display_name"] = PI_ALIASES[hostname]
        if hostname in PI_POWER_STATES:
            PI_DATA[hostname]["power_state"] = PI_POWER_STATES[hostname]
    return jsonify({"ok": True})


@app.route("/api/rename", methods=["POST"])
def rename_pi():
    data = request.get_json(force=True)
    hostname = data.get("hostname", "").strip()
    display_name = data.get("display_name", "").strip()

    if not hostname:
        return jsonify({"error": "hostname required"}), 400
    if not display_name:
        return jsonify({"error": "display_name required"}), 400

    with DATA_LOCK:
        if hostname not in PI_DATA:
            return jsonify({"error": "unknown hostname"}), 404
        PI_ALIASES[hostname] = display_name
        PI_DATA[hostname]["display_name"] = display_name

    return jsonify({"ok": True, "hostname": hostname, "display_name": display_name})


@app.route("/api/power", methods=["POST"])
def set_power():
    data = request.get_json(force=True)
    hostname = data.get("hostname", "").strip()
    power_on = data.get("power_on")

    if not hostname:
        return jsonify({"error": "hostname required"}), 400
    if not isinstance(power_on, bool):
        return jsonify({"error": "power_on must be boolean"}), 400

    with DATA_LOCK:
        if hostname not in PI_DATA:
            return jsonify({"error": "unknown hostname"}), 404
        PI_POWER_STATES[hostname] = "ON" if power_on else "OFF"
        PI_DATA[hostname]["power_state"] = PI_POWER_STATES[hostname]

    msg = {
        "target": hostname,
        "command": "POWER_ON" if power_on else "POWER_OFF",
        "timestamp": datetime.now().isoformat(),
    }

    try:
        mqtt_publish.single(
            MQTT_CONTROL_TOPIC,
            payload=json.dumps(msg),
            hostname=MQTT_BROKER_HOST,
            port=MQTT_BROKER_PORT,
        )
    except Exception as exc:
        return jsonify({"error": f"failed to publish control command: {exc}"}), 500

    return jsonify({"ok": True, "hostname": hostname, "power_on": power_on})


@app.route("/api/pis")
def api_pis():
    with DATA_LOCK:
        prune_offline_nodes(time.time())
        return jsonify(PI_DATA)


if __name__ == "__main__":
  start_mqtt_listener()
  print("Pi Fleet Monitor running at http://localhost:5555")
  app.run(host="0.0.0.0", port=5555, debug=True, use_reloader=False)
