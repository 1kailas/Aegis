/* Aegis AI operations dashboard — vanilla JS, no build step.
 * All data comes from the backend API; unavailable sensors are shown
 * honestly rather than replaced with invented values. */
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

let overlapSeq = 0;      // last processed event seq (SSE)
let chartData = { ts: [], cpu_percent: [], mem_percent: [], temps: {} };

/* ------------------------------------------------ polling: overview + sensors */
async function pollOverview() {
  try {
    const snap = await (await fetch("/api/overview")).json();
    renderHealth(snap);
    renderCards(snap);
    renderThermal(snap);
    renderDemo(snap.active_overrides || {});
    $("footNote").textContent =
      `host ${snap.host.hostname} · mode ${snap.mode} · ` +
      `interval ${snap.collect_interval}s · db ${snap.db.path}`;
  } catch {
    setHealth("crit", "API unreachable — retrying");
  }
}

async function pollSensors() {
  try {
    const sensors = await (await fetch("/api/sensors")).json();
    renderSensors(sensors);
    renderTemps(sensors);
  } catch { /* transient */ }
}

async function pollHistory() {
  try {
    chartData = await (await fetch("/api/metrics/history?points=180")).json();
    drawChart();
  } catch { /* transient */ }
}

/* ------------------------------------------------------------- SSE event feed */
function connectEvents() {
  const es = new EventSource(`/api/events/stream?after=${overlapSeq}`);
  for (const kind of ["metrics", "thermal", "incident", "action", "policy",
                      "demo", "system"]) {
    es.addEventListener(kind, (e) => appendEvent(JSON.parse(e.data)));
  }
  es.onerror = () => {
    $(".live-dot").classList.add("off");
    setTimeout(() => { es.close(); connectEvents(); }, 3000);
  };
  es.onopen = () => $(".live-dot").classList.remove("off");
}

function appendEvent(ev) {
  if (ev.seq <= overlapSeq) return;
  overlapSeq = ev.seq;
  const list = $("events");
  const row = document.createElement("div");
  row.className = `ev sev-${ev.severity}`;
  row.innerHTML =
    `<time>${(ev.ts || "").replace("T", " ").slice(11, 19)}</time>` +
    `<span><b>${ev.kind}</b> ${escapeHtml(ev.message)}</span>`;
  list.prepend(row);
  while (list.children.length > 60) list.lastChild.remove();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;" }[c]));
}

/* -------------------------------------------------------------------- render */
function setHealth(cls, text) {
  $("health").innerHTML = `<span class="dot dot-${cls}"></span><span>${text}</span>`;
}

function clampTone(v, warn, crit) {
  return v >= crit ? "crit" : v >= warn ? "warn" : "ok";
}

function renderHealth(snap) {
  setHealth(snap.engine_running ? "ok" : "warn",
    snap.engine_running ? "engine running" : "engine stopped");
  if (snap.emergency_stop) setHealth("crit", "EMERGENCY STOP engaged");
}

function renderCards(snap) {
  const l = snap.latest;
  if (!l) return;
  $("cpuVal").textContent = `${(l.cpu_percent ?? 0).toFixed(1)}%`;
  $("cpuVal").className = "big " + clampTone(l.cpu_percent ?? 0, 60, 85);
  $("cpuSub").textContent =
    `load ${(l.load_avg_1 ?? 0).toFixed(2)} · ${snap.host.cpu_cores ?? "?"} cores`;

  $("memVal").textContent = `${l.memory.percent.toFixed(0)}%`;
  $("memVal").className = "big " + clampTone(l.memory.percent, 70, 90);
  $("memSub").textContent =
    `${l.memory.used_mb} MiB used · ${l.memory.available_mb} MiB avail · ` +
    `swap ${l.swap.percent.toFixed(0)}%`;

  const disks = l.disks || [];
  if (disks.length) {
    const d = disks[0];
    $("diskVal").textContent = `${d.used_pct.toFixed(0)}%`;
    $("diskVal").className = "big " + clampTone(d.used_pct, 75, 90);
    $("diskSub").textContent = disks.slice(0, 3)
      .map(d => `${d.mount} ${d.free_gb}GiB free`).join(" · ");
  } else {
    $("diskVal").textContent = "–";
    $("diskSub").textContent = "no usable mounts reported";
  }

  const net = l.net_io || {};
  $("netVal").textContent =
    "↓" + fmtBytes(net.recv_bytes_s) + " ↑" + fmtBytes(net.sent_bytes_s);
  let sub = `uptime ${Math.floor((l.uptime_s || 0) / 60)} min · validity ${snap.latest_validity}`;
  if (snap.sampling_boosted) sub += " · ⏩ fast sampling";
  $("netSub").textContent = sub;
}

function renderThermal(snap) {
  const t = snap.thermal;
  const el = $("thermalState");
  el.textContent = t.state;
  el.className = "big " +
    (t.state === "CRITICAL" ? "crit" : t.state === "WARNING" ? "warn" :
     t.state === "NORMAL" ? "ok" : "sim");
  $("thermalSub").textContent = t.reason || "";
  const qual = $("thermalQual");
  qual.innerHTML = "";
  if (t.data_quality !== "OK") {
    qual.innerHTML += `<span class="chip ${t.data_quality === "STALE" ? "STALE" : "UNAVAILABLE"}">${t.data_quality}</span>`;
  }
  if (t.current_c !== null && t.current_c !== undefined) {
    qual.innerHTML += `<span class="chip VALID">${t.current_c.toFixed(1)} °C</span>`;
  }
}

function renderSensors(sensors) {
  const tb = $("sensorTbl").querySelector("tbody");
  tb.innerHTML = "";
  for (const s of sensors) {
    const r = s.latest || {};
    const tr = document.createElement("tr");
    const val = (r.value === null || r.value === undefined)
      ? `<span class="muted">no data</span>`
      : `${r.value} ${s.unit || ""}`;
    tr.innerHTML = `<td>${escapeHtml(s.label)}</td><td>${s.kind}</td>` +
      `<td>${s.source}</td><td>${val}</td>` +
      `<td><span class="chip ${r.validity || "UNAVAILABLE"}">${r.validity || "NO DATA"}</span></td>` +
      `<td><span class="muted">${r.ts ? fmtTime(new Date(r.ts * 1000).toISOString()) : "–"}</span></td>`;
    tb.appendChild(tr);
  }
}

function renderTemps(sensors) {
  const temps = sensors.filter(s => s.kind === "temperature");
  const list = $("tempList");
  list.innerHTML = "";
  const anyValid = temps.some(s => s.latest && s.latest.validity === "VALID");
  const anySim = temps.some(s => s.latest && s.latest.validity === "SIMULATED_OVERRIDE");
  if (!anyValid) {
    $("tempHint").textContent = anySim
      ? "· only simulated data is active (real host exposes no usable sensors)"
      : "· this host does not expose usable hardware temperature sensors";
  } else {
    $("tempHint").textContent = "";
  }
  for (const s of temps) {
    const r = s.latest || {};
    const item = document.createElement("div");
    item.className = "temp-item";
    const sim = r.validity === "SIMULATED_OVERRIDE";
    item.innerHTML =
      `<span class="val ${sim ? "sim" : ""}">${
        r.value !== null && r.value !== undefined ? r.value + " °C" : "–"}</span>` +
      `<div><div>${escapeHtml(s.label)}</div>` +
      `<div class="meta">${escapeHtml(s.sensor_id)}</div></div>` +
      `<span class="spacer"></span>` +
      `<span class="chip ${r.validity}">${r.validity}</span>`;
    list.appendChild(item);
  }
}

function renderDemo(active) {
  const keys = Object.keys(active);
  $("demoActive").textContent = keys.length
    ? `Active: ${keys.map(k => `${k} = ${active[k].value_c} °C (${active[k].note})`).join(", ")}`
    : "No overrides active.";
}

/* --------------------------------------------------------------------- charts */
function drawChart() {
  const cv = $("chart-main");
  const ctx = cv.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  cv.width = cv.clientWidth * dpr;
  cv.height = 200 * dpr;
  ctx.scale(dpr, dpr);
  const w = cv.clientWidth, h = 200, pad = 26;
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "#2a3038";
  ctx.fillStyle = "#8b949e";
  ctx.font = "10px system-ui";
  for (let v = 0; v <= 100; v += 25) {
    const y = h - pad - (v / 100) * (h - 2 * pad);
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke();
    ctx.fillText(v, 6, y + 3);
  }
  const line = (data, color) => {
    if (!data || data.length < 2) return;
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.beginPath();
    const n = data.length;
    data.forEach((v, i) => {
      const x = pad + (i / (n - 1)) * (w - 2 * pad);
      const y = h - pad - ((v ?? 0) / 100) * (h - 2 * pad);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  };
  line(chartData.cpu_percent, "#58a6ff");
  line(chartData.mem_percent, "#2ea043");
}

/* --------------------------------------------------------------------- controls */
async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    const msg = await res.json().catch(() => ({}));
    alert(`Request failed (${res.status}): ${msg.detail || res.statusText}`);
  }
  return res.ok;
}

function wireControls() {
  const mode = $("modeToggle");
  mode.addEventListener("change", () => {
    const value = mode.checked ? "policy_autonomous" : "monitoring_only";
    post("/api/policy/mode", { mode: value }).then(initMode);
  });

  $("estopBtn").addEventListener("click", () => {
    const engage = !$("estopBtn").classList.contains("engage");
    if (engage && !confirm("Engage emergency stop? Autonomous remediation will be blocked.")) return;
    post("/api/policy/emergency_stop", { enabled: engage }).then(initMode);
  });

  const demoDefs = [
    ["warning", "Simulate WARNING (90°C)"],
    ["critical", "Simulate CRITICAL (95°C)"],
    ["rapid_rise", "Simulate rapid rise (80°C)"],
    ["recover", "Simulate recovery (40°C)"],
    ["unavailable", "Simulate sensor unavailable"],
    ["clear", "Clear overrides"],
  ];
  for (const [kind, label] of demoDefs) {
    const b = document.createElement("button");
    b.className = "btn";
    b.textContent = label;
    b.addEventListener("click", () =>
      post("/api/demo/override", { kind }).then(pollOverview));
    $("demoBtns").appendChild(b);
  }
}

async function initMode() {
  const snap = await (await fetch("/api/overview")).json();
  $("modeToggle").checked = snap.mode === "policy_autonomous";
  $("modeLabel").textContent = snap.mode;
  $("estopBtn").classList.toggle("engage", snap.emergency_stop);
  $("estopBtn").textContent =
    snap.emergency_stop ? "Release emergency stop" : "Emergency stop";
  pollOverview();
}

async function initVersion() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("ver").textContent = "v" + h.version;
  } catch { /* transient */ }
}

setInterval(pollOverview, 2000);
setInterval(pollSensors, 2000);
setInterval(pollHistory, 5000);
initVersion(); initMode();
wireControls();
connectEvents();
