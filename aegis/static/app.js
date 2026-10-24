/* Aegis AI Autonomous Operations Dashboard
 * Vanilla JS, zero external dependencies, live Canvas telemetry rendering,
 * and multi-view orchestration.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "–");
const fmtBytes = (b) => {
  if (b === null || b === undefined) return "–";
  const u = ["B/s", "KiB/s", "MiB/s", "GiB/s"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(1)} ${u[i]}`;
};

let chartData = { ts: [], cpu_percent: [], mem_percent: [] };
let activeIncidentId = null;

// ---------------------------------------------------------------- Tab Switching
document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
    btn.classList.add("active");
    const target = $(`tab-${btn.dataset.tab}`);
    if (target) target.classList.add("active");
    // Trigger specific tab refreshes
    if (btn.dataset.tab === "workloads") refreshDocker();
    if (btn.dataset.tab === "ai") refreshAI();
    if (btn.dataset.tab === "incidents") refreshIncidents();
    if (btn.dataset.tab === "engineering") refreshDevin();
    if (btn.dataset.tab === "policy") refreshAudit();
  });
});

// ------------------------------------------------------------- Core Overview Polling
async function pollOverview() {
  try {
    const snap = await (await fetch("/api/overview")).json();
    renderHealth(snap);
    renderCards(snap);
    renderThermal(snap);
    renderQuickSummaries(snap);
    $("footNote").textContent =
      `Host: ${snap.host.hostname} (${snap.host.platform}) · Engine: ${snap.engine_running ? "RUNNING" : "STOPPED"} · Interval: ${snap.collect_interval}s · DB: ${snap.db.path}`;
  } catch {
    setHealth("crit", "API unreachable — retrying");
  }
}

function renderHealth(snap) {
  const isOk = snap.engine_running && snap.db.integrity === "ok";
  setHealth(isOk ? "ok" : "warn", isOk ? "Autonomous Engine Active" : "Degraded");

  const modeTgl = $("modeToggle");
  modeTgl.checked = snap.mode === "policy_autonomous";
  $("modeLabel").textContent = snap.mode;

  const estop = $("estopBtn");
  if (snap.emergency_stop) {
    estop.classList.add("active");
    estop.textContent = "Emergency Stop ENGAGED";
  } else {
    estop.classList.remove("active");
    estop.textContent = "Emergency Stop";
  }
}

function setHealth(kind, txt) {
  const h = $("health");
  h.innerHTML = `<span class="dot dot-${kind}"></span><span>${txt}</span>`;
}

function renderCards(snap) {
  const l = snap.latest || {};
  const mem = l.memory || {};
  const io = l.io_rates || {};
  const res = snap.resources || {};

  // CPU
  const cpu = l.cpu_percent !== undefined ? `${l.cpu_percent.toFixed(1)}%` : "–";
  $("cpuVal").textContent = cpu;
  $("cpuVal").className = `big ${l.cpu_percent > 85 ? "crit" : l.cpu_percent > 70 ? "warn" : "ok"}`;
  $("cpuSub").textContent = `Load avg: ${l.load_avg_1 ?? "–"} | Cores: ${snap.host.cpu_cores || "–"}`;

  // Memory
  const memPct = mem.percent !== undefined ? `${mem.percent.toFixed(1)}%` : "–";
  $("memVal").textContent = memPct;
  $("memVal").className = `big ${mem.percent > 90 ? "crit" : mem.percent > 75 ? "warn" : "ok"}`;
  $("memSub").textContent = `${mem.used_mb ?? "–"} MB used / ${mem.total_mb ?? "–"} MB`;

  // Disks
  const disks = l.disks || [];
  const maxDisk = disks.reduce((acc, d) => Math.max(acc, d.used_pct), 0);
  $("diskVal").textContent = disks.length ? `${maxDisk.toFixed(0)}%` : "–";
  $("diskVal").className = `big ${maxDisk > 90 ? "crit" : maxDisk > 75 ? "warn" : "ok"}`;
  $("diskSub").textContent = `${disks.length} filesystem partition(s) monitored`;

  // Network
  $("netVal").textContent = fmtBytes(io.recv_bytes_s);
  $("netSub").textContent = `↑ ${fmtBytes(io.sent_bytes_s)} TX`;

  // GPU
  const gpu = snap.gpu || {};
  if (gpu.available && gpu.devices && gpu.devices.length > 0) {
    const dev = gpu.devices[0];
    $("gpuVal").textContent = `${dev.utilization_gpu_pct ?? 0}%`;
    $("gpuVal").className = "big ok";
    $("gpuSub").textContent = `${dev.name} (${dev.temperature_c ?? "–"}°C)`;
  } else {
    $("gpuVal").textContent = "N/A";
    $("gpuVal").className = "big muted";
    $("gpuSub").textContent = gpu.reason || "No dedicated GPU detected";
  }

  // Active Incidents banner
  const banner = $("incidentBanner");
  if (res.active_breaches && res.active_breaches.length > 0) {
    banner.style.display = "block";
    banner.textContent = `⚠️ Resource Guardian Alert: ${res.active_breaches.join(" | ")}`;
  } else {
    banner.style.display = "none";
  }
}

function renderThermal(snap) {
  const t = snap.thermal || {};
  const el = $("thermalState");
  el.textContent = t.state || "–";
  el.className = `big ${t.state === "CRITICAL" ? "crit" : t.state === "WARNING" ? "warn" : t.state === "RECOVERY" ? "sim" : "ok"}`;

  const cur = t.current_c !== null && t.current_c !== undefined ? `${t.current_c.toFixed(1)}°C` : "–";
  const trend = t.trend_per_s !== null && t.trend_per_s !== undefined ? `${t.trend_per_s >= 0 ? "+" : ""}${t.trend_per_s.toFixed(2)}°/s` : "stable";
  $("thermalSub").textContent = `${cur} (${trend}) · ${t.reason || ""}`;

  const q = $("thermalQual");
  q.innerHTML = `<span class="chip ${t.data_quality}">${t.data_quality}</span>` +
    (snap.overrides_enabled && Object.keys(snap.active_overrides || {}).length ? '<span class="tag">SIMULATED OVERRIDE</span>' : "");
}

function renderQuickSummaries(snap) {
  const d = snap.docker || {};
  const k = snap.kubernetes || {};
  const ai = snap.ai_workloads || {};

  $("quickWorkloads").innerHTML = `
    <div class="summary-item"><div class="title">Docker</div>${d.available ? `${d.running_count} running / ${d.total_containers} total containers` : d.reason}</div>
    <div class="summary-item"><div class="title">Kubernetes</div>${k.available ? `${k.nodes.length} nodes, ${k.pods.length} pods` : k.reason}</div>
    <div class="summary-item"><div class="title">AI Serving (Ollama)</div>${ai.ollama && ai.ollama.available ? `Active (${ai.ollama.latency_ms}ms) · ${ai.ollama.installed_models.length} model(s)` : (ai.ollama ? ai.ollama.reason : "Offline")}</div>
  `;
}

// ------------------------------------------------------------- History Chart
async function pollHistory() {
  try {
    chartData = await (await fetch("/api/metrics/history?points=120")).json();
    drawChart();
  } catch {}
}

function drawChart() {
  const c = $("chart-main");
  if (!c) return;
  const ctx = c.getContext("2d");
  const w = c.clientWidth, h = c.height;
  c.width = w;
  ctx.clearRect(0, 0, w, h);

  const pts = chartData.cpu_percent || [];
  const memPts = chartData.mem_percent || [];
  if (pts.length < 2) return;

  const dx = w / (pts.length - 1);
  const plot = (arr, color) => {
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    arr.forEach((v, i) => {
      const val = v !== null ? v : 0;
      const y = h - (val / 100) * (h - 20) - 10;
      if (i === 0) ctx.moveTo(0, y);
      else ctx.lineTo(i * dx, y);
    });
    ctx.stroke();
  };

  plot(pts, "#58a6ff");
  plot(memPts, "#3fb950");
}

// ------------------------------------------------------------- Hardware & Sensors Tab
async function pollSensors() {
  try {
    const sensors = await (await fetch("/api/sensors")).json();
    renderSensorsTable(sensors);
    renderTempsList(sensors);

    const storage = await (await fetch("/api/system/storage")).json();
    renderStorageTable(storage.partitions || []);

    const inv = await (await fetch("/api/system/inventory")).json();
    renderInventory(inv);
  } catch {}
}

function renderSensorsTable(sensors) {
  const tb = $("sensorTbl").querySelector("tbody");
  tb.innerHTML = sensors.map(s => {
    const l = s.latest || {};
    const val = l.value !== null && l.value !== undefined ? `${l.value.toFixed(1)} ${s.unit || ""}` : "–";
    return `<tr>
      <td><code>${s.sensor_id}</code></td>
      <td>${s.kind}</td>
      <td>${s.source}</td>
      <td><strong>${val}</strong></td>
      <td><span class="chip ${l.validity}">${l.validity}</span></td>
      <td>${fmtTime(l.ts ? l.ts * 1000 : null)}</td>
    </tr>`;
  }).join("");
}

function renderTempsList(sensors) {
  const temps = sensors.filter(s => s.kind === "temperature");
  const el = $("tempList");
  if (!temps.length) {
    el.innerHTML = '<div class="muted">No physical temperature sensors registered on host.</div>';
    return;
  }
  el.innerHTML = temps.map(s => {
    const l = s.latest || {};
    const val = l.value !== null && l.value !== undefined ? `${l.value.toFixed(1)}°C` : "–";
    return `<div class="temp-item">
      <div class="val">${val}</div>
      <div class="meta"><div><strong>${s.label || s.sensor_id}</strong></div><div class="muted">${s.source} · <span class="chip ${l.validity}">${l.validity}</span></div></div>
    </div>`;
  }).join("");
}

function renderStorageTable(partitions) {
  const tb = $("storageTbl").querySelector("tbody");
  tb.innerHTML = partitions.map(p => `
    <tr>
      <td><strong>${p.mount}</strong></td>
      <td>${p.device}</td>
      <td>${p.used_gb} GB</td>
      <td>${p.free_gb} GB</td>
      <td>${p.total_gb} GB</td>
      <td><span class="${p.used_pct > 90 ? 'ev-crit' : 'ev-info'}">${p.used_pct}%</span></td>
    </tr>
  `).join("");
}

function renderInventory(inv) {
  const hw = inv.hardware || {};
  const sw = inv.software || {};
  $("inventoryContainer").innerHTML = `
    <div class="summary-item"><div class="title">Operating System</div>${sw.os_name} (${sw.architecture}) · Kernel ${sw.kernel}</div>
    <div class="summary-item"><div class="title">CPU Architecture</div>${hw.cpu.model || "Unknown"} · ${hw.cpu.logical_cores} logical cores</div>
    <div class="summary-item"><div class="title">Memory Capacity</div>${hw.memory.total_mb} MB RAM · ${hw.memory.swap_total_mb} MB Swap</div>
    <div class="summary-item"><div class="title">Runtimes</div>Python ${sw.python_version} · Docker ${sw.docker ? sw.docker.version : "N/A"}</div>
  `;
}

// ------------------------------------------------------------- Docker & K8s Tab
async function refreshDocker() {
  try {
    const d = await (await fetch("/api/workloads/docker")).json();
    const tb = $("dockerTbl").querySelector("tbody");
    if (!d.available || !d.containers.length) {
      tb.innerHTML = `<tr><td colspan="8" class="muted">${d.reason || "No containers discovered."}</td></tr>`;
      return;
    }
    tb.innerHTML = d.containers.map(c => {
      const m = c.metrics || {};
      return `<tr>
        <td><strong>${c.name}</strong></td>
        <td><code>${c.image.substring(0, 24)}</code></td>
        <td><span class="chip ${c.status === 'running' ? 'VALID' : 'ERROR'}">${c.status}</span></td>
        <td>${c.health}</td>
        <td>${c.restart_count}</td>
        <td>${m.memory_usage_mb ? `${m.memory_usage_mb} MB` : "–"}</td>
        <td>${m.cpu_percent !== undefined ? `${m.cpu_percent}%` : "–"}</td>
        <td>
          <button class="btn" onclick="viewContainerLogs('${c.id}', '${c.name}')">Logs</button>
          <button class="btn danger" onclick="restartContainer('${c.id}')">Restart</button>
        </td>
      </tr>`;
    }).join("");

    const k = await (await fetch("/api/workloads/k8s")).json();
    $("k8sOverview").innerHTML = k.available ?
      k.nodes.map(n => `<div class="summary-item"><div class="title">Node: ${n.name}</div>Status: ${n.status} · Kubelet: ${n.kubelet_version}</div>`).join("") :
      `<div class="muted">${k.reason}</div>`;
  } catch {}
}

window.viewContainerLogs = async function(cid, name) {
  try {
    const res = await (await fetch(`/api/workloads/docker/${cid}/logs?tail=80`)).json();
    $("logViewerTitle").textContent = `Logs: ${name}`;
    $("logViewerContent").textContent = res.logs || "No logs available.";
    $("logViewerPanel").style.display = "block";
  } catch {}
};

$("closeLogsBtn").addEventListener("click", () => {
  $("logViewerPanel").style.display = "none";
});

window.restartContainer = async function(cid) {
  if (!confirm("Authorize container restart?")) return;
  try {
    const res = await (await fetch(`/api/workloads/docker/${cid}/restart`, { method: "POST" })).json();
    alert(`Restart result: ${res.result} (${res.mode || res.reason || ""})`);
    refreshDocker();
  } catch (e) {
    alert(`Restart error: ${e}`);
  }
};

// ------------------------------------------------------------- AI Infrastructure Tab
async function refreshAI() {
  try {
    const ai = await (await fetch("/api/workloads/ai")).json();
    const o = ai.ollama || {};
    $("ollamaStatus").innerHTML = `
      <div class="summary-item"><div class="title">Status</div>${o.available ? "Online" : "Offline"} · ${o.endpoint}</div>
      <div class="summary-item"><div class="title">Endpoint Latency</div>${o.latency_ms ? `${o.latency_ms} ms` : "–"}</div>
      <div class="summary-item"><div class="title">Installed Models</div>${(o.installed_models || []).join(", ") || "None"}</div>
    `;

    $("activeModels").innerHTML = (o.active_models && o.active_models.length) ?
      o.active_models.map(m => `<div class="summary-item"><div class="title">${m.name}</div>Size: ${m.size_mb} MB (VRAM: ${m.size_vram_mb} MB)</div>`).join("") :
      '<div class="muted">No models currently loaded in memory.</div>';

    $("aiProcs").innerHTML = (ai.ai_processes && ai.ai_processes.length) ?
      ai.ai_processes.map(p => `<div class="summary-item"><div class="title">${p.name} (PID ${p.pid})</div>Runtime: ${p.runtime} · CPU: ${p.cpu_percent}% · RAM: ${p.memory_mb} MB</div>`).join("") :
      '<div class="muted">No local AI inference processes active.</div>';

    const dev = await (await fetch("/api/devin/status")).json();
    $("devinStatusBox").innerHTML = `
      <div class="summary-item"><div class="title">Devin Status</div><strong>${dev.status}</strong></div>
      <div class="summary-item"><div class="title">API Key Configured</div>${dev.has_api_key ? "Yes" : "No"}</div>
      <div class="summary-item"><div class="title">Desktop CLI</div>${dev.has_desktop_cli ? dev.cli_path : "Not found on PATH"}</div>
      <div class="muted" style="margin-top:6px;">${dev.setup_instructions}</div>
    `;
  } catch {}
}

// ------------------------------------------------------------- Incidents Tab
async function refreshIncidents() {
  try {
    const list = await (await fetch("/api/incidents")).json();
    const tb = $("incidentsTbl").querySelector("tbody");
    if (!list.length) {
      tb.innerHTML = '<tr><td colspan="7" class="muted">No incidents recorded. Infrastructure nominal.</td></tr>';
      return;
    }
    tb.innerHTML = list.map(inc => `
      <tr>
        <td><code>${inc.id}</code></td>
        <td>${inc.category}</td>
        <td><span class="chip ${inc.severity === 'critical' ? 'ERROR' : 'STALE'}">${inc.severity}</span></td>
        <td><strong>${inc.title}</strong></td>
        <td><span class="chip ${inc.state === 'RECOVERY_VERIFIED' ? 'VALID' : 'STALE'}">${inc.state}</span></td>
        <td>${fmtTime(inc.created_at)}</td>
        <td><button class="btn" onclick="selectIncident('${inc.id}')">Inspect</button></td>
      </tr>
    `).join("");
  } catch {}
}

window.selectIncident = async function(iid) {
  activeIncidentId = iid;
  try {
    const list = await (await fetch("/api/incidents")).json();
    const inc = list.find(x => x.id === iid);
    if (!inc) return;

    $("incDetailTitle").textContent = `Incident: ${inc.id} (${inc.state})`;
    $("incDetailContent").innerHTML = `
      <p><strong>Summary:</strong> ${inc.summary || "–"}</p>
      <p><strong>Lifecycle:</strong></p>
      <div class="events">${(inc.lifecycle || []).map(l => `<div class="ev"><time>${fmtTime(l.ts)}</time> <strong>${l.state}</strong> (${l.actor || ""}): ${l.note || ""}</div>`).join("")}</div>
      <p style="margin-top:10px;"><strong>Evidence:</strong></p>
      <pre class="log-box">${JSON.stringify(inc.evidence || [], null, 2)}</pre>
    `;
    $("incidentDetailPanel").style.display = "block";
  } catch {}
};

$("runDiagBtn").addEventListener("click", async () => {
  if (!activeIncidentId) return;
  $("runDiagBtn").textContent = "Diagnosing…";
  try {
    const res = await (await fetch(`/api/incidents/${activeIncidentId}/diagnose`, { method: "POST" })).json();
    alert(`Diagnosis (${res.provider}):\nRoot Cause: ${res.root_cause_hypothesis}\nConfidence: ${res.confidence_score * 100}%\nCode/Config Issue: ${res.is_code_or_config_issue}`);
    selectIncident(activeIncidentId);
  } catch (e) {
    alert(`Diagnosis error: ${e}`);
  } finally {
    $("runDiagBtn").textContent = "Diagnose with AI";
  }
});

// ------------------------------------------------------------- Devin & Engineering Tab
async function refreshDevin() {
  try {
    const tasks = await (await fetch("/api/devin/tasks")).json();
    $("devinTasksList").innerHTML = tasks.length ?
      tasks.map(t => `<div class="summary-item"><div class="title">${t.id} (${t.state})</div>${t.payload ? t.payload.title : ""} · Claims: ${t.claimed_by || "None"}</div>`).join("") :
      '<div class="muted">No Devin repair tasks in queue.</div>';

    const gh = await (await fetch("/api/github/status")).json();
    $("githubStatusBox").innerHTML = `
      <div class="summary-item"><div class="title">Repository</div><a href="${gh.repo_url}" target="_blank">${gh.owner}/${gh.repo}</a></div>
      <div class="summary-item"><div class="title">Authentication</div>${gh.authenticated ? `Authenticated (${gh.auth_method})` : "Unauthenticated"}</div>
    `;
  } catch {}
}

$("runValidationBtn").addEventListener("click", async () => {
  const tid = $("valTaskId").value || "TASK-VAL-DEMO";
  const patchText = $("valPatchText").value;
  if (!patchText) {
    alert("Please provide a unified git patch to validate.");
    return;
  }
  $("runValidationBtn").textContent = "Validating…";
  try {
    const res = await (await fetch("/api/repair/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: tid, patch: patchText })
    })).json();

    $("valResultBox").innerHTML = `
      <div class="summary-item">
        <div class="title">Validation ${res.passed ? "PASSED ✅" : "FAILED ❌"}</div>
        <p>Scope Valid: ${res.scope_valid ? "Yes" : `No (${res.rejection_reason})`}</p>
        <p>Changed Files: ${(res.changed_files || []).join(", ")}</p>
        <div class="events">${(res.steps || []).map(s => `<div class="ev">${s.passed ? "✅" : "❌"} <code>${s.command}</code> (exit code ${s.exit_code})</div>`).join("")}</div>
      </div>
    `;
  } catch (e) {
    alert(`Validation error: ${e}`);
  } finally {
    $("runValidationBtn").textContent = "Run Isolated Validation";
  }
});

// ------------------------------------------------------------- Policy, Audit & Demo Tab
async function refreshAudit() {
  try {
    const actions = await (await fetch("/api/actions?limit=50")).json();
    const tb = $("auditTbl").querySelector("tbody");
    tb.innerHTML = actions.map(a => `
      <tr>
        <td>${fmtTime(a.ts)}</td>
        <td><code>operator</code></td>
        <td><strong>${a.action}</strong></td>
        <td>${a.target || "–"}</td>
        <td>${a.detail || a.reason || "–"}</td>
      </tr>
    `).join("");

    const demo = await (await fetch("/api/demo")).json();
    renderDemo(demo);
  } catch {}
}

function renderDemo(demo) {
  const container = $("demoBtns");
  const kinds = demo.kinds || {};
  container.innerHTML = Object.entries(kinds).map(([k, desc]) => `
    <button class="btn" title="${desc}" onclick="applyDemo('${k}')">${k.replace("_", " ").toUpperCase()}</button>
  `).join("");

  const active = Object.entries(demo.active_overrides || {});
  $("demoActive").innerHTML = active.length ?
    `Active simulated overrides: ${active.map(([s, d]) => `<code>${s}=${d.value_c}°C</code>`).join(", ")}` :
    "No active simulated overrides.";
}

window.applyDemo = async function(kind) {
  try {
    await fetch("/api/demo/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind })
    });
    pollOverview();
    refreshAudit();
  } catch (e) {
    alert(`Demo override error: ${e}`);
  }
};

// ------------------------------------------------------------- Controls & SSE
$("modeToggle").addEventListener("change", async (e) => {
  const mode = e.target.checked ? "policy_autonomous" : "monitoring_only";
  await fetch("/api/policy/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode })
  });
  pollOverview();
});

$("estopBtn").addEventListener("click", async () => {
  const active = $("estopBtn").classList.contains("active");
  await fetch("/api/policy/emergency_stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: !active })
  });
  pollOverview();
});

function initSSE() {
  const sse = new EventSource("/api/events/stream");
  sse.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      appendEvent(ev);
    } catch {}
  };
}

function appendEvent(ev) {
  const c = $("events");
  if (!c) return;
  const d = document.createElement("div");
  d.className = `ev ev-${ev.severity || "info"}`;
  d.innerHTML = `<time>${fmtTime(ev.ts)}</time> <span>${ev.message}</span>`;
  c.prepend(d);
  if (c.children.length > 50) c.lastChild.remove();
}

// ------------------------------------------------------------- Startup
pollOverview();
pollHistory();
pollSensors();
initSSE();

setInterval(pollOverview, 3000);
setInterval(pollHistory, 5000);
setInterval(pollSensors, 10000);
