/* Aegis AI — Production SRE Operations Dashboard
 * Vanilla JS, zero dependencies, high-density telemetry, live SVG sparklines,
 * interactive incident triage & cluster mesh management.
 */
"use strict";

// Resolve API origin: if opened via file:// or without host, default to 127.0.0.1:8000
const API_BASE = (window.location.protocol === "file:" || !window.location.host) ? "http://127.0.0.1:8000" : "";
if (API_BASE) {
  const _origFetch = window.fetch;
  window.fetch = function(resource, init) {
    if (typeof resource === "string" && resource.startsWith("/api/")) {
      resource = API_BASE + resource;
    }
    return _origFetch(resource, init);
  };
}

const $ = (id) => document.getElementById(id);
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "–");
const fmtRelative = (ts) => {
  if (!ts) return "never";
  const diff = Math.round((Date.now() - (typeof ts === "number" ? ts * 1000 : new Date(ts).getTime())) / 1000);
  if (diff < 5) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
};

// Global State
let chartData = { ts: [], cpu_percent: [], mem_percent: [] };
let cpuSparkHistory = [];
let memSparkHistory = [];
let activeIncidentId = null;
let currentEventFilter = "ALL";
let eventsPaused = false;
let dockerFilter = "ALL";
let incidentFilter = "ALL";
let lastKnownHost = window.location.host || "127.0.0.1:8000";

// ------------------------------------------------------------- Toast Feedback
function showToast(msg, kind = "info") {
  const c = $("toastContainer");
  if (!c) return;
  const t = document.createElement("div");
  t.className = "toast";
  const dotColor = kind === "ok" ? "var(--ok)" : kind === "crit" ? "var(--crit)" : "var(--warn)";
  t.innerHTML = `<span class="status-dot" style="background:${dotColor};flex-shrink:0"></span><span>${msg}</span>`;
  c.appendChild(t);
  setTimeout(() => {
    t.classList.add("fade");
    setTimeout(() => t.remove(), 220);
  }, 2800);
}

function copySnippet(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => {
      showToast(`Copied to clipboard: ${text}`, "ok");
    }).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}
window.copySnippet = copySnippet;

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
  showToast(`Copied: ${text}`, "ok");
}

// ------------------------------------------------------------- Navigation & Shortcuts
const TABS = ["overview", "cluster", "models", "infra", "incidents"];

function switchTab(tabName) {
  // Graceful aliases for legacy/external links
  if (tabName === "workloads" || tabName === "hardware") tabName = "infra";
  if (tabName === "policy" || tabName === "engineering" || tabName === "ai") tabName = "incidents";

  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

  const btn = document.querySelector(`.nav-btn[data-tab="${tabName}"]`);
  if (btn) btn.classList.add("active");

  const target = $(`tab-${tabName}`);
  if (target) target.classList.add("active");

  if (tabName === "cluster") refreshCluster();
  if (tabName === "models") refreshModels();
  if (tabName === "infra") { refreshDocker(); pollSensors(); }
  if (tabName === "incidents") { refreshIncidents(); refreshAudit(); }
}
window.switchTab = switchTab;

document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// Keyboard shortcuts
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  if (e.key === "?") {
    e.preventDefault();
    $("cliModal").style.display = "flex";
  } else if (e.key === "Escape") {
    $("cliModal").style.display = "none";
    if ($("logViewerPanel")) $("logViewerPanel").style.display = "none";
    if ($("incidentDetailPanel")) $("incidentDetailPanel").style.display = "none";
  } else if (e.key === "/") {
    e.preventDefault();
    if ($("globalSearch")) $("globalSearch").focus();
  } else if (e.key >= "1" && e.key <= "8") {
    const idx = parseInt(e.key, 10) - 1;
    if (TABS[idx]) switchTab(TABS[idx]);
  }
});

$("cliDocsBtn")?.addEventListener("click", () => { $("cliModal").style.display = "flex"; });
$("closeCliModalBtn")?.addEventListener("click", () => { $("cliModal").style.display = "none"; });

// ------------------------------------------------------------- Live Ping & Health Meter
async function measurePing() {
  const t0 = performance.now();
  try {
    const res = await fetch("/api/health");
    const t1 = performance.now();
    const lat = Math.round(t1 - t0);
    const val = $("pingVal");
    const dot = $("pingDot");
    if (val && dot) {
      if (res.ok) {
        val.textContent = `${lat}ms`;
        dot.className = lat > 150 ? "ping-dot warn" : "ping-dot";
      } else {
        val.textContent = `${res.status}`;
        dot.className = "ping-dot crit";
      }
    }
  } catch {
    const val = $("pingVal");
    const dot = $("pingDot");
    if (val && dot) {
      val.textContent = "offline";
      dot.className = "ping-dot crit";
    }
  }
}

// ------------------------------------------------------------- SVG Sparkline Generator
function updateSparkline(svgId, dataPoints, warnThresh = 70, critThresh = 85) {
  const svg = $(svgId);
  if (!svg) return;
  const path = svg.querySelector("path");
  if (!path || dataPoints.length < 2) return;

  const w = 90, h = 26;
  const n = dataPoints.length;
  const dx = w / (n - 1);

  let d = "";
  for (let i = 0; i < n; i++) {
    const val = Math.max(0, Math.min(100, dataPoints[i] || 0));
    const y = h - (val / 100) * (h - 4) - 2;
    d += (i === 0 ? `M 0,${y.toFixed(1)}` : ` L ${(i * dx).toFixed(1)},${y.toFixed(1)}`);
  }
  path.setAttribute("d", d);

  const lastVal = dataPoints[n - 1] || 0;
  path.className = `sparkline-path ${lastVal >= critThresh ? "crit" : lastVal >= warnThresh ? "warn" : ""}`;
}

// ------------------------------------------------------------- Core Overview Polling
async function pollOverview() {
  await measurePing();
  try {
    const res = await fetch("/api/overview");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const snap = await res.json();
    try { renderHealth(snap); } catch (e) { console.warn("renderHealth error:", e); }
    try { renderCards(snap); } catch (e) { console.warn("renderCards error:", e); }
    try { renderThermal(snap); } catch (e) { console.warn("renderThermal error:", e); }
    try { renderQuickSummaries(snap); } catch (e) { console.warn("renderQuickSummaries error:", e); }
  } catch (err) {
    console.warn("pollOverview fetch error:", err);
    const val = $("pingVal");
    const dot = $("pingDot");
    if (val && dot) {
      val.textContent = "offline";
      dot.className = "ping-dot crit";
    }
  }
}

function renderHealth(snap) {
  const modeTgl = $("modeToggle");
  modeTgl.checked = snap.mode === "policy_autonomous";
  $("modeLabel").textContent = snap.mode === "policy_autonomous" ? "autonomous" : "monitoring";

  const estop = $("estopBtn");
  if (snap.emergency_stop) {
    estop.classList.add("active");
    estop.textContent = "E-STOP ON";
  } else {
    estop.classList.remove("active");
    estop.textContent = "E-STOP";
  }

  // Active Incidents Banner
  const incs = snap.active_incidents || [];
  const b = $("incidentBanner");
  const incTab = $("incTabBadge");
  if (incs.length > 0) {
    b.classList.add("visible");
    $("incidentBannerText").textContent = `${incs.length} active incident${incs.length > 1 ? "s" : ""} require attention`;
    if (incTab) { incTab.style.display = "inline-flex"; incTab.textContent = incs.length; }
  } else {
    b.classList.remove("visible");
    if (incTab) incTab.style.display = "none";
  }
}

function renderCards(snap) {
  const l = snap.latest || {};
  const mem = l.memory || {};
  const host = snap.host || {};

  // CPU
  const cpuPct = l.cpu_percent !== undefined ? l.cpu_percent : 0;
  $("cpuVal").textContent = `${cpuPct.toFixed(1)}%`;
  $("cpuVal").className = `metric-val ${cpuPct > 85 ? "crit" : cpuPct > 70 ? "warn" : "ok"}`;
  $("cpuSub").textContent = `${host.cpu_cores || 1} cores · load ${l.load_avg_1 ?? "–"}`;

  cpuSparkHistory.push(cpuPct);
  if (cpuSparkHistory.length > 25) cpuSparkHistory.shift();
  updateSparkline("spark-cpu", cpuSparkHistory, 70, 85);

  // Memory
  const memPct = mem.percent !== undefined ? mem.percent : 0;
  $("memVal").textContent = `${memPct.toFixed(1)}%`;
  $("memVal").className = `metric-val ${memPct > 90 ? "crit" : memPct > 75 ? "warn" : "ok"}`;
  $("memSub").textContent = `${mem.used_mb ?? "–"} / ${mem.total_mb ?? "–"} MB`;

  memSparkHistory.push(memPct);
  if (memSparkHistory.length > 25) memSparkHistory.shift();
  updateSparkline("spark-mem", memSparkHistory, 75, 90);

  // Storage
  const disks = l.disks || [];
  const maxDisk = disks.reduce((acc, d) => Math.max(acc, d.used_pct || 0), 0);
  $("diskVal").textContent = disks.length ? `${maxDisk.toFixed(0)}%` : "–";
  $("diskVal").className = `metric-val ${maxDisk > 90 ? "crit" : maxDisk > 75 ? "warn" : "ok"}`;
  $("diskSub").textContent = `${disks.length} partition${disks.length !== 1 ? "s" : ""}`;

  // Network
  const io = l.io_rates || {};
  const rxKb = io.recv_bytes_s ? (io.recv_bytes_s / 1024).toFixed(0) : 0;
  const txKb = io.sent_bytes_s ? (io.sent_bytes_s / 1024).toFixed(0) : 0;
  $("netVal").textContent = `${rxKb} KB/s`;
  $("netSub").textContent = `TX ${txKb} · RX ${rxKb} KB/s`;

  // GPU
  const gpu = snap.gpu || {};
  const devs = gpu.devices || [];
  if (devs.length > 0) {
    const g0 = devs[0];
    $("gpuVal").textContent = `${g0.utilization_pct || 0}%`;
    $("gpuVal").className = "metric-val ok";
    $("gpuSub").textContent = `${devs.length}x ${g0.model || "GPU"} · ${g0.temperature_c || 0}°C`;
  } else {
    $("gpuVal").textContent = "–";
    $("gpuVal").className = "metric-val muted";
    $("gpuSub").textContent = "No GPU detected";
  }
}

function renderThermal(snap) {
  const t = snap.thermal || {};
  const st = t.state || "NOMINAL";
  $("thermalVal").textContent = `${t.highest_c ? t.highest_c.toFixed(1) + "°C" : "–"}`;
  $("thermalVal").className = `metric-val ${st === "CRITICAL" ? "crit" : st === "WARNING" ? "warn" : "ok"}`;
  const r = t.rate_of_rise_c_per_s !== undefined ? `${t.rate_of_rise_c_per_s >= 0 ? "+" : ""}${t.rate_of_rise_c_per_s.toFixed(2)}°C/s` : "0.0°C/s";
  $("thermalSub").textContent = `${st} · ${r}`;
}

function renderQuickSummaries(snap) {
  const c = snap.docker || {};
  const containers = c.containers || [];
  const quickW = $("quickWorkloads");
  if (containers.length) {
    quickW.innerHTML = `
      <table class="tbl">
        <thead><tr><th>Container</th><th>Image</th><th>Status</th><th>Memory</th></tr></thead>
        <tbody>
          ${containers.slice(0, 4).map(cnt => `
            <tr>
              <td><strong>${cnt.name}</strong></td>
              <td><code>${(cnt.image || "").substring(0, 20)}</code></td>
              <td><span class="badge ${cnt.status === 'running' ? 'ok' : 'crit'}">${cnt.status}</span></td>
              <td>${cnt.metrics ? cnt.metrics.memory_usage_mb + " MB" : "–"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  } else {
    quickW.innerHTML = `<div style="font-size:0.8rem; color:var(--muted); padding:8px 0;">No active containers detected.</div>`;
  }

  const actions = snap.actions || [];
  const quickA = $("quickActions");
  if (actions.length) {
    quickA.innerHTML = `
      <table class="tbl">
        <thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Result</th></tr></thead>
        <tbody>
          ${actions.slice(0, 4).map(a => `
            <tr>
              <td>${fmtTime(a.ts)}</td>
              <td><strong>${a.action}</strong></td>
              <td>${a.target || "host"}</td>
              <td><span class="badge ok">${a.result || "executed"}</span></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  } else {
    quickA.innerHTML = `<div style="font-size:0.8rem; color:var(--muted); padding:8px 0;">No recent autonomous interventions recorded.</div>`;
  }
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

  plot(pts, "#3b82f6");
  plot(memPts, "#10b981");
}

// ------------------------------------------------------------- Cluster Mesh (v2.0)
async function refreshCluster() {
  try {
    const summary = await (await fetch("/api/cluster/summary")).json();
    const vitals = $("clusterVitals");
    if (vitals) {
      vitals.textContent = `${summary.online_nodes}/${summary.total_nodes} online · ${summary.total_cluster_cpu_cores} cores · ${summary.total_cluster_ram_gb} GB RAM`;
    }

    const tabBadge = $("clusterTabBadge");
    if (tabBadge && summary.total_nodes > 0) {
      tabBadge.style.display = "inline-flex";
      tabBadge.textContent = summary.online_nodes;
    }

    const nodes = await (await fetch("/api/cluster/nodes")).json();
    const tb = $("clusterNodesTbl").querySelector("tbody");
    if (!nodes.length) {
      tb.innerHTML = `<tr><td colspan="7" style="color:var(--muted); text-align:center; padding:16px;">No remote nodes registered. Run <code>aegis-agent</code> on worker nodes to join the mesh.</td></tr>`;
    } else {
      tb.innerHTML = nodes.map(n => {
        const isOnline = n.status === "ONLINE";
        const badgeClass = isOnline ? "ok" : n.status === "DRAINED" ? "info" : "crit";
        const t = n.telemetry_summary || {};
        const cpu = t.cpu_percent !== undefined ? t.cpu_percent : (n.cpu_percent || 0);
        const ram = t.ram_percent !== undefined ? t.ram_percent : (n.ram_percent || 0);
        return `
        <tr>
              <td><code class="mono">${n.node_id}</code></td>
              <td><strong>${n.hostname}</strong></td>
              <td class="mono dim">${n.ip_address || "–"}</td>
              <td><span class="badge ${badgeClass}">${n.status}</span></td>
              <td>
                <div class="progress-wrap">
                  <span class="text-xs text-muted" style="width:28px">CPU</span>
                  <div class="progress-bar"><div class="progress-fill ${cpu > 80 ? 'crit' : 'ok'}" style="width:${cpu}%"></div></div>
                  <span class="progress-pct">${cpu.toFixed(0)}%</span>
                </div>
                <div class="progress-wrap mt-8" style="margin-top:4px">
                  <span class="text-xs text-muted" style="width:28px">RAM</span>
                  <div class="progress-bar"><div class="progress-fill ${ram > 85 ? 'crit' : 'ok'}" style="width:${ram}%"></div></div>
                  <span class="progress-pct">${ram.toFixed(0)}%</span>
                </div>
              </td>
              <td class="dim">${fmtRelative(n.last_heartbeat_at)}</td>
              <td>
                ${isOnline ? `<button class="btn xs danger" onclick="drainNode('${n.node_id}')">Drain</button>` :
                  `<div class="flex gap-4 items-center">
                    <span class="text-xs text-muted">${n.status}</span>
                    <button class="btn xs" style="color:var(--crit);border-color:var(--border)" onclick="removeNode('${n.node_id}')">Remove</button>
                  </div>`}
              </td>
            </tr>
        `;
      }).join("");
    }

    // Canary rollouts
    const rollouts = await (await fetch("/api/canary/rollouts")).json();
    const ctb = $("canaryTbl").querySelector("tbody");
    if (!rollouts.length) {
      ctb.innerHTML = `<tr><td colspan="7" style="color:var(--muted); text-align:center; padding:14px;">No active canary rollouts.</td></tr>`;
    } else {
      ctb.innerHTML = rollouts.map(r => {
        const bClass = r.state === "PROMOTED" ? "ok" : r.state === "ROLLED_BACK" ? "crit" : "warn";
        const pct = r.total_steps ? Math.round((r.current_step / r.total_steps) * 100) : 0;
        return `
          <tr>
            <td><code>${r.rollout_id}</code></td>
            <td><strong>${r.target}</strong></td>
            <td><span class="badge ${bClass}">${r.state}</span></td>
            <td>
              <div class="progress-container">
                <div class="progress-bar"><div class="progress-fill info" style="width:${pct}%"></div></div>
                <span class="progress-val">${r.current_step}/${r.total_steps}</span>
              </div>
            </td>
            <td>${(r.error_rate * 100).toFixed(1)}%</td>
            <td>${r.latency_ms.toFixed(0)} ms</td>
            <td>
              ${r.state === "CANARY_OBSERVING" || r.state === "CANARY_INIT" ?
                `<button class="btn sm danger" onclick="rollbackCanary('${r.rollout_id}')">Rollback</button>` :
                `<span style="color:var(--muted); font-size:0.75rem;">${r.state}</span>`}
            </td>
          </tr>
        `;
      }).join("");
    }
  } catch {}
}

async function drainNode(nodeId) {
  if (!confirm(`Are you sure you want to cordon and drain node '${nodeId}'?`)) return;
  try {
    const res = await (await fetch(`/api/cluster/nodes/${nodeId}/drain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Operator dashboard drain" })
    })).json();
    showToast(`Drained node ${nodeId}`, "ok");
    refreshCluster();
  } catch (e) {
    showToast(`Drain error: ${e}`, "crit");
  }
}
window.drainNode = drainNode;

async function removeNode(nodeId) {
  if (!confirm(`Remove node '${nodeId}' from cluster mesh?`)) return;
  try {
    await fetch(`/api/cluster/nodes/${nodeId}`, { method: "DELETE" });
    showToast(`Node ${nodeId} removed`, "ok");
    refreshCluster();
  } catch (e) {
    showToast(`Error removing node: ${e}`, "crit");
  }
}
window.removeNode = removeNode;

async function rollbackCanary(rolloutId) {
  if (!confirm(`Trigger emergency rollback for ${rolloutId}?`)) return;
  try {
    await fetch(`/api/canary/rollouts/${rolloutId}/rollback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Operator manual rollback" })
    });
    showToast(`Rollback initiated for ${rolloutId}`, "warn");
    refreshCluster();
  } catch (e) {
    showToast(`Rollback error: ${e}`, "crit");
  }
}
window.rollbackCanary = rollbackCanary;

function setupClusterJoinCommands() {
  const hostInput = $("clusterHostInput");
  if (!hostInput) return;
  const currentHost = window.location.host || "127.0.0.1:8000";
  if (!hostInput.value) hostInput.value = currentHost;

  const update = () => {
    const h = hostInput.value.trim() || currentHost;
    const origin = h.startsWith("http") ? h : `http://${h}`;
    const cleanHost = h.replace(/^https?:\/\//, "");

    const ir = $("cmdInstantRun");
    if (ir) ir.textContent = `curl -sSL ${origin}/agent.py | python3 - ${cleanHost}`;

    const si = $("cmdServiceInstall");
    if (si) si.textContent = `curl -sSL ${origin}/install.sh | bash -s ${cleanHost}`;

    const cj = $("cmdCliJoin");
    if (cj) cj.textContent = `aegis join ${cleanHost}`;
  };

  hostInput.addEventListener("input", update);
  update();
}

const rcb = $("refreshClusterBtn");
if (rcb) rcb.addEventListener("click", refreshCluster);

// ------------------------------------------------------------- AI Model Hub & Deployer (v3.0)
let currentModelTaskFilter = "ALL";

async function refreshModels() {
  try {
    const taskParam = currentModelTaskFilter !== "ALL" ? `?task=${currentModelTaskFilter}` : "";
    const res = await (await fetch(`/api/models/recommendations${taskParam}`)).json();
    const prof = res.hardware_profile || {};

    // 1. Hardware profile banner
    const gpus = (prof.gpu_names || []).join(", ") || "No discrete GPU (CPU mode)";
    $("aiHardwareProfileBox").innerHTML = `
      <div class="hw-field">
        <div class="hw-field-label">Acceleration</div>
        <div class="hw-field-val">${prof.has_gpu ? `<span class="badge ok">GPU</span>` : `<span class="badge warn">CPU</span>`}</div>
      </div>
      <div class="hw-field">
        <div class="hw-field-label">VRAM</div>
        <div class="hw-field-val">${prof.total_vram_gb} GB</div>
      </div>
      <div class="hw-field">
        <div class="hw-field-label">System RAM</div>
        <div class="hw-field-val">${prof.total_ram_gb} GB</div>
      </div>
      <div class="hw-field">
        <div class="hw-field-label">CPU Cores</div>
        <div class="hw-field-val">${prof.cpu_cores}</div>
      </div>
      <div class="hw-field">
        <div class="hw-field-label">GPU</div>
        <div class="hw-field-val" style="font-size:0.75rem;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${gpus}</div>
      </div>
    `;

    // 2. Best Models List
    const best = res.best_models || [];
    $("bestModelsList").innerHTML = best.length ? best.map(m => {
      const s = m.model;
      return `
        <div class="model-card ok">
          <div class="flex items-center justify-between">
            <div class="model-name">${s.display_name}</div>
            <span class="badge ok">${m.fit_score}/100</span>
          </div>
          <div class="model-meta">
            <span class="badge ghost">${s.task}</span>
            <span>${s.parameters_b}B params</span>
            <span><code>${s.quantization}</code></span>
            <span>~${m.estimated_tokens_per_sec} t/s</span>
          </div>
          <div class="text-xs text-muted">
            ${(m.reasons || []).map(r => `<div>${r}</div>`).join("")}
          </div>
          <div class="model-actions">
            <button class="btn xs primary" onclick="deployModel('${s.name}', 'docker')">Docker</button>
            <button class="btn xs" onclick="deployModel('${s.name}', 'k8s')">K8s</button>
            <button class="btn xs" onclick="runBenchmark('${s.name}')">Benchmark</button>
            <button class="btn xs" onclick="viewK8sManifest('${s.name}')">View YAML</button>
          </div>
        </div>
      `;
    }).join("") : `<div class="text-muted text-sm" style="padding:12px">No optimal models for this hardware.</div>`;

    // 3. Marginal Models
    const marginal = res.marginal_models || [];
    $("marginalModelsList").innerHTML = marginal.length ? marginal.map(m => {
      const s = m.model;
      return `
        <div class="model-card warn" style="flex-direction:row;align-items:center;justify-content:space-between;gap:10px">
          <div>
            <div class="model-name">${s.display_name}</div>
            <div class="text-xs text-muted">${s.vram_required_gb} GB · ~${m.offload_layers_percent}% on GPU</div>
            <div class="text-xs" style="color:var(--warn)">${(m.warnings || []).join(" ")}</div>
          </div>
          <div class="flex gap-8" style="gap:5px;flex-shrink:0">
            <button class="btn xs" onclick="deployModel('${s.name}', 'docker')">Docker</button>
            <button class="btn xs" onclick="deployModel('${s.name}', 'k8s')">K8s</button>
          </div>
        </div>
      `;
    }).join("") : `<div class="text-xs text-muted" style="padding:8px">None</div>`;

    // 4. Worst Models
    const worst = res.worst_models || [];
    $("worstModelsList").innerHTML = worst.length ? worst.map(m => {
      const s = m.model;
      return `
        <div class="model-card crit" style="flex-direction:row;align-items:center;justify-content:space-between;gap:10px">
          <div>
            <div class="model-name">${s.display_name}</div>
            <div class="text-xs" style="color:var(--crit)">${(m.warnings || []).join(" ")}</div>
          </div>
          <span class="badge crit">${m.fit_tier}</span>
        </div>
      `;
    }).join("") : `<div class="text-xs text-muted" style="padding:8px">None</div>`;

    // 5. Active Deployments
    const deps = await (await fetch("/api/models/deployments")).json();
    const dtb = $("deploymentsTbl").querySelector("tbody");
    if (!deps.length) {
      dtb.innerHTML = `<tr><td colspan="7" style="color:var(--muted); text-align:center; padding:14px;">No active deployments. Click "1-Click Docker" or "1-Click K8s" on any model above to launch.</td></tr>`;
    } else {
      dtb.innerHTML = deps.map(d => {
        const isRunning = d.status === "RUNNING";
        const bClass = isRunning ? "ok" : d.status === "CONFIGURED" ? "info" : "crit";
        return `
          <tr>
            <td><code>${d.deployment_id}</code></td>
            <td><strong>${d.model_name}</strong></td>
            <td><span class="badge info">${d.runtime.toUpperCase()}</span></td>
            <td><span class="badge ${bClass}">${d.status}</span></td>
            <td>:${d.port}</td>
            <td><code>${(d.target || "").substring(0, 32)}</code></td>
            <td>
              ${isRunning ? `<button class="btn sm danger" onclick="stopDeployment('${d.deployment_id}')">Stop</button>` : `<span style="font-size:0.75rem; color:var(--muted);">${d.status}</span>`}
            </td>
          </tr>
        `;
      }).join("");
    }

    // 6. Empirical Benchmarks (v3.1)
    await refreshBenchmarks();
  } catch {}
}

async function refreshBenchmarks() {
  try {
    const benches = await (await fetch("/api/models/benchmarks")).json();
    const btb = $("benchmarksTbl")?.querySelector("tbody");
    if (!btb) return;
    if (!benches.length) {
      btb.innerHTML = `<tr><td colspan="8" style="color:var(--muted); text-align:center; padding:12px;">No benchmarks executed yet. Click "⚡ Benchmark" on any model above to run live empirical testing.</td></tr>`;
    } else {
      btb.innerHTML = benches.map(b => {
        const stClass = b.status === "COMPLETED" ? "ok" : b.status === "SIMULATED" ? "info" : "warn";
        return `
          <tr>
            <td><code>${b.benchmark_id}</code></td>
            <td><strong>${b.model_name}</strong></td>
            <td><code>${b.runtime}</code></td>
            <td><strong style="color:var(--text-bright);">${b.tokens_per_sec} t/s</strong></td>
            <td>${b.ttft_ms} ms</td>
            <td>${b.prompt_eval_tps || "--"} t/s</td>
            <td><span class="badge ${stClass}">${b.status}</span></td>
            <td><span class="time-ago">${b.created_at ? b.created_at.substring(11, 19) : ""}</span></td>
          </tr>
        `;
      }).join("");
    }
  } catch {}
}

async function runBenchmark(modelName) {
  showToast(`Running inference benchmark for ${modelName}...`, "info");
  try {
    const res = await (await fetch("/api/models/benchmark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: modelName })
    })).json();
    showToast(`Benchmark complete: ${res.tokens_per_sec} tokens/s (TTFT: ${res.ttft_ms}ms)`, "ok");
    await refreshBenchmarks();
  } catch (e) {
    showToast(`Benchmark error: ${e}`, "crit");
  }
}
window.runBenchmark = runBenchmark;

async function optimizeVram() {
  showToast("Optimizing AI model memory allocation...", "info");
  try {
    const res = await (await fetch("/api/models/optimize", { method: "POST" })).json();
    showToast(`Reclaimed ${res.reclaimed_vram_mb} MB VRAM (${res.unloaded_models.length} idle models unloaded)`, "ok");
  } catch (e) {
    showToast(`Optimize error: ${e}`, "crit");
  }
}
window.optimizeVram = optimizeVram;

async function openClusterCapacityModal() {
  try {
    const cap = await (await fetch("/api/models/cluster-capacity")).json();
    const hw = cap.concurrent_model_headroom || {};
    const nodes = cap.node_placements || [];

    $("clusterCapacityContent").innerHTML = `
      <div style="background:#090d16; border:1px solid var(--line); border-radius:6px; padding:14px; margin-bottom:14px;">
        <div style="display:flex; justify-content:space-between; margin-bottom:10px;">
          <div>
            <div style="font-size:0.75rem; color:var(--muted);">TOTAL FLEET VRAM</div>
            <div style="font-size:1.4rem; font-weight:700; color:var(--primary);">${cap.total_cluster_vram_gb} GB</div>
          </div>
          <div>
            <div style="font-size:0.75rem; color:var(--muted);">TOTAL FLEET RAM</div>
            <div style="font-size:1.4rem; font-weight:700; color:var(--text-bright);">${cap.total_cluster_ram_gb} GB</div>
          </div>
          <div>
            <div style="font-size:0.75rem; color:var(--muted);">ONLINE NODES</div>
            <div style="font-size:1.4rem; font-weight:700; color:var(--ok);">${cap.online_nodes}</div>
          </div>
        </div>
        <div style="font-size:0.8rem; color:var(--dim);">
          <strong>Placement Strategy:</strong> ${cap.recommended_strategy}
        </div>
      </div>

      <h3 style="font-size:0.9rem; margin-bottom:8px;">Concurrent Model Headroom</h3>
      <div class="cards" style="grid-template-columns:repeat(4, 1fr); margin-bottom:14px;">
        <div class="card" style="padding:10px; text-align:center;">
          <div style="font-size:0.75rem; color:var(--muted);">7B Models</div>
          <div style="font-size:1.3rem; font-weight:700; color:var(--ok);">${hw["7b_models"] || 0}</div>
        </div>
        <div class="card" style="padding:10px; text-align:center;">
          <div style="font-size:0.75rem; color:var(--muted);">14B Models</div>
          <div style="font-size:1.3rem; font-weight:700; color:var(--info);">${hw["14b_models"] || 0}</div>
        </div>
        <div class="card" style="padding:10px; text-align:center;">
          <div style="font-size:0.75rem; color:var(--muted);">32B Models</div>
          <div style="font-size:1.3rem; font-weight:700; color:var(--warn);">${hw["32b_models"] || 0}</div>
        </div>
        <div class="card" style="padding:10px; text-align:center;">
          <div style="font-size:0.75rem; color:var(--muted);">70B Frontier</div>
          <div style="font-size:1.3rem; font-weight:700; color:var(--crit);">${hw["70b_models"] || 0}</div>
        </div>
      </div>

      <h3 style="font-size:0.9rem; margin-bottom:8px;">Node-by-Node Recommendations</h3>
      <div style="display:flex; flex-direction:column; gap:8px;">
        ${nodes.map(n => `
          <div style="background:var(--card); border:1px solid var(--line); border-radius:5px; padding:10px 12px; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <strong>${n.node_id}</strong> (${n.hostname}) · <code>${n.ip_address}</code>
              <div style="font-size:0.75rem; color:var(--muted);">VRAM: ${n.vram_gb} GB | RAM: ${n.ram_gb} GB</div>
            </div>
            <div>
              ${(n.suitable_models || []).map(sm => `<span class="badge info" style="margin-left:4px;">${sm}</span>`).join("")}
            </div>
          </div>
        `).join("")}
      </div>
    `;
    $("clusterCapacityModal").style.display = "flex";
  } catch (e) {
    showToast(`Error fetching cluster capacity: ${e}`, "crit");
  }
}
window.openClusterCapacityModal = openClusterCapacityModal;

async function deployModel(modelName, runtime = "docker") {
  showToast(`Deploying ${modelName} to ${runtime}...`, "info");
  try {
    const res = await (await fetch("/api/models/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_name: modelName, runtime: runtime })
    })).json();
    showToast(`Successfully deployed ${modelName} (${res.status})`, "ok");
    refreshModels();
  } catch (e) {
    showToast(`Deployment error: ${e}`, "crit");
  }
}
window.deployModel = deployModel;

async function viewK8sManifest(modelName) {
  try {
    const res = await (await fetch(`/api/orchestration/manifests/k8s?model_name=${modelName}`)).json();
    $("manifestModalTitle").textContent = `Kubernetes Manifest: ${modelName}`;
    $("manifestModalContent").textContent = res.manifest_yaml;
    $("manifestModal").style.display = "flex";
  } catch (e) {
    showToast(`Error generating manifest: ${e}`, "crit");
  }
}
window.viewK8sManifest = viewK8sManifest;

async function stopDeployment(depId) {
  if (!confirm(`Stop deployment ${depId}?`)) return;
  try {
    await fetch(`/api/models/deployments/${depId}/stop`, { method: "POST" });
    showToast(`Stopped ${depId}`, "warn");
    refreshModels();
  } catch (e) {
    showToast(`Error stopping: ${e}`, "crit");
  }
}
window.stopDeployment = stopDeployment;

$("closeManifestModalBtn")?.addEventListener("click", () => { $("manifestModal").style.display = "none"; });
$("copyManifestBtn")?.addEventListener("click", () => { copySnippet($("manifestModalContent").textContent); });
$("refreshModelsBtn")?.addEventListener("click", refreshModels);
$("optimizeVramBtn")?.addEventListener("click", optimizeVram);
$("clusterCapacityBtn")?.addEventListener("click", openClusterCapacityModal);
$("closeClusterCapacityModalBtn")?.addEventListener("click", () => { $("clusterCapacityModal").style.display = "none"; });

document.querySelectorAll("#modelTaskFilterGroup .filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#modelTaskFilterGroup .filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentModelTaskFilter = btn.dataset.filter;
    refreshModels();
  });
});

// ------------------------------------------------------------- Docker & Workloads Tab
async function refreshDocker() {
  try {
    const d = await (await fetch("/api/workloads/docker")).json();
    const tb = $("dockerTbl").querySelector("tbody");
    const term = ($("dockerSearch")?.value || "").toLowerCase();

    let list = d.containers || [];
    if (dockerFilter === "RUNNING") list = list.filter(c => c.status === "running");
    if (dockerFilter === "CRASH") list = list.filter(c => c.status !== "running" || c.restart_count > 3);
    if (term) list = list.filter(c => c.name.toLowerCase().includes(term) || c.image.toLowerCase().includes(term));

    if (!list.length) {
      tb.innerHTML = `<tr><td colspan="8" style="color:var(--muted); text-align:center; padding:16px;">${d.available ? "No containers matching filter." : d.reason || "Docker daemon unavailable."}</td></tr>`;
    } else {
      tb.innerHTML = list.map(c => {
        const m = c.metrics || {};
        const isRunning = c.status === "running";
        const memMb = m.memory_usage_mb || 0;
        const cpuPct = m.cpu_percent !== undefined ? m.cpu_percent : 0;
        return `
          <tr>
            <td><strong>${c.name}</strong></td>
            <td><code>${c.image.substring(0, 24)}</code></td>
            <td><span class="badge ${isRunning ? 'ok' : 'crit'}">${c.status}</span></td>
            <td>${c.health || "–"}</td>
            <td>${c.restart_count > 3 ? `<span class="badge crit">${c.restart_count}</span>` : c.restart_count}</td>
            <td>${memMb ? `${memMb} MB` : "–"}</td>
            <td>${cpuPct ? `${cpuPct}%` : "–"}</td>
            <td>
              <button class="btn sm" onclick="viewContainerLogs('${c.id}', '${c.name}')">Logs</button>
              <button class="btn sm danger" onclick="restartContainer('${c.id}')">Restart</button>
            </td>
          </tr>
        `;
      }).join("");
    }

    const k = await (await fetch("/api/workloads/k8s")).json();
    $("k8sOverview").innerHTML = k.available ?
      `<table class="tbl"><thead><tr><th>Node</th><th>Status</th><th>Kubelet</th></tr></thead><tbody>${k.nodes.map(n => `<tr><td><strong>${n.name}</strong></td><td><span class="badge ok">${n.status}</span></td><td>${n.kubelet_version}</td></tr>`).join("")}</tbody></table>` :
      `<div style="color:var(--muted); font-size:0.8rem; padding:10px;">${k.reason}</div>`;

    $("k8sPods").innerHTML = k.available ?
      `<table class="tbl"><thead><tr><th>Pod</th><th>Namespace</th><th>Status</th><th>Restarts</th></tr></thead><tbody>${k.pods.map(p => `<tr><td>${p.name}</td><td>${p.namespace}</td><td><span class="badge ${p.status === 'Running' ? 'ok' : 'crit'}">${p.status}</span></td><td>${p.restart_count}</td></tr>`).join("")}</tbody></table>` :
      `<div style="color:var(--muted); font-size:0.8rem; padding:10px;">No Kubernetes cluster configured in ~/.kube/config.</div>`;
  } catch {}
}

async function viewContainerLogs(cid, name) {
  try {
    const res = await (await fetch(`/api/workloads/docker/${cid}/logs?tail=100`)).json();
    $("logViewerTitle").textContent = `Logs: ${name}`;
    $("logViewerContent").textContent = res.logs || "No logs produced.";
    $("logViewerPanel").style.display = "block";
    $("logViewerPanel").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    showToast(`Error fetching logs: ${e}`, "crit");
  }
}
window.viewContainerLogs = viewContainerLogs;

async function restartContainer(cid) {
  if (!confirm("Authorize policy restart for container?")) return;
  try {
    const res = await (await fetch(`/api/workloads/docker/${cid}/restart`, { method: "POST" })).json();
    showToast(`Restart result: ${res.result}`, res.result === "success" ? "ok" : "warn");
    refreshDocker();
  } catch (e) {
    showToast(`Error restarting container: ${e}`, "crit");
  }
}
window.restartContainer = restartContainer;

$("closeLogsBtn")?.addEventListener("click", () => { $("logViewerPanel").style.display = "none"; });
$("copyLogsBtn")?.addEventListener("click", () => { copySnippet($("logViewerContent").textContent); });
$("refreshDockerBtn")?.addEventListener("click", refreshDocker);
$("dockerSearch")?.addEventListener("input", refreshDocker);

document.querySelectorAll("#dockerFilterGroup .filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#dockerFilterGroup .filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    dockerFilter = btn.dataset.filter;
    refreshDocker();
  });
});

// ------------------------------------------------------------- Incidents Tab
async function refreshIncidents() {
  try {
    const list = await (await fetch("/api/incidents")).json();
    const tb = $("incidentsTbl").querySelector("tbody");

    let filtered = list;
    if (incidentFilter === "ACTIVE") filtered = list.filter(i => i.state !== "RESOLVED" && i.state !== "RECOVERY_VERIFIED");
    if (incidentFilter === "CRIT") filtered = list.filter(i => (i.severity || "").toLowerCase() === "critical");
    if (incidentFilter === "APPROVAL") filtered = list.filter(i => i.state === "NEEDS_HUMAN_APPROVAL");
    if (incidentFilter === "RESOLVED") filtered = list.filter(i => i.state === "RESOLVED" || i.state === "RECOVERY_VERIFIED");

    if (!filtered.length) {
      tb.innerHTML = `<tr><td colspan="7" style="color:var(--muted); text-align:center; padding:16px;">No incidents matching filter. Infrastructure nominal.</td></tr>`;
      return;
    }

    tb.innerHTML = filtered.map(inc => {
      const isCrit = (inc.severity || "").toLowerCase() === "critical";
      const isResolved = inc.state === "RESOLVED" || inc.state === "RECOVERY_VERIFIED";
      const stateBadge = isResolved ? "ok" : inc.state === "NEEDS_HUMAN_APPROVAL" ? "warn" : "info";
      return `
        <tr>
          <td><code>${inc.id}</code></td>
          <td><span class="badge ${isCrit ? 'crit' : 'warn'}">${inc.severity}</span></td>
          <td>${inc.category}</td>
          <td><strong>${inc.title}</strong></td>
          <td><span class="badge ${stateBadge}">${inc.state}</span></td>
          <td>${fmtTime(inc.created_at)}</td>
          <td><button class="btn sm" onclick="selectIncident('${inc.id}')">Inspect</button></td>
        </tr>
      `;
    }).join("");
  } catch {}
}

async function selectIncident(iid) {
  activeIncidentId = iid;
  try {
    const list = await (await fetch("/api/incidents")).json();
    const inc = list.find(x => x.id === iid);
    if (!inc) return;

    $("incDetailTitle").textContent = `Incident ${inc.id} — ${inc.title}`;
    $("incDetailContent").innerHTML = `
      <div style="margin-bottom:8px;"><strong>Severity:</strong> <span class="badge ${inc.severity === 'critical' ? 'crit' : 'warn'}">${inc.severity}</span> | <strong>State:</strong> <span class="badge info">${inc.state}</span></div>
      <p><strong>Summary:</strong> ${inc.summary || "–"}</p>
      <div style="font-size:0.8rem; margin:10px 0;"><strong>State Transitions:</strong></div>
      <div class="events-console" style="height:120px; margin-bottom:12px;">
        ${(inc.lifecycle || []).map(l => `<div class="ev-line info"><time>${fmtTime(l.ts)}</time> <strong>${l.state}</strong> (${l.actor || "engine"}): ${l.note || ""}</div>`).join("")}
      </div>
      <div style="font-size:0.8rem; margin-bottom:4px;"><strong>Collected Telemetry Evidence:</strong></div>
      <pre class="log-box" style="max-height:160px;">${JSON.stringify(inc.evidence || [], null, 2)}</pre>
    `;
    $("incidentDetailPanel").style.display = "block";
    $("incidentDetailPanel").scrollIntoView({ behavior: "smooth" });
  } catch {}
}
window.selectIncident = selectIncident;

$("copyIncCliBtn")?.addEventListener("click", () => {
  if (activeIncidentId) copySnippet(`aegis incidents diagnose ${activeIncidentId}`);
});
$("closeIncDetailBtn")?.addEventListener("click", () => { $("incidentDetailPanel").style.display = "none"; });
$("refreshIncidentsBtn")?.addEventListener("click", refreshIncidents);

document.querySelectorAll("#incFilterGroup .filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#incFilterGroup .filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    incidentFilter = btn.dataset.filter;
    refreshIncidents();
  });
});

$("runDiagBtn")?.addEventListener("click", async () => {
  if (!activeIncidentId) return;
  $("runDiagBtn").textContent = "Diagnosing…";
  try {
    const res = await (await fetch(`/api/incidents/${activeIncidentId}/diagnose`, { method: "POST" })).json();
    showToast(`Diagnosis (${res.provider}): ${res.root_cause_hypothesis}`, "ok");
    selectIncident(activeIncidentId);
  } catch (e) {
    showToast(`Diagnosis error: ${e}`, "crit");
  } finally {
    $("runDiagBtn").textContent = "Diagnose with AI";
  }
});

// ------------------------------------------------------------- Hardware & Forecasting Tab
async function pollSensors() {
  try {
    const sensors = await (await fetch("/api/sensors")).json();
    const sensorTbl = $("sensorTbl")?.querySelector("tbody");
    if (sensorTbl) {
      sensorTbl.innerHTML = sensors.map(s => {
        const l = s.latest || {};
        const val = l.value !== null && l.value !== undefined ? `${l.value.toFixed(1)} ${s.unit || ""}` : "–";
        const bClass = l.validity === "VALID" ? "ok" : l.validity === "STALE" ? "warn" : "crit";
        return `<tr><td><code>${s.sensor_id}</code></td><td>${s.kind}</td><td><strong>${val}</strong></td><td><span class="badge ${bClass}">${l.validity}</span></td></tr>`;
      }).join("");
    }

    const storage = await (await fetch("/api/system/storage")).json();
    const storageTbl = $("storageTbl")?.querySelector("tbody");
    if (storageTbl) {
      storageTbl.innerHTML = (storage.partitions || []).map(p => `
        <tr>
          <td><strong>${p.mount}</strong></td>
          <td><code>${p.device}</code></td>
          <td>${p.used_gb} / ${p.total_gb} GB</td>
          <td>
            <div class="progress-wrap">
              <div class="progress-bar"><div class="progress-fill ${p.used_pct > 85 ? 'crit' : 'ok'}" style="width:${p.used_pct}%"></div></div>
              <span class="progress-pct">${p.used_pct}%</span>
            </div>
          </td>
        </tr>
      `).join("");
    }

    const inv = await (await fetch("/api/system/inventory")).json();
    const hw = inv.hardware || {};
    const sw = inv.software || {};
    if ($("inventoryContainer")) {
      $("inventoryContainer").innerHTML = `
        <table class="tbl">
          <tbody>
            <tr><td style="color:var(--text-muted); width:160px;">OS &amp; Kernel</td><td><strong>${sw.os_name}</strong> (${sw.architecture}) · Kernel ${sw.kernel}</td></tr>
            <tr><td style="color:var(--text-muted);">CPU Processor</td><td>${hw.cpu?.model || "Unknown"} (${hw.cpu?.logical_cores || 1} Cores)</td></tr>
            <tr><td style="color:var(--text-muted);">System Memory</td><td>${hw.memory?.total_mb || 0} MB RAM · ${hw.memory?.swap_total_mb || 0} MB Swap</td></tr>
            <tr><td style="color:var(--text-muted);">Python Runtime</td><td>Python ${sw.python_version || "3.x"}</td></tr>
          </tbody>
        </table>
      `;
    }

    // PSI Pressure Stall Information
    const snap = await (await fetch("/api/overview")).json();
    const psi = snap.latest?.pressure || {};
    if ($("psiContainer")) {
      $("psiContainer").innerHTML = `
        <table class="tbl">
          <thead><tr><th>Resource</th><th>10s Avg</th><th>60s Avg</th><th>300s Avg</th></tr></thead>
          <tbody>
            <tr><td><strong>CPU Pressure</strong></td><td>${psi.cpu?.some_avg10 || 0}%</td><td>${psi.cpu?.some_avg60 || 0}%</td><td>${psi.cpu?.some_avg300 || 0}%</td></tr>
            <tr><td><strong>Memory Pressure</strong></td><td>${psi.memory?.some_avg10 || 0}%</td><td>${psi.memory?.some_avg60 || 0}%</td><td>${psi.memory?.some_avg300 || 0}%</td></tr>
            <tr><td><strong>I/O Stall</strong></td><td>${psi.io?.some_avg10 || 0}%</td><td>${psi.io?.some_avg60 || 0}%</td><td>${psi.io?.some_avg300 || 0}%</td></tr>
          </tbody>
        </table>
      `;
    }

    const forecasts = await (await fetch("/api/forecasting/forecasts")).json();
    const fc = $("forecastContainer");
    if (fc) {
      const mem = forecasts.memory || {};
      const disks = forecasts.disks || {};
      let items = [];
      if (mem.metric_name) {
        const bClass = mem.severity === "CRITICAL" ? "crit" : mem.severity === "WARNING" ? "warn" : "ok";
        items.push(`
          <div style="background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-md); padding:12px 16px; margin-bottom:8px;">
            <div><strong>RAM Consumption Trend:</strong> Current: ${mem.current_value.toFixed(1)}% | Rate: ${mem.rate_per_sec > 0 ? "+" : ""}${mem.rate_per_sec.toFixed(3)}%/s</div>
            <div style="margin-top:4px;">Projected Time-To-Exhaustion: <span class="badge ${bClass}">${mem.tte_human}</span> (Confidence: ${(mem.confidence * 100).toFixed(0)}%)</div>
          </div>
        `);
      }
      for (const [mount, f] of Object.entries(disks)) {
        const bClass = f.severity === "CRITICAL" ? "crit" : f.severity === "WARNING" ? "warn" : "ok";
        items.push(`
          <div style="background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-md); padding:12px 16px; margin-bottom:8px;">
            <div><strong>Disk Partition [${mount}] Trend:</strong> Current: ${f.current_value.toFixed(1)}% | Rate: ${f.rate_per_sec > 0 ? "+" : ""}${f.rate_per_sec.toFixed(3)}%/s</div>
            <div style="margin-top:4px;">Projected Time-To-Exhaustion: <span class="badge ${bClass}">${f.tte_human}</span></div>
          </div>
        `);
      }
      fc.innerHTML = items.join("") || `<div style="color:var(--text-muted); font-size:12.5px; padding:6px 0;">Accumulating telemetry samples for ordinary least squares projection...</div>`;
    }
  } catch {}
}

// ------------------------------------------------------------- AI Serving Tab
async function refreshAI() {
  try {
    const ai = await (await fetch("/api/workloads/ai")).json();
    const o = ai.ollama || {};
    $("ollamaStatus").innerHTML = `
      <table class="tbl">
        <tbody>
          <tr><td style="color:var(--muted);">Status</td><td><span class="badge ${o.available ? 'ok' : 'crit'}">${o.available ? 'ONLINE' : 'OFFLINE'}</span></td></tr>
          <tr><td style="color:var(--muted);">Endpoint</td><td><code>${o.endpoint || "http://127.0.0.1:11434"}</code></td></tr>
          <tr><td style="color:var(--muted);">Latency</td><td>${o.latency_ms ? `${o.latency_ms} ms` : "–"}</td></tr>
          <tr><td style="color:var(--muted);">Installed Models</td><td>${(o.installed_models || []).join(", ") || "None"}</td></tr>
        </tbody>
      </table>
    `;

    $("activeModels").innerHTML = (o.active_models && o.active_models.length) ?
      `<table class="tbl"><thead><tr><th>Model</th><th>RAM Size</th><th>VRAM Size</th></tr></thead><tbody>${o.active_models.map(m => `<tr><td><strong>${m.name}</strong></td><td>${m.size_mb} MB</td><td>${m.size_vram_mb} MB</td></tr>`).join("")}</tbody></table>` :
      '<div style="color:var(--muted); font-size:0.8rem; padding:10px;">No models currently loaded in memory.</div>';

    $("aiProcs").innerHTML = (ai.ai_processes && ai.ai_processes.length) ?
      `<table class="tbl"><thead><tr><th>Process</th><th>PID</th><th>Runtime</th><th>Memory</th></tr></thead><tbody>${ai.ai_processes.map(p => `<tr><td><strong>${p.name}</strong></td><td><code>${p.pid}</code></td><td>${p.runtime}</td><td>${p.memory_mb} MB</td></tr>`).join("")}</tbody></table>` :
      '<div style="color:var(--muted); font-size:0.8rem; padding:10px;">No local AI inference processes active.</div>';

    const dev = await (await fetch("/api/devin/status")).json();
    $("devinStatusBox").innerHTML = `
      <table class="tbl">
        <tbody>
          <tr><td style="color:var(--muted);">Readiness</td><td><span class="badge ${dev.has_api_key ? 'ok' : 'warn'}">${dev.status}</span></td></tr>
          <tr><td style="color:var(--muted);">API Key</td><td>${dev.has_api_key ? "Configured" : "Unset (Mock/Queue Mode)"}</td></tr>
          <tr><td style="color:var(--muted);">Devin CLI</td><td>${dev.has_desktop_cli ? dev.cli_path : "Not in PATH"}</td></tr>
        </tbody>
      </table>
    `;
  } catch {}
}

// ------------------------------------------------------------- Devin & PR Tab
async function refreshDevin() {
  try {
    const tasks = await (await fetch("/api/devin/tasks")).json();
    $("devinTasksList").innerHTML = tasks.length ?
      `<table class="tbl"><thead><tr><th>Task ID</th><th>Title</th><th>State</th></tr></thead><tbody>${tasks.map(t => `<tr><td><code>${t.id}</code></td><td><strong>${t.payload ? t.payload.title : ""}</strong></td><td><span class="badge info">${t.state}</span></td></tr>`).join("")}</tbody></table>` :
      '<div style="color:var(--muted); font-size:0.8rem; padding:10px;">No Devin repair tasks in queue.</div>';

    const gh = await (await fetch("/api/github/status")).json();
    $("githubStatusBox").innerHTML = `
      <table class="tbl">
        <tbody>
          <tr><td style="color:var(--muted);">Repository</td><td><a href="${gh.repo_url}" target="_blank"><strong>${gh.owner}/${gh.repo}</strong></a></td></tr>
          <tr><td style="color:var(--muted);">Auth State</td><td><span class="badge ${gh.authenticated ? 'ok' : 'warn'}">${gh.authenticated ? 'AUTHENTICATED' : 'READONLY'}</span></td></tr>
        </tbody>
      </table>
    `;
  } catch {}
}

$("runValidationBtn")?.addEventListener("click", async () => {
  const tid = $("valTaskId").value || "TASK-VAL-DEMO";
  const patchText = $("valPatchText").value;
  if (!patchText) {
    showToast("Please paste a git patch to validate", "warn");
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
      <div style="background:var(--card); border:1px solid var(--line); border-radius:5px; padding:12px;">
        <div style="margin-bottom:6px;"><strong>Sandbox Test Outcome:</strong> <span class="badge ${res.passed ? 'ok' : 'crit'}">${res.passed ? 'PASSED' : 'REJECTED'}</span></div>
        <div>Scope Check: ${res.scope_valid ? "✔ Approved" : `❌ Rejected (${res.rejection_reason})`}</div>
        <div style="margin-top:6px;">Changed Files: <code>${(res.changed_files || []).join(", ") || "none"}</code></div>
        <div class="events-console" style="height:110px; margin-top:8px;">
          ${(res.steps || []).map(s => `<div class="ev-line ${s.passed ? 'info' : 'crit'}"><span>${s.passed ? '✔' : '❌'}</span> <code>${s.command}</code> (exit code: ${s.exit_code})</div>`).join("")}
        </div>
      </div>
    `;
  } catch (e) {
    showToast(`Validation error: ${e}`, "crit");
  } finally {
    $("runValidationBtn").textContent = "Run Sandbox Validation";
  }
});

// ------------------------------------------------------------- Policy, Audit & Keys Tab
async function refreshAudit() {
  try {
    const actions = await (await fetch("/api/actions?limit=50")).json();
    const auditTbl = $("auditTbl")?.querySelector("tbody");
    if (auditTbl) {
      auditTbl.innerHTML = actions.map(a => `
        <tr>
          <td>${fmtTime(a.ts)}</td>
          <td><code>${a.actor || "operator"}</code></td>
          <td><strong>${a.action}</strong></td>
          <td>${a.target || "host"}</td>
          <td>${a.detail || a.reason || "–"}</td>
        </tr>
      `).join("");
    }

    const keys = await (await fetch("/api/security/keys")).json();
    const keysTbl = $("apiKeysTbl")?.querySelector("tbody");
    if (keysTbl) {
      keysTbl.innerHTML = keys.length ?
        keys.map(k => `<tr><td><strong>${k.name}</strong></td><td><span class="badge info">${k.role}</span></td><td><code>${k.key_prefix}</code></td><td>${fmtTime(k.created_at)}</td><td>${fmtRelative(k.last_used_at)}</td></tr>`).join("") :
        `<tr><td colspan="5" style="color:var(--text-muted); text-align:center; padding:14px;">No API keys generated.</td></tr>`;
    }

    const demo = await (await fetch("/api/demo")).json();
    if ($("demoBtns")) {
      $("demoBtns").innerHTML = Object.entries(demo.kinds || {}).map(([k, desc]) => `
        <button class="btn sm" title="${desc}" onclick="applyDemo('${k}')">${k.replace("_", " ").toUpperCase()}</button>
      `).join("");
    }

    const active = Object.entries(demo.active_overrides || {});
    if ($("demoActive")) {
      $("demoActive").innerHTML = active.length ?
        `Active simulation overrides: ${active.map(([s, d]) => `<code>${s}=${d.value_c}°C</code>`).join(", ")}` :
        "No active simulated overrides.";
    }
  } catch {}
}

async function applyDemo(kind) {
  try {
    await fetch("/api/demo/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind })
    });
    showToast(`Injected test scenario: ${kind}`, "info");
    pollOverview();
    refreshAudit();
  } catch (e) {
    showToast(`Demo error: ${e}`, "crit");
  }
}
window.applyDemo = applyDemo;

$("createApiKeyBtn")?.addEventListener("click", async () => {
  const name = prompt("Enter a friendly name for this API key (e.g. worker-node-01):");
  if (!name) return;
  const role = prompt("Enter role [ADMIN, OPERATOR, NODE_AGENT, READONLY]:", "NODE_AGENT");
  if (!role) return;
  try {
    const res = await (await fetch("/api/security/keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, role: role.toUpperCase() })
    })).json();
    prompt("API Key generated! Save this secret now:", res.secret_key);
    refreshAudit();
  } catch (e) {
    showToast(`Error creating key: ${e}`, "crit");
  }
});

// ------------------------------------------------------------- Controls & SSE
$("modeToggle")?.addEventListener("change", async (e) => {
  const mode = e.target.checked ? "policy_autonomous" : "monitoring_only";
  try {
    await fetch("/api/policy/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });
    showToast(`Mode set to ${mode}`, "ok");
    pollOverview();
  } catch (e) {
    showToast(`Error updating mode: ${e}`, "crit");
  }
});

$("estopBtn")?.addEventListener("click", async () => {
  const active = $("estopBtn").classList.contains("active");
  try {
    await fetch("/api/policy/emergency_stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !active })
    });
    showToast(active ? "Emergency stop disengaged" : "Emergency stop ENGAGED", active ? "ok" : "crit");
    pollOverview();
  } catch (e) {
    showToast(`Error setting emergency stop: ${e}`, "crit");
  }
});

// Click outside modal backdrop to dismiss
document.querySelectorAll(".modal-backdrop").forEach(backdrop => {
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) backdrop.style.display = "none";
  });
});

function initSSE() {
  const streamUrl = `${API_BASE}/api/events/stream`;
  const sse = new EventSource(streamUrl);
  sse.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      appendEvent(ev);
    } catch {}
  };
  sse.onerror = () => {
    const dot = $("sseStatusDot");
    if (dot) { dot.className = "status-dot crit"; }
  };
  sse.onopen = () => {
    const dot = $("sseStatusDot");
    if (dot) { dot.className = "status-dot ok"; }
  };
}

function appendEvent(ev) {
  if (eventsPaused) return;
  const c = $("events");
  if (!c) return;

  const sev = (ev.severity || "info").toLowerCase();
  if (currentEventFilter === "CRIT" && sev !== "crit" && sev !== "critical") return;
  if (currentEventFilter === "WARN" && sev !== "warn" && sev !== "warning") return;
  if (currentEventFilter === "INFO" && sev !== "info") return;

  const d = document.createElement("div");
  d.className = `ev-line ${sev}`;
  d.innerHTML = `<time>${fmtTime(ev.ts)}</time> <span>${ev.message}</span>`;
  c.prepend(d);
  if (c.children.length > 80) c.lastChild.remove();
}

document.querySelectorAll("#eventFilterGroup .filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#eventFilterGroup .filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentEventFilter = btn.dataset.filter;
  });
});

$("pauseEventsBtn")?.addEventListener("click", () => {
  eventsPaused = !eventsPaused;
  $("pauseEventsBtn").textContent = eventsPaused ? "Resume" : "Pause";
  $("pauseEventsBtn").classList.toggle("danger", eventsPaused);
});

// ------------------------------------------------------------- Initialization
pollOverview();
pollHistory();
pollSensors();
setupClusterJoinCommands();
initSSE();

setInterval(pollOverview, 3000);
setInterval(pollHistory, 5000);
setInterval(pollSensors, 10000);
