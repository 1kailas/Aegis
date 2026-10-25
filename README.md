# Aegis AI — Autonomous Site Reliability Engineering & Auto-PR Control Plane

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Version: 3.2.0](https://img.shields.io/badge/version-3.2.0-blue.svg)]()
[![Tests Passing](https://img.shields.io/badge/tests-101%2F101%20passed-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Safety: Deterministic Gate](https://img.shields.io/badge/safety-deterministic%20guardrails-red.svg)]()
[![Autonomous PRs](https://img.shields.io/badge/GitHub%20PRs-Real%20gh%20CLI-purple.svg)]()

> **"Aegis monitors live web services. Deterministic controllers enforce safety boundaries. AI models analyze stack traces and synthesize code fixes. The GitHub CLI opens real Pull Requests. Independent health probes verify closed-loop recovery."**

Aegis is an autonomous, production-oriented Site Reliability Engineering (SRE) control plane. When a production web application or microservice throws an unhandled exception or enters a 5xx crash loop, Aegis intercepts the telemetry, calculates cross-service dependency causality, rejects red-herring decoy events, synthesizes a clean unified git diff patch via rate-guarded AI models, and uses the GitHub CLI (`gh`) to open a **real, reviewable Pull Request** on GitHub in under 5 seconds.

---

## ⚡ The Core Problem: Alert Fatigue vs. Autonomous Resolution

Traditional monitoring platforms (Datadog, New Relic, Grafana) excel at telling you that a system is broken:
* A service crashes at 3:00 AM $\rightarrow$ PagerDuty fires a critical alert.
* An on-call engineer wakes up, logs into a bastion, digs through megabytes of raw logs, and isolates the stack trace.
* The engineer locates the repository, reproduces the bug locally, writes a fix, runs unit tests, pushes a branch, and opens a Pull Request.
* Average Mean Time to Resolution (MTTR): **45 to 90 minutes**.

**With Aegis:**
1. **Live Log & Crash Ingestion**: The monitored website reports unhandled exceptions with full stack traces directly to `POST /api/incidents/report`.
2. **Causality Topology & Decoy Rejection**: Aegis constructs a causal DAG of the microservice topology, identifies the blast radius, and deterministically ranks root causes while rejecting unrelated red herrings (e.g., scheduled deployment on a decoy service).
3. **Isolated AI Code Synthesis**: Groq AI (`qwen/qwen3.8-27b` / `llama-3.1-8b-instant`) generates a surgical unified git diff patch inside an ephemeral git worktree.
4. **Real GitHub PR Creation**: Aegis commits the patch, pushes an isolated branch (`aegis/repair-*`), and invokes `gh pr create` to publish an authentic Pull Request on GitHub.
5. **Closed-Loop Verification**: An independent HTTP probe tests the live application endpoint. Only when HTTP 200 OK is re-established does the incident transition to `RECOVERY_VERIFIED`.
* Average Mean Time to Resolution (MTTR): **< 5 seconds**.

---

## 🏛️ System Architecture

Aegis enforces a strict **Dual-Plane Separation of Concerns**:

```
                  +-------------------------------------------------------+
                  |               Monitored Web Application               |
                  |                (e.g., Webliz Storefront)              |
                  +-------------------------------------------------------+
                                     |                    ^
              Unhandled Exception    |                    |  Independent Probe
              (HTTP 500 / Traceback) |                    |  (HTTP 200 OK)
                                     v                    |
                  +-------------------------------------------------------+
                  |                  Aegis Control Plane                  |
                  |                 (http://localhost:8080)               |
                  +-------------------------------------------------------+
                         |                    |                     |
                         v                    v                     v
            +--------------------+  +--------------------+  +--------------------+
            | Causality Topology |  |   Safety Guardian  |  | Operator AI Switch |
            | Deterministic DAG  |  | Deterministic Gate |  | Instant AI Disarm  |
            | Decoy Rejection    |  | HTTP 403 Forbidden |  | Zero Cloud Outflow |
            +--------------------+  +--------------------+  +--------------------+
                         |
                         v
            +-------------------------------------------------------+
            |             Autonomous PR Synthesis Engine            |
            |     - Groq AI Engine (qwen/qwen3.8-27b / Llama-3.1)   |
            |     - Strict hourly request cap (15 req/hr)           |
            |     - Deterministic unified diff fallback guardrail   |
            +-------------------------------------------------------+
                         |
                         v
            +-------------------------------------------------------+
            |              Isolated Git Worktree Pipeline           |
            |     - git worktree add /tmp/aegis-repair-*            |
            |     - Apply unified diff & run validation checks      |
            |     - git commit & git push origin aegis/repair-*     |
            +-------------------------------------------------------+
                         |
                         v
            +-------------------------------------------------------+
            |                Real GitHub PR via gh CLI              |
            |   gh pr create --repo 1kailas/Aegis --head aegis/...  |
            |   Live PR: https://github.com/1kailas/Aegis/pull/X    |
            +-------------------------------------------------------+
```
<img width="1914" height="962" alt="Screenshot 2026-10-25 072646" src="https://github.com/user-attachments/assets/69c5d154-eb59-4bb1-8092-24d7aa9086c4" />

### 1. Deterministic Observation & Safety Plane
* **Probes & Watchdogs**: Monitored endpoints are probed continuously with configurable interval and failure thresholds.
* **Deterministic Safety Gate**: AI recommendations are treated as untrusted advice. If an autonomous agent attempts a destructive command (`rm -rf`, modifying `.env`, directory traversal, or piping raw shell scripts), the policy gate blocks execution with an immutable **HTTP 403 Forbidden**.
* **Operator AI Kill Switch**: A physical toggle on the dashboard immediately halts all external AI/LLM API calls while keeping monitoring and manual remediation 100% active.

### 2. Autonomous Engineering & PR Plane
* **Ephemeral Workspaces**: Patches are applied in isolated git worktrees (`/tmp/aegis-repair-*`) to prevent corrupting the active checkout.
* **Rate-Guarded LLM Synthesis**: AI calls are capped with hourly token budgets and automated fallback mechanisms to eliminate quota exhaustion.
* **Authentic GitHub CLI Integration**: Uses the host's authenticated `gh` session to push real branches and open real PRs on GitHub with structured evidence, test reports, and reproduction steps.

---

## 🛒 The Live Demonstration Application: Webliz Cloud Store

Aegis includes a real, responsive E-Commerce application (`demo-target/app.py`) running on **`http://localhost:5050`** specifically designed to demonstrate live error ingestion and automated PR generation.

### The Planted Defect
In `demo-target/app.py`, the checkout calculation function contains a division-by-zero vulnerability:

```python
def calculate_discounted_total(base_amount: float, discount_percent: float) -> float:
    """Calculates final order total applying promotional discount."""
    if _STATE["broken"]:
        # Planted defect: ZeroDivisionError occurs when discount_percent is 100!
        multiplier = 100 / (100 - discount_percent)
        return round(base_amount * multiplier, 2)
    # Correct calculation:
    discount_fraction = max(0.0, min(100.0, discount_percent)) / 100.0
    return round(base_amount * (1.0 - discount_fraction), 2)
```

### How the Live Demo Loop Operates:
1. **Visit Storefront**: Open `http://localhost:5050`. The store operates normally with HTTP 200 OK.
2. **Inject 500 Outage Bug**: Click **`💥 Inject 500 Outage Bug`** on the top toolbar:
   * The application triggers `calculate_discounted_total(149.00, 100.0)` with coupon `CRASH100`.
   * Python catches the `ZeroDivisionError` and captures the full traceback.
   * `demo-target` posts the payload to `http://127.0.0.1:8080/api/incidents/report`.
3. **Aegis Autonomous Response**:
   * Aegis opens a Critical Incident (`software_bug`).
   * Groq AI (`qwen/qwen3.8-27b`) analyzes the traceback and synthesizes the git patch.
   * Aegis creates branch `aegis/repair-inc-...`, pushes to GitHub, and calls `gh pr create`.
   * The live GitHub PR link (e.g. `https://github.com/1kailas/Aegis/pull/2`) is returned.
   * Both the Webliz Storefront and the Aegis Dashboard immediately display the clickable PR link.
4. **Service Topology Update**:
   * In the Aegis Dashboard, the visual topology highlights `checkout` as **FAILING (✖)** and downstream `payment` and `postgres` as **DEGRADED (⚠)**.
5. **Apply Fix & Closed-Loop Recovery**:
   * Click **`✨ Apply Fix / Recover`** on the storefront or **`⚡ Verify & Recover`** on the Aegis Dashboard.
   * The application restores safe division logic.
   * Aegis probes `http://127.0.0.1:5050/health`, confirms HTTP 200 OK, transitions the incident to **`RECOVERY_VERIFIED`**, and resets all 8 topology nodes back to **100% GREEN (✔)**.

---

## 🌐 Kea-Style Causality Microservice Topology

Aegis includes an interactive SVG topology visualizer rendering an 8-node microservice dependency graph:

```
[ web-frontend ] ---> [ api-gateway ] ---> [ checkout ] ---> [ payment ] ---> [ postgres ]
                                 |              |
                                 v              v
                           [ inventory ]  [ notifications (decoy) ]
                                 |
                                 v
                             [ redis ]
```

### State Transitions:
| System State | Nodes Appearance | Edges & Indicators | Right Panel Diagnostics |
| :--- | :--- | :--- | :--- |
| **Nominal (Healthy)** | All 8 services show **green `✔`** (`healthy`) | Subtle blue/gray connections; 0 affected services | `Services affected: 0/8 (All Healthy)`, `ALL HEALTHY` badge |
| **Active Outage (Failing)** | `checkout` turns **red `✖`** (`failing`); `payment` & `postgres` turn **yellow `⚠`** (`degraded`) | Glowing red/orange causal path (`checkout -> payment -> postgres`) | `Services affected: 2/8`, Ranked root causes, unified diff, real GitHub PR URL |
| **Recovery Verified** | All 8 services instantly return to **green `✔`** | Glowing causal chain cleared; live probes confirm HTTP 200 | `Recovery verified via live HTTP 200 health probe`, `MTTR recorded` |

### Decoy Rejection Engine
When outages occur, real distributed systems generate multiple correlated events. Aegis deterministically evaluates candidate events against the dependency graph:
* **True Cause**: `Unhandled Exception in demo-target/app.py` $\rightarrow$ Composite Score: `0.95`, Status: **`RANKED_ROOT_CAUSE`**.
* **Red Herring**: `Deployment on Notifications Service (Decoy)` $\rightarrow$ Composite Score: `0.08`, Status: **`REJECTED_DECOY`** (`NO_DEPENDENCY_PATH: Notifications service has zero dependency connections to the failing checkout path`).

---

## 🔒 Operator AI Kill Switch & Deterministic Guardrails

In mission-critical production environments, operators must have absolute authority to stop autonomous agents.

### 1. The Tactile AI Toggle Switch
* Located in the top header of the Aegis Dashboard: `#apiToggleSwitch`.
* Backed by `GET /api/policy/api-enabled` and `POST /api/policy/api-enabled`.
* **When ON (`Autonomous AI Engine Active`)**: Ingestion automatically synthesizes fixes and opens GitHub PRs.
* **When OFF (`AI API Disabled`)**: Ingestion logs incidents locally without making external LLM or GitHub PR calls. The API returns `status: "API_DISABLED"`.

### 2. Deterministic Command Gate (HTTP 403)
Aegis tests every automated remediation against an immutable whitelist. You can test this live from the dashboard or terminal:

```bash
curl -i -X POST http://127.0.0.1:8080/api/policy/test-safety \
  -H "Content-Type: application/json" \
  -d '{"command": "rm -rf /", "target": "/"}'
```

**Response:**
```http
HTTP/1.1 403 Forbidden
Content-Type: application/json

{
  "detail": {
    "error": "Deterministic safety gate rejected dangerous command.",
    "risk_level": "PROHIBITED",
    "reason": "Command matches blacklisted destructive pattern: rm -rf"
  }
}
```

---

## 🚀 Quick Start Guide

### Prerequisites
* Linux or macOS (or WSL2 on Windows)
* Python 3.11+
* Git
* GitHub CLI (`gh`) authenticated with repository write access:
  ```bash
  gh auth login
  gh auth status
  ```

### 1. Clone & Set Up Virtual Environment
```bash
git clone https://github.com/1kailas/Aegis.git
cd Aegis

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Create a `.env` file in the repository root:
```ini
# Aegis Control Plane Configuration
PORT=8080
AEGIS_URL=http://127.0.0.1:8080

# Groq Autonomous PR Engine (Llama-3.1 / Qwen)
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=qwen/qwen3.8-27b
GROQ_FALLBACK_MODEL=llama-3.1-8b-instant

# Optional: Devin API Key (if using Devin integration)
DEVIN_API_KEY=
```

### 3. Launch Services
Open two terminal windows:

**Terminal 1 — Aegis Control Plane:**
```bash
source .venv/bin/activate
PYTHONPATH=. python3 -m aegis.main --host 0.0.0.0 --port 8080 --db data/aegis.db
```
*Access the SRE Console at:* **`http://localhost:8080`**

**Terminal 2 — Webliz Storefront Demo Target:**
```bash
source .venv/bin/activate
python3 demo-target/app.py 5050
```
*Access the Storefront at:* **`http://localhost:5050`**

---

## 🧪 Testing the Live Auto-PR Loop (Step-by-Step)

1. Open **`http://localhost:8080`** in your browser. Verify the dashboard loads with **0 Outages** and **100% OK**.
2. Open **`http://localhost:5050`** in a second browser window.
3. On the Webliz Storefront, click **`💥 Inject 500 Outage Bug`**:
   * An error banner immediately appears showing the caught `ZeroDivisionError`.
   * In 2 to 4 seconds, the banner displays the live GitHub PR link:
     ```
     ⚡ Autonomous PR Engine Engaged! Pull Request created:
     https://github.com/1kailas/Aegis/pull/2 ↗
     ```
4. Switch to the Aegis Dashboard at **`http://localhost:8080`**:
   * Navigate to the **Service Topology / Causality** tab.
   * `checkout` is flagged red (`failing ✖`), `payment` & `postgres` are yellow (`degraded ⚠`).
   * Click the **Autonomous Code Repair & PR** tab on the right panel to inspect the branch name, the live PR link, and the synthesized git diff.
5. On the Webliz Storefront, click **`✨ Apply Fix / Recover`**:
   * The bug is cleared.
   * Aegis independently verifies recovery with an HTTP 200 health probe.
   * All 8 microservices in the topology return to **100% green (`healthy ✔`)**.

---

## 📡 Core REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Control plane operational health status and engine state. |
| `GET` | `/api/overview` | Aggregated host vitals, memory pressure, active targets, and AI status. |
| `POST` | `/api/incidents/report` | **Live Ingestion**: Ingests unhandled exceptions/stack traces, dispatches AI patch synthesis, and opens real GitHub PR. |
| `GET` | `/api/incidents` | Lists all active and historical incidents with full lifecycle timelines. |
| `GET` | `/api/incidents/{id}/causality` | Returns the deterministic causality graph, blast radius, and candidate ranking for an incident. |
| `POST` | `/api/incidents/recovery-confirm` | Closes the loop: validates target recovery and transitions active incidents to `RECOVERY_VERIFIED`. |
| `POST` | `/api/incidents/clear-all` | Purges incident records for clean demo resets. |
| `GET` | `/api/policy/api-enabled` | Returns current state of the Operator AI Kill Switch (`{"enabled": true}`). |
| `POST` | `/api/policy/api-enabled` | Toggles the Operator AI Kill Switch ON or OFF. |
| `POST` | `/api/policy/test-safety` | Submits commands to the deterministic safety gate (proves 403 Forbidden). |
| `GET` | `/api/targets` | Lists all monitored website targets, latency metrics, and probe status. |
| `POST` | `/api/targets/{id}/probe` | Triggers an immediate HTTP health probe against a monitored website. |

---

## 🔬 Automated Test Suite

Aegis includes a comprehensive test suite covering the dual-plane safety engine, rate limits, causality DAG analysis, incident state machines, and API report ingestion:

```bash
source .venv/bin/activate
pytest tests/ -q
```

**Output:**
```
........................................................................ [ 71%]
.............................                                            [100%]
101 passed, 1 warning in 19.56s
```

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.
