#!/usr/bin/env python3
"""
Raspberry Pi Fleet Monitoring Dashboard
========================================
A Flask-based dashboard for monitoring Raspberry Pi devices.

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
PI_TELEMETRY = {}
DATA_LOCK = threading.Lock()
STALE_SECONDS = 60  # Mark Pi as offline after 60s without a 'heartbeat'
OFFLINE_PRUNE_MINUTES = int(os.getenv("OFFLINE_PRUNE_MINUTES", "10"))
OFFLINE_PRUNE_SECONDS = max(OFFLINE_PRUNE_MINUTES, 1) * 60
TELEMETRY_RETENTION_POINTS = int(os.getenv("TELEMETRY_RETENTION_POINTS", "360"))
MQTT_CONTROL_TOPIC = "belimo/swarm/control"
MQTT_GOSSIP_TOPIC = "belimo/gossip/#"
MQTT_DISCOVERY_TOPIC = "belimo/discovery/#"
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
EXPECTED_NODES = int(os.getenv("EXPECTED_NODES", "0"))
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
  .card.clickable { cursor: pointer; }
  .telemetry-modal { position:fixed; inset:0; background:rgba(8,10,14,0.75); display:none; align-items:center; justify-content:center; z-index:999; padding:20px; }
  .telemetry-modal.open { display:flex; }
  .telemetry-panel { width:min(1100px,96vw); max-height:90vh; overflow:auto; background:#f5f5f5; color:#222; border-radius:12px; border:1px solid #c9c9c9; padding:18px; box-shadow:0 30px 90px rgba(0,0,0,0.55); }
  .telemetry-header { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px; }
  .telemetry-title { font-size:1.15rem; font-weight:700; }
  .telemetry-subtitle { color:#555; font-size:0.85rem; margin-bottom:12px; }
  .telemetry-close { border:0; border-radius:8px; padding:8px 12px; background:#20252e; color:#fff; cursor:pointer; font-size:0.8rem; }
  .telemetry-close:hover { filter:brightness(1.1); }
  .telemetry-plot-wrap { background:#fff; border:1px solid #d5d5d5; border-radius:10px; padding:8px; }
  .telemetry-empty { padding:28px; text-align:center; color:#666; }
</style>
</head>
<body>
<h1>🍓 Pi Fleet Monitor</h1>
<p class="meta" id="metaText">Live updates every 1s &middot; {{ total }} Pi(s) registered{% if expected_nodes > 0 %} / target {{ expected_nodes }}{% endif %}</p>

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
<div class="card pi-card clickable {{ 'offline' if not pi.online else '' }}" data-search="{{ pi.display_name|lower }} {{ pi.hostname|lower }} {{ pi.ip|lower }}" onclick="openTelemetry('{{ pi.hostname }}')">
  <div class="status {{ 'online' if pi.online else 'offline' }}"></div>
  <div class="hostname">
    {{ pi.display_name }}
    <small>Host: {{ pi.hostname }}</small>
  </div>
  <div class="ip">{{ pi.ip }} &middot; {{ pi.group }} &middot; seen {{ pi.last_seen_ago }}</div>
  <div class="metrics">
    <div class="metric">
      <div class="label">Torque</div>
      <div class="value">{{ pi.torque }}</div>
      <div class="bar-bg"><div class="bar-fill {{ pi.torque_color }}" style="width:{{ pi.torque }}%"></div></div>
    </div>
    <div class="metric">
      <div class="label">Position</div>
      <div class="value">{{ pi.position }}</div>
      <div class="bar-bg"><div class="bar-fill {{ pi.position_color }}" style="width:{{ pi.position }}%"></div></div>
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
    <button onclick="event.stopPropagation(); renamePi('{{ pi.hostname }}')">Save Name</button>
  </div>
  <div class="power-row">
    {% if pi.power_state == "ON" %}
    <button class="power-off" onclick="event.stopPropagation(); setPower('{{ pi.hostname }}', false)">Turn Off</button>
    {% else %}
    <button class="power-on" onclick="event.stopPropagation(); setPower('{{ pi.hostname }}', true)">Turn On</button>
    {% endif %}
  </div>
</div>
{% endfor %}
</div>
{% endfor %}
{% endif %}
</div>

<div id="telemetryModal" class="telemetry-modal" onclick="closeTelemetryModal(event)">
  <div class="telemetry-panel" onclick="event.stopPropagation()">
    <div class="telemetry-header">
      <div class="telemetry-title" id="telemetryTitle">Telemetry</div>
      <button class="telemetry-close" onclick="closeTelemetryModal(event)">Close</button>
    </div>
    <div class="telemetry-subtitle" id="telemetrySubtitle">Click a Raspberry Pi card to inspect trends.</div>
    <div class="telemetry-plot-wrap" id="telemetryPlotWrap">
      <div class="telemetry-empty">No telemetry loaded yet.</div>
    </div>
  </div>
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

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function stats(values) {
  if (!values.length) return { min: 0, max: 1 };
  let min = values[0], max = values[0];
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  return { min, max };
}

function linePath(points, x0, y0, w, h, yMin, yMax, key) {
  if (!points.length) return '';
  const span = Math.max(1, points.length - 1);
  const ySpan = yMax - yMin;
  return points.map((p, idx) => {
    const x = x0 + (idx / span) * w;
    const y = y0 + h - ((p[key] - yMin) / ySpan) * h;
    return `${idx === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
}

function horizontalY(value, y0, h, yMin, yMax) {
  const ySpan = yMax - yMin;
  return y0 + h - ((value - yMin) / ySpan) * h;
}

function closeTelemetryModal(event) {
  if (event) event.stopPropagation();
  const modal = document.getElementById('telemetryModal');
  if (!modal) return;
  modal.classList.remove('open');
}

async function openTelemetry(rawHostname) {
  const hostname = decodeURIComponent(String(rawHostname));
  const modal = document.getElementById('telemetryModal');
  const title = document.getElementById('telemetryTitle');
  const subtitle = document.getElementById('telemetrySubtitle');
  const wrap = document.getElementById('telemetryPlotWrap');

  if (!modal || !title || !subtitle || !wrap) {
    console.error('Telemetry modal elements are missing from the DOM.');
    return;
  }

  title.textContent = `Telemetry - ${hostname}`;
  subtitle.textContent = 'Loading recent telemetry...';
  wrap.innerHTML = '<div class="telemetry-empty">Loading chart...</div>';
  modal.classList.add('open');

  try {
    const resp = await fetch(`/api/telemetry/${encodeURIComponent(hostname)}?points=180`, { cache: 'no-store' });
    if (!resp.ok) {
      wrap.innerHTML = '<div class="telemetry-empty">No telemetry available for this node yet.</div>';
      return;
    }

    const data = await resp.json();
    const points = Array.isArray(data.points) ? data.points : [];
    if (!points.length) {
      wrap.innerHTML = '<div class="telemetry-empty">No telemetry points collected yet.</div>';
      return;
    }

    const svgWidth = 1020;
    const topHeight = 250;
    const bottomHeight = 250;
    const leftPad = 52;
    const rightPad = 16;
    const plotWidth = svgWidth - leftPad - rightPad;

    const temps = points.map(p => Number(p.cpu_temp || 0));
    const valve = points.map(p => Number(p.position || 0));

    const avgTemp = Number(data.temp_avg || 0);
    const threshold = Number(data.temp_threshold_15pct || 0);
    const topBandHi = avgTemp + threshold;
    const topBandLo = avgTemp - threshold;

    const tempStats = stats(temps.concat([topBandHi, topBandLo, avgTemp]));
    const valveStats = stats(valve);

    const topY = 38;
    const topPlotH = 180;
    const botY = 300;
    const botPlotH = 180;

    const tempPath = linePath(points, leftPad, topY, plotWidth, topPlotH, tempStats.min, tempStats.max, 'cpu_temp');
    const valvePath = linePath(points, leftPad, botY, plotWidth, botPlotH, valveStats.min, valveStats.max, 'position');

    const avgY = horizontalY(avgTemp, topY, topPlotH, tempStats.min, tempStats.max);
    const hiY = horizontalY(topBandHi, topY, topPlotH, tempStats.min, tempStats.max);
    const loY = horizontalY(topBandLo, topY, topPlotH, tempStats.min, tempStats.max);

    const svg = `
<svg viewBox="0 0 ${svgWidth} 520" width="100%" height="520" role="img" aria-label="Telemetry charts">
  <rect x="0" y="0" width="${svgWidth}" height="520" fill="#f0f0f0"/>
  <text x="${svgWidth / 2}" y="24" text-anchor="middle" font-size="14" fill="#333">Highly Sensitive Threshold: 15% of Standard Deviation (${threshold.toFixed(4)} units)</text>

  <rect x="${leftPad}" y="${topY}" width="${plotWidth}" height="${topPlotH}" fill="none" stroke="#888" stroke-width="1"/>
  <path d="${tempPath}" fill="none" stroke="#9a9a9a" stroke-width="2"/>
  <line x1="${leftPad}" y1="${avgY.toFixed(2)}" x2="${leftPad + plotWidth}" y2="${avgY.toFixed(2)}" stroke="#2ca02c" stroke-width="2"/>
  <line x1="${leftPad}" y1="${hiY.toFixed(2)}" x2="${leftPad + plotWidth}" y2="${hiY.toFixed(2)}" stroke="#ff3333" stroke-width="2" stroke-dasharray="6 4"/>
  <line x1="${leftPad}" y1="${loY.toFixed(2)}" x2="${leftPad + plotWidth}" y2="${loY.toFixed(2)}" stroke="#ff3333" stroke-width="2" stroke-dasharray="6 4"/>
  <text x="${leftPad + 8}" y="${topY + 16}" font-size="12" fill="#555">Temp</text>
  <text x="${leftPad + 8}" y="${topY + 32}" font-size="12" fill="#d33">15% SD Threshold</text>

  <rect x="${leftPad}" y="${botY}" width="${plotWidth}" height="${botPlotH}" fill="none" stroke="#888" stroke-width="1"/>
  <path d="${valvePath}" fill="none" stroke="#f0a11a" stroke-width="2"/>
  <text x="${leftPad + 8}" y="${botY + 16}" font-size="12" fill="#a66d10">Valve (0.15 SD Control)</text>
  <text x="${svgWidth / 2}" y="510" text-anchor="middle" font-size="14" fill="#333">Time</text>
</svg>`;

    subtitle.textContent = `${data.display_name || hostname} · ${points.length} samples · latest load ${Number(points[points.length - 1].load_1m || 0).toFixed(2)}`;
    wrap.innerHTML = svg;
  } catch (_err) {
    wrap.innerHTML = '<div class="telemetry-empty">Failed to load telemetry.</div>';
  }
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
        ? `<button class="power-off" onclick="event.stopPropagation(); setPower('${escapeHtml(pi.hostname)}', false)">Turn Off</button>`
        : `<button class="power-on" onclick="event.stopPropagation(); setPower('${escapeHtml(pi.hostname)}', true)">Turn On</button>`;

      html += `
<div class="card pi-card clickable ${pi.online ? '' : 'offline'}" data-search="${escapeHtml((pi.display_name + ' ' + pi.hostname + ' ' + pi.ip).toLowerCase())}" onclick="openTelemetry('${encodeURIComponent(pi.hostname)}')">
  <div class="status ${pi.online ? 'online' : 'offline'}"></div>
  <div class="hostname">${escapeHtml(pi.display_name)}<small>Host: ${escapeHtml(pi.hostname)}</small></div>
  <div class="ip">${escapeHtml(pi.ip)} &middot; ${escapeHtml(pi.group)} &middot; seen ${escapeHtml(pi.last_seen_ago)}</div>
  <div class="metrics">
    <div class="metric"><div class="label">Torque</div><div class="value">${pi.torque}</div><div class="bar-bg"><div class="bar-fill ${colorClass(pi.torque)}" style="width:${pi.torque}%"></div></div></div>
    <div class="metric"><div class="label">Position</div><div class="value">${pi.position}</div><div class="bar-bg"><div class="bar-fill ${colorClass(pi.position, 80, 95)}" style="width:${pi.position}%"></div></div></div>
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
    <button onclick="event.stopPropagation(); renamePi('${escapeHtml(pi.hostname)}')">Save Name</button>
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

    const expected = Number(data.expected_nodes || 0);
    document.getElementById('metaText').innerHTML = expected > 0
      ? `Live updates every 1s &middot; ${data.total} Pi(s) registered / target ${expected}`
      : `Live updates every 1s &middot; ${data.total} Pi(s) registered`;
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
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeTelemetryModal();
  });
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
        PI_TELEMETRY.pop(hostname, None)

    return len(stale_hosts)


def _append_telemetry_locked(hostname, data):
    history = PI_TELEMETRY.setdefault(hostname, [])
    history.append({
      "ts": data.get("last_seen", time.time()),
      "torque": float(data.get("torque", data.get("cpu_percent", 0.0))),
      "position": float(data.get("position", data.get("mem_percent", 0.0))),
      "cpu_temp": float(data.get("cpu_temp", data.get("torque", data.get("cpu_percent", 0.0)))),
      "load_1m": float(data.get("load_1m", 0.0)),
      "status": str(data.get("swarm_status", data.get("status", "UNKNOWN"))),
    })
    if len(history) > TELEMETRY_RETENTION_POINTS:
      del history[:-TELEMETRY_RETENTION_POINTS]


def _online_hosts_locked(now):
    online_hosts = []
    for hostname, d in PI_DATA.items():
        power_state = PI_POWER_STATES.get(hostname, d.get("power_state", "ON"))
        if power_state != "ON":
            continue
        if (now - d.get("last_seen", 0)) >= STALE_SECONDS:
            continue
        online_hosts.append(hostname)
    return online_hosts


def _reassign_leader_locked(now):
    """Choose an online leader and propagate that leader_id to all known nodes."""
    online_hosts = _online_hosts_locked(now)
    if not online_hosts:
        for d in PI_DATA.values():
            d["leader_id"] = "-"
        return "-"

    leader_votes = {}
    for d in PI_DATA.values():
        candidate = str(d.get("leader_id", "")).strip()
        if candidate in online_hosts:
            leader_votes[candidate] = leader_votes.get(candidate, 0) + 1

    if leader_votes:
        # Stable deterministic selection by vote count, then hostname.
        selected_leader = sorted(leader_votes.items(), key=lambda item: (-item[1], item[0]))[0][0]
    else:
        selected_leader = sorted(online_hosts)[0]

    for hostname, d in PI_DATA.items():
        d["leader_id"] = selected_leader
        power_state = PI_POWER_STATES.get(hostname, d.get("power_state", "ON"))
        if power_state == "OFF":
            d["status"] = "OFFLINE"
            d["swarm_status"] = "OFFLINE"

    return selected_leader


def build_dashboard_snapshot(now):
    with DATA_LOCK:
        prune_offline_nodes(now)
        _reassign_leader_locked(now)
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
    known_offline = len(pis) - online
    missing_nodes = max(EXPECTED_NODES - len(pis), 0) if EXPECTED_NODES > 0 else 0
    offline = known_offline + missing_nodes
    target_total = len(pis) + missing_nodes
    health_percent = round((online / target_total * 100) if target_total else 100)
    avg_torque = round(sum(p["torque"] for p in pis) / len(pis), 1) if pis else 0
    avg_position = round(sum(p["position"] for p in pis) / len(pis), 1) if pis else 0
    avg_cpu = round(sum(p["cpu_percent"] for p in pis) / len(pis), 1) if pis else 0
    avg_temp = round(sum(p["cpu_temp"] for p in pis) / len(pis), 1) if pis else 0

    return {
        "pis": pis,
        "groups": groups,
        "total": len(pis),
      "expected_nodes": EXPECTED_NODES,
      "missing_nodes": missing_nodes,
      "target_total": target_total,
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
    _append_telemetry_locked(node_id, PI_DATA[node_id])


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
            if PI_POWER_STATES[hostname] == "OFF":
                PI_DATA[hostname]["status"] = "OFFLINE"
                PI_DATA[hostname]["swarm_status"] = "OFFLINE"
        _append_telemetry_locked(hostname, PI_DATA[hostname])
        _reassign_leader_locked(time.time())
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
        if power_on:
          PI_DATA[hostname]["status"] = "OPTIMAL"
          PI_DATA[hostname]["swarm_status"] = "OPTIMAL"
          PI_DATA[hostname]["detail"] = "Powered on by dashboard"
        else:
          PI_DATA[hostname]["status"] = "OFFLINE"
          PI_DATA[hostname]["swarm_status"] = "OFFLINE"
          PI_DATA[hostname]["detail"] = "Powered off by dashboard"

        PI_DATA[hostname]["last_seen"] = time.time()
        _append_telemetry_locked(hostname, PI_DATA[hostname])
        _reassign_leader_locked(time.time())

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
        _reassign_leader_locked(time.time())
        return jsonify(PI_DATA)


@app.route("/api/telemetry/<hostname>")
def api_telemetry(hostname):
  requested_points = request.args.get("points", default=180, type=int)
  points_limit = max(30, min(requested_points, TELEMETRY_RETENTION_POINTS))

  with DATA_LOCK:
    if hostname not in PI_DATA:
      return jsonify({"error": "unknown hostname"}), 404

    points = PI_TELEMETRY.get(hostname, [])[-points_limit:]
    current = PI_DATA.get(hostname, {})

  if points:
    temps = [float(p.get("cpu_temp", 0.0)) for p in points]
    avg_temp = sum(temps) / len(temps)
    variance = sum((v - avg_temp) ** 2 for v in temps) / len(temps)
    std_temp = variance ** 0.5
  else:
    avg_temp = 0.0
    std_temp = 0.0

  threshold_15 = std_temp * 0.15
  return jsonify({
    "hostname": hostname,
    "display_name": current.get("display_name", hostname),
    "points": points,
    "temp_avg": round(avg_temp, 4),
    "temp_sd": round(std_temp, 4),
    "temp_threshold_15pct": round(threshold_15, 4),
  })


if __name__ == "__main__":
  start_mqtt_listener()
  print("Pi Fleet Monitor running at http://localhost:5555")
  app.run(host="0.0.0.0", port=5555, debug=True, use_reloader=False)
