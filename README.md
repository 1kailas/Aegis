# Aegis AI — Autonomous Infrastructure Monitoring & Engineering Platform

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Version: 2.0.0](https://img.shields.io/badge/version-2.0.0-blue.svg)]()
[![Tests Passing](https://img.shields.io/badge/tests-58%2F58%20passed-brightgreen.svg)]()
[![Prometheus Ready](https://img.shields.io/badge/prometheus-%2Fmetrics-orange.svg)]()
[![Security: Hardened](https://img.shields.io/badge/security-hardened-blue.svg)](SECURITY.md)
[![Agents: Devin Protocol](https://img.shields.io/badge/agents-Devin%20protocol-purple.svg)](AGENTS.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Safety: Deterministic Guardrails](https://img.shields.io/badge/safety-deterministic%20guardrails-red.svg)]()

> **"Aegis monitors and orchestrates. Deterministic controllers enforce safety. AI investigates and engineers fixes. Independent verification determines whether the fix actually worked."**

Aegis AI is a production-minded autonomous infrastructure monitoring and engineering control plane. It continuously observes whole-system computing environments, orchestrates workloads across Docker and Kubernetes, monitors hardware health and AI model-serving infrastructure, detects and diagnoses failures, and delegates complex software and configuration repairs to **Devin** as its designated repair engineer. It validates fixes in isolated sandboxes and submits evidence-backed GitHub pull requests—backed by automated recovery verification, resource optimization, predictive protection, and safety-first operations.

---

## Table of Contents

- [Architectural Principles & Safety Guardrails](#architectural-principles--safety-guardrails)
- [Architecture & Phase Breakdown](#architecture--phase-breakdown)
  - [Phase 1: Core Platform & Host Telemetry](#phase-1-core-platform--host-telemetry)
  - [Phase 2: Docker, Kubernetes & AI Workloads](#phase-2-docker-kubernetes--ai-workloads)
  - [Phase 3: AI Diagnosis, Devin Integration & Policy Engine](#phase-3-ai-diagnosis-devin-integration--policy-engine)
  - [Phase 4: Isolated Sandboxes, GitHub PRs & Recovery Verification](#phase-4-isolated-sandboxes-github-prs--recovery-verification)
  - [Phase 5: Unified Web Dashboard & Operational Readiness](#phase-5-unified-web-dashboard--operational-readiness)
  - [Aegis 1.5: Predictive Forecasting, Closed-Loop Autonomy & Observability](#aegis-15-predictive-forecasting-closed-loop-autonomy--observability)
  - [Aegis 2.0: Distributed Cluster Mesh, Canary Rollbacks & RBAC](#aegis-20-distributed-cluster-mesh-canary-rollbacks--rbac)
- [Non-Negotiable Principles](#non-negotiable-principles)
- [Installation & Quick Start](#installation--quick-start)
- [Configuration Reference](#configuration-reference)
- [Operational Controls & Safety Overrides](#operational-controls--safety-overrides)
- [API Reference](#api-reference)
- [Running Automated Tests](#running-automated-tests)

---

## Architectural Principles & Safety Guardrails

Traditional monitoring alerts humans when systems fail. Traditional autonomous agents risk catastrophic runaway actions when allowed unconstrained host execution. Aegis bridges this divide with a **safety-first dual-plane architecture**:

1. **Deterministic Safety Plane**: Operates 100% locally and deterministically. Evaluates resource saturation, thermal thresholds, and policy allowlists without calling cloud APIs or LLMs. Hardware-critical thresholds, emergency stops, and prohibited actions (`rm -rf`, raw shell pipes, credential access) are hardcoded and mathematically bounded.
2. **AI Engineering Plane**: Analyzes telemetry, traces, and container logs using a 3-tier fallback engine (Local Ollama → Cloud Gemini → Deterministic Expert Rules). Translates diagnosed root causes into structured repair tasks dispatched to Devin.
3. **Independent Verification Plane**: Never trusts patches blindly. Validates patches in ephemeral git sandboxes with strict scope checks, and verifies recovery through independent live probe checks before closing incidents.

```
       +-------------------------------------------------------+
       |                  Aegis Control Plane                  |
       +-------------------------------------------------------+
            |                          |                     |
   [Telemetry Pipeline]       [Action Policy Engine]   [Unified Dashboard]
   - CPU, RAM, PSI, NVMe      - Risk Classification    - Real-time Telemetry
   - Docker & Kubernetes      - Emergency Stop State   - Topology Graph
   - AI Serving (Ollama/vLLM) - Rate Limits & Audits   - Incidents & Actions
            |                          |                     |
            +------------+-------------+---------------------+
                         |
           +-------------v-------------+
           | Incident Detection Engine |
           +-------------+-------------+
                         |
           +-------------v-------------+
           | 3-Tier Multi-Engine Diag  |
           | 1. Local Ollama (qwen2.5) |
           | 2. Cloud Gemini (budgeted)|
           | 3. Deterministic Rules    |
           +-------------+-------------+
                         |
           +-------------v-------------+
           | Devin Repair Delegation   |
           +-------------+-------------+
                         |
           +-------------v-------------+
           | Isolated Sandboxes        |
           | - Scope check (.env reject|
           | - Test suite validation   |
           +-------------+-------------+
                         |
           +-------------v-------------+
           | Evidence-Backed GitHub PR |
           | - Test logs & exit codes  |
           | - Security checklist      |
           +-------------+-------------+
                         |
           +-------------v-------------+
           | Independent Recovery Verif|
           | - Live probe verification |
           +---------------------------+
```

---

## Architecture & Phase Breakdown

### Phase 1: Core Platform & Host Telemetry
- **Database Engine**: High-performance SQLite in WAL mode with structured schemas and migration tracking (`migrations/001_init.sql`).
- **Telemetry Collectors**:
  - `hardware.py`: Linux PSI (`/proc/pressure`), CPU frequency and core metrics, thermal throttling states (`/sys/class/thermal/cooling_device*`), network interface error/drop counters, disk latency.
  - `gpu.py`: Hardware-aware detection for NVIDIA (`nvidia-smi`), AMD (`rocm-smi` / sysfs), and Intel GPUs. Gracefully reports `UNAVAILABLE` without fabricating data on CPU-only machines.
  - `processes.py`: Top CPU and RAM consumer tracking with process attribution.
  - `storage.py`: Partition monitoring and NVMe SMART health tracking, filtering read-only system mounts to eliminate false positives.
  - `inventory.py`: Host identity, kernel, OS, and hardware inventory collection.
- **Deterministic Guardians**:
  - `ThermalGuardian`: Rate-of-rise alerting (°C/sec), hysteresis controls, and cooldown periods.
  - `ResourceGuardian`: Memory, disk, and CPU saturation thresholds with cooldown hold periods.

### Phase 2: Docker, Kubernetes & AI Workloads
- **Docker Integration (`docker_adapter.py`)**: Live container discovery, CPU/RAM/IO stats, restart counts, crash loop detection, OOM kill tracking, and sanitized container log streaming.
- **Kubernetes Integration (`k8s_adapter.py`)**: Multi-cluster monitoring via official `kubernetes` client. Tracks nodes, deployments, pods, `CrashLoopBackOff`, `ImagePullBackOff`, `OOMKilled`, and replica degradation. Scoped to read-only monitoring by default with explicit restart operations.
- **AI Infrastructure Adapter (`ai_workloads.py`)**: Monitors local Ollama instances (`/api/tags`, `/api/ps`), active model inference processes (PyTorch, vLLM, llama.cpp), and VRAM/RAM contention.
- **Dependency Topology (`dependency_graph.py`)**: Graph-based blast radius calculation (`LOW`, `MEDIUM`, `HIGH`) and resource competition analysis across Docker networks and Kubernetes services.

### Phase 3: AI Diagnosis, Devin Integration & Policy Engine
- **3-Tier Diagnosis Engine (`diagnosis.py`)**:
  - **Tier 1 (Local)**: Fast local inference via Ollama (`qwen2.5:1.5b`).
  - **Tier 2 (Cloud)**: Fallback to Google Gemini (`gemini-flash-latest`) with strict token budgeting, hourly rate limits, and automated credential redaction.
  - **Tier 3 (Deterministic Rules)**: Offline expert heuristics ensuring 100% operational continuity without internet access.
  - Strictly typed Pydantic outputs labeled as `ASSESSMENT`.
- **Devin Integration (`devin_adapter.py`)**:
  - Treats Devin as the designated intelligent repair engineer.
  - Formats rich `RepairTask` payloads including incident reproduction steps, acceptance criteria, scope boundaries, and security constraints.
  - Honestly reports readiness (`READY` or `AWAITING_EXTERNAL_SETUP`); queues jobs in SQLite if API keys are unconfigured without fabricating execution.
- **Deterministic Action Policy Engine (`action_policy.py`)**:
  - Categorizes actions into `AUTOMATIC_EXECUTE`, `REQUIRE_APPROVAL`, and `PROHIBITED`.
  - Rejects arbitrary shell execution (`rm -rf`, bash pipes, curl-to-sh).
  - Enforces global emergency stop and operator authorization modes.

### Phase 4: Isolated Sandboxes, GitHub PRs & Recovery Verification
- **Isolated Workspace Manager (`workspace.py`)**:
  - Creates ephemeral local git clones in `repair-workspaces/<task_id>`.
  - Sanitizes environment variables to strip host API keys (`GEMINI_API_KEY`, `GITHUB_TOKEN`).
  - Inspects patch scopes, immediately rejecting patches modifying `.env`, GitHub workflows, or attempting directory traversal (`../`).
  - Applies patches and executes test commands with strict execution timeouts.
- **Evidence-Backed GitHub PR Workflow (`github_adapter.py`)**:
  - Creates dedicated repair branches (`aegis/repair-...`).
  - Submits pull requests using authenticated `gh` CLI or GitHub REST API.
  - Injects full validation evidence: test commands, execution duration, exit codes, output logs, security checklists, and rollback steps.
- **Independent Recovery Verifier (`recovery_verifier.py`)**:
  - Decouples pull request status from service recovery.
  - Executes live metric and health probes to determine true recovery status: `CONFIRMED`, `PARTIAL`, `FAILED`, or `INCONCLUSIVE`.

### Phase 5: Unified Web Dashboard & Operational Readiness
- **Responsive Dashboard (`aegis/static/`)**:
  - **Overview**: System vitals, mode toggle, emergency stop switch, active incident feed, and real-time event log.
  - **Hardware & Guardians**: Core telemetry, thermal rate-of-rise gauges, and resource pressure indicators.
  - **Docker & Kubernetes**: Live container and pod tables, health statuses, restart counts, and dependency blast radius view.
  - **AI Infrastructure**: Local model-serving processes, loaded models, and resource contention alerts.
  - **Incidents & Diagnosis**: Detailed incident timeline, multi-tier diagnostic assessments, and remediation proposals.
  - **Devin & Repair**: Repair task queue, sandbox test logs, and GitHub pull request links.
  - **Policy & Audit**: Comprehensive audit log of every action, approval status, and security decision.
- **Server-Sent Events (SSE)**: Real-time event streaming via `/api/events/stream`.

### Aegis 1.5: Predictive Forecasting, Closed-Loop Autonomy & Observability
- **Predictive Telemetry Forecasting (`forecasting.py`)**:
  - Online ordinary least squares (OLS) linear regression modeling metric slopes.
  - Computes Time-To-Exhaustion (TTE) for RAM and disk storage partitions.
  - Triggers proactive warnings before physical saturation or OOM crashes occur.
- **Autonomous Closed-Loop Self-Healing Engine (`self_healing.py`)**:
  - Continuous reconciliation cycle inspecting active incidents.
  - Automatically queries diagnostic assessments and authorizes allowlisted remediations in `policy_autonomous` mode.
  - Dispatches non-trivial code/config repairs to Devin.
  - Dead-letter escalation to `NEEDS_HUMAN_APPROVAL` if repairs do not resolve the incident within max retry limits.
- **Synthetic Health Prober (`probers.py`)**:
  - Regular HTTP and TCP canary probes testing external service endpoints.
  - Validates expected HTTP status codes, latency SLAs, and connection responsiveness.
- **Prometheus Metrics Exposition (`prometheus.py`)**:
  - Native Prometheus `/metrics` endpoint compliant with OpenMetrics standard.
  - Exposes host vitals, guardian statuses, active incident counters, and container states.

### Aegis 2.0: Distributed Cluster Mesh, Canary Rollbacks & RBAC
- **Distributed Cluster Mesh & Agent Protocol (`mesh.py`)**:
  - Multi-node management with registration, periodic telemetry heartbeats, and stale node detection.
  - Real-time fleet capacity aggregation across all cluster nodes (CPU cores, RAM, and GPU capacity).
  - Node maintenance and draining (`drain_node`) preventing workload dispatch to cordoned machines.
- **Automated Canary Deployment & Rollback Engine (`canary_verifier.py`)**:
  - Progressive rollout observation over configurable step counts.
  - Automated canary verification evaluating error budgets and latency SLAs.
  - Immediate closed-loop automated rollback (`ROLLING_BACK` -> `ROLLED_BACK`) upon SLA degradation.
- **Multi-Channel Notification Dispatcher (`dispatcher.py`)**:
  - Multi-destination alerts routing to Slack, PagerDuty, and generic Webhooks.
  - Sliding-window alert storm debouncing and deduplication to prevent notification fatigue during cascade failures.
- **Cryptographic RBAC & Scoped API Keys (`rbac.py`)**:
  - SHA-256 token hashing with high-entropy prefixes (`aegis_sec_...`).
  - Strict role hierarchy: `ADMIN`, `OPERATOR`, `READONLY`, and `NODE_AGENT`.
  - Enforces authorization on all state-changing endpoints, blocking unauthorized writes with HTTP 403 Forbidden.
- **Cluster Mesh Dashboard Tab**:
  - Dedicated real-time view of all registered nodes, IP addresses, statuses, and active canary rollouts.

---

## Non-Negotiable Principles

Aegis strictly enforces ten operational invariants:

1. **No AI Control Over Safety Mechanisms**: Thermal, memory, disk, and crash thresholds are deterministic code. AI models never alter physical protection limits.
2. **No Arbitrary Shell Execution**: AI cannot execute arbitrary bash commands on the host. Actions are constrained to a predefined, typed allowlist.
3. **No Unverified Success Claims**: A repair is not marked resolved because a PR was opened or a patch compiled. Resolution requires independent live recovery verification.
4. **Zero Secret Leakage**: API tokens, private keys, and environment variables are scrubbed from telemetry, logs, diagnostic prompts, and PR descriptions.
5. **No Destructive Operations Without Approval**: Destructive operations (`DROP`, `DELETE`, `systemctl stop`) require explicit human operator confirmation.
6. **No Cloud Dependency for Routine Monitoring**: Host telemetry collection, guardian evaluations, and incident tracking function 100% offline.
7. **Zero Fabrication**: Unsupported sensors (e.g. absent GPUs, missing fan sensors) are marked `UNAVAILABLE`. No fake telemetry or simulated PRs are recorded as real.
8. **Explainable and Traceable**: Every incident transition, diagnosis, policy decision, and remediation execution is written to an immutable SQLite audit log.
9. **Graceful Degradation**: Docker daemon down? Kubernetes cluster unconfigured? Ollama offline? Aegis logs the state and continues monitoring all available subsystems.
10. **Modular and Testable**: Clean separation between collectors, guardians, diagnosis engines, and orchestration adapters.

---

## Installation & Production Deployment

### Option 1: Automated One-Line Linux Installer
```bash
# Automated installer creates virtualenv, links CLI binaries and sets up systemd
./scripts/install.sh --role all
```

### Option 2: Python / Pip Editable Setup
```bash
# 1. Clone the repository
git clone https://github.com/1kailas/Aegis.git
cd Aegis

# 2. Create and activate a Python 3.11+ virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install in editable mode (installs aegis, aegis-server, and aegis-agent)
pip install -e .
```

### Option 3: Docker Compose
```bash
docker-compose up -d
```

---

## Client & Server Architecture

Aegis is packaged as a real distributed client-server platform:

### 1. Aegis Server (`aegis-server` or `aegis server`)
Runs the centralized control plane, SQLite WAL database, high-density web dashboard, self-healing orchestrator, and cluster coordinator:
```bash
aegis-server --host 0.0.0.0 --port 8000
```
- Dashboard UI: **`http://localhost:8000`**
- OpenAPI Docs: **`http://localhost:8000/api/docs`**
- Prometheus Scrape: **`http://localhost:8000/metrics`**

### 2. Aegis Agent (`aegis-agent` or `aegis join`)
Lightweight daemon running on ANY client device. Just give the server's IP address, and it immediately normalizes the endpoint, discovers local CPU/RAM/disks/GPUs, registers the node, and streams heartbeats:

```bash
# Option A: Instant zero-install run (No pip/git needed, pure Python 3 on any machine)
curl -sSL http://<SERVER_IP>:8000/agent.py | python3 - <SERVER_IP>

# Option B: Automated service install (Installs systemd unit + CLI)
curl -sSL http://<SERVER_IP>:8000/install.sh | bash -s <SERVER_IP>

# Option C: Pass just the IP directly to the binary
aegis-agent 192.168.1.50
# Or using the unified CLI:
aegis join 192.168.1.50

# With RBAC token and custom labels:
aegis-agent 192.168.1.50 --token aegis_sec_... --labels role=gpu-worker,zone=us-east-1
```

### 3. Aegis Developer CLI (`aegis`)
Full command-line tooling for SREs and developers:
```bash
aegis status                   # High-density terminal dashboard of health and guardians
aegis nodes list               # List all registered cluster mesh nodes and capacities
aegis nodes drain <node_id>    # Cordon and drain a node before maintenance
aegis incidents list           # List active, contained, or repairing incidents
aegis incidents diagnose <id>  # Trigger multi-tier AI root cause diagnosis
aegis canary list              # Inspect active progressive canary rollouts
aegis canary rollback <id>     # Instantly abort and rollback an active canary
aegis keys create -n worker1   # Generate cryptographically hashed RBAC token
aegis mode [autonomous|approval] # Change operating mode
aegis estop [on|off]           # Global emergency stop kill-switch
```
👉 **`http://localhost:8000/docs`**

---

## Configuration Reference

Aegis is configured via two cleanly separated configuration files:

### `config.yaml` — System & Collector Settings
```yaml
server:
  host: "0.0.0.0"
  port: 8000

monitoring:
  interval_s: 5.0
  retention_days: 7
  disk_mounts: ["/"]

ai:
  ollama_url: "http://127.0.0.1:11434"
  ollama_model: "qwen2.5:1.5b"
  gemini_model: "gemini-flash-latest"
  gemini_hourly_budget: 60

devin:
  api_url: "https://api.devin.ai/v1"
```

### `policies.yaml` — Deterministic Safety Thresholds
```yaml
thermal:
  sensor: auto
  warning_c: 75.0
  critical_c: 85.0
  recovery_below_c: 68.0
  hysteresis_c: 3.0
  rate_per_s_warning: 0.5
  recovery_hold_s: 60

resources:
  memory_warning_pct: 85.0
  memory_critical_pct: 95.0
  disk_warning_pct: 85.0
  disk_critical_pct: 95.0
  cpu_warning_pct: 90.0
  cpu_critical_pct: 98.0
  cooldown_s: 60.0
```

---

## Operational Controls & Safety Overrides

- **Emergency Stop**: Immediately halts all autonomous actions across the platform.
  ```bash
  curl -X POST http://localhost:8000/api/policy/emergency_stop -H "Content-Type: application/json" -d '{"enabled": true}'
  ```
- **Autonomous vs. Operator Mode**: Switch between automatic execution of allowlisted low-risk remediations and manual approval:
  ```bash
  curl -X POST http://localhost:8000/api/policy/mode -H "Content-Type: application/json" -d '{"mode": "policy_autonomous"}'
  # or
  curl -X POST http://localhost:8000/api/policy/mode -H "Content-Type: application/json" -d '{"mode": "operator_approval"}'
  ```
- **Demonstration Overrides**: Inject non-destructive synthetic telemetry to verify guardian responses without harming physical hardware:
  ```bash
  # Simulate critical thermal event (92°C)
  curl -X POST http://localhost:8000/api/demo/override -H "Content-Type: application/json" -d '{"kind": "thermal_critical", "value_c": 92.0}'
  
  # Clear override
  curl -X POST http://localhost:8000/api/demo/override -H "Content-Type: application/json" -d '{"kind": "clear"}'
  ```

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | `GET` | Serves the unified Aegis web dashboard |
| `/api/health` | `GET` | Health check and subsystem status |
| `/metrics` | `GET` | Prometheus / OpenMetrics scrape format |
| `/api/system/snapshot` | `GET` | Latest host, container, and AI infrastructure telemetry |
| `/api/telemetry/metrics` | `GET` | Historical metric series with aggregation |
| `/api/incidents` | `GET`, `POST` | List and query incidents |
| `/api/incidents/{id}/diagnose` | `POST` | Trigger multi-tier AI diagnosis |
| `/api/devin/status` | `GET` | Check Devin integration readiness |
| `/api/devin/tasks` | `GET`, `POST` | Create or inspect Devin repair tasks |
| `/api/repair/validate` | `POST` | Validate proposed patch in isolated workspace |
| `/api/repair/submit_pr` | `POST` | Submit evidence-backed GitHub pull request |
| `/api/repair/verify_recovery`| `POST` | Independently verify workload recovery |
| `/api/remediation/authorize`| `POST` | Evaluate action against safety policy engine |
| `/api/policy/emergency_stop`| `POST` | Engage or disengage global emergency stop |
| `/api/policy/mode` | `POST` | Toggle autonomous or operator-approval mode |
| `/api/cluster/nodes/register` | `POST` | Register cluster node in distributed mesh |
| `/api/cluster/nodes/{id}/heartbeat` | `POST` | Record agent telemetry heartbeat |
| `/api/cluster/nodes/{id}/drain` | `POST` | Cordon and drain cluster node |
| `/api/cluster/summary` | `GET` | Aggregated cluster capacity & node counts |
| `/api/canary/rollouts` | `GET`, `POST` | List or initialize canary deployment rollouts |
| `/api/canary/rollouts/{id}/step` | `POST` | Advance canary step with automated health check |
| `/api/canary/rollouts/{id}/rollback` | `POST` | Trigger automated canary rollback |
| `/api/notifications/channels` | `GET`, `POST` | Configure Slack / PagerDuty / webhook channels |
| `/api/notifications/dispatch` | `POST` | Dispatch alert with storm debouncing |
| `/api/security/keys` | `GET`, `POST` | Generate and list cryptographically hashed RBAC keys |
| `/api/audit` | `GET` | Query immutable audit logs |
| `/api/events/stream` | `GET` | SSE real-time stream of all platform events |

---

## Running Automated Tests

Aegis includes an exhaustive automated test suite covering all 5 implementation phases plus releases v1.5 and v2.0.

```bash
# Run the full test suite
PYTHONPATH=. .venv/bin/pytest tests/ -v
```

### Test Suite Coverage:
- **`tests/test_phase1.py` (12 tests)**: Host telemetry, PSI pressure, GPU handling, process tracking, NVMe SMART, ThermalGuardian hysteresis, ResourceGuardian cooldowns, SQLite persistence.
- **`tests/test_phase2.py` (10 tests)**: Docker container monitoring, crash loop detection, log redaction, restart policy protection, Kubernetes client fallback, AI workload detection, blast radius calculations.
- **`tests/test_phase3.py` (8 tests)**: 3-tier diagnosis fallback, Gemini hourly rate limiting, credential scrubbing, Devin task creation & queueing, policy engine allowlists and prohibited command rejection.
- **`tests/test_phase4.py` (7 tests)**: Isolated sandbox clones, patch scope enforcement (`.env` and path traversal rejection), environment variable sanitization, GitHub PR evidence generation, independent recovery verification.
- **`tests/test_phase5.py` (4 tests)**: End-to-end autonomous lifecycle simulation (Detect → Diagnose → Plan → Authorize → Validate → PR → Verify Recovery), injection security hardening, operator approval mode enforcement, target-aware live probe verification, and static dashboard & OpenAPI delivery.
- **`tests/test_v15.py` (5 tests)**: Predictive telemetry forecasting (OLS linear regression & TTE calculations), closed-loop autonomous self-healing engine (triage, auto-remediation, and dead-letter escalation), synthetic HTTP/TCP probers, Prometheus metrics exposition line protocol, and v1.5 API endpoints.
- **`tests/test_v20.py` (5 tests)**: Distributed cluster mesh node lifecycle and draining, automated canary verification and closed-loop rollbacks, multi-channel webhook alert storm debouncing, cryptographic RBAC token generation & permission enforcement, and v2.0 API routes.
- **`tests/test_client_and_cli.py` (7 tests)**: Aegis Agent host telemetry & capacity sampling, cluster mesh registration and authenticated heartbeats, graceful node draining on shutdown, unified `aegis` CLI subcommands (`status`, `nodes`, `incidents`, `canary`, `keys`, `mode`, `estop`, `join`), `normalize_server_url` IP parsing, and server distribution endpoints (`/agent.py`, `/install.sh`, `/api/cluster/join-info`).

**Result: 58 passed in 9.2 seconds.**
