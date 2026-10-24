# Change Log — Aegis AI

All notable changes to the **Aegis AI** platform are documented in this file in reverse chronological order.

---

## 2026-10-24T23:35:00+05:30 — Aegis AI v1.5.0: Predictive Forecasting, Closed-Loop Autonomy & Observability

- **Changed:**
  - Implemented `TelemetryForecaster` in `aegis/intelligence/forecasting.py` using online ordinary least squares (OLS) linear regression to project rate-of-change and Time-To-Exhaustion (TTE) for RAM and disk storage partitions before physical thresholds are breached.
  - Implemented `SelfHealingEngine` in `aegis/control_plane/self_healing.py` executing autonomous closed-loop reconciliation cycles: pairs detected incidents with multi-tier diagnosis, authorizes allowlisted remediations (`policy_autonomous`), delegates code/config defects to Devin, and enforces dead-letter escalation (`NEEDS_HUMAN_APPROVAL`) on repeated failures.
  - Implemented `SyntheticProber` in `aegis/orchestration/probers.py` executing continuous HTTP and TCP canary probes to monitor endpoint health, response validity, and latency SLAs.
  - Implemented standardized Prometheus metrics exporter endpoint at `GET /metrics` in `aegis/api/prometheus.py` exposing host vitals, guardian states, active incidents, and container counts.
  - Upgraded unified dashboard with a real-time **Predictive Forecasting & TTE** panel on the Hardware tab, and linked the Prometheus `/metrics` exposition format in the footer.
  - Added comprehensive v1.5 automated test suite in `tests/test_v15.py` (5 tests).
- **Why:** Delivers enterprise-grade predictive prevention, proactive capacity planning, closed-loop self-healing, and native Prometheus integration.
- **Validation:** Executed full test suite: `PYTHONPATH=. .venv/bin/pytest tests/ -v`. All 46 tests passed in 7.46s.
- **Limitations:** None.

---

## 2026-10-24T23:26:00+05:30 — Production Hardening, Bug Fixes & Operational Protocols

- **Changed:**
  - Added support for `operator_approval` mode across `aegis/api/server.py`, `aegis/appstate.py`, `aegis/control_plane/action_policy.py`, and `aegis/control_plane/policy.py`, ensuring human approval is strictly enforced for non-allowlisted actions.
  - Implemented target-aware live probing in `/api/repair/verify_recovery`, evaluating specific subsystem health (`thermal_guardian`, Docker containers, and host vitals).
  - Added `/docs` redirect to `/api/docs` in `server.py` for standard Swagger UI navigation.
  - Updated default Gemini model to `gemini-flash-latest` in `config.yaml` to ensure high rate limits and eliminate 404 model deprecation errors.
  - Enhanced `IsolatedWorkspaceManager` in `aegis/engineering/workspace.py` to automatically configure `PYTHONPATH` for ephemeral cloned workspaces.
  - Removed obsolete hackathon scaffolding files (`RULES.md`, `SUBMISSION.md`).
  - Authored comprehensive `SECURITY.md` policy and `AGENTS.md` operational engineering protocol for autonomous agents (Devin, Claude, Antigravity).
  - Expanded Phase 5 automated test suite (`tests/test_phase5.py`) with `test_operator_approval_mode_and_target_verification`.
- **Why:** Elevates Aegis AI from hackathon prototype to production-grade autonomous infrastructure engineering platform.
- **Validation:** Executed full test suite: `PYTHONPATH=. .venv/bin/pytest tests/ -v`. All 41 tests passed in 4.81s.
- **Limitations:** None. Full backward and forward compatibility maintained.

---

## 2026-10-24T23:15:00+05:30 — Phase 5: Unified Web Dashboard, Security Hardening & End-to-End Autonomous Lifecycle

- **Changed:**
  - Designed and built the unified responsive web dashboard in `aegis/static/index.html`, `styles.css`, and `app.js` featuring 7 dedicated tabs: Overview, Hardware, Docker & Kubernetes, AI Infrastructure, Incidents & Diagnosis, Devin & Repair, Policy & Audit.
  - Mounted dashboard and static asset endpoints in `aegis/api/server.py` with real-time SSE live event streaming at `/api/events/stream`.
  - Implemented the complete end-to-end autonomous lifecycle orchestrator linking Detection → Diagnosis → Remediation Proposal → Policy Authorization → Devin Task Creation → Isolated Workspace Validation → Evidence-backed PR Submission → Independent Recovery Verification.
  - Implemented automated security hardening rejecting malicious commands, path traversals, and unallowlisted system modifications.
  - Added comprehensive test suite `tests/test_phase5.py` validating the full autonomous loop, injection rejection, and UI asset delivery.
- **Why:** Delivers single-pane-of-glass operator visibility, real-time control (mode toggle, emergency stop), and completes the fully automated remediation loop.
- **Validation:** Ran `PYTHONPATH=. .venv/bin/pytest tests/test_phase5.py`. All 3 tests passed. Full test suite: 40/40 passed.
- **Limitations:** None. System gracefully functions in both autonomous and manual operator-approval modes.

---

## 2026-10-24T22:50:00+05:30 — Phase 4: Isolated Sandboxes, Evidence-Backed PRs & Recovery Verification

- **Changed:**
  - Implemented `IsolatedWorkspaceManager` in `aegis/engineering/workspace.py` providing ephemeral git repository clones in `repair-workspaces/<task_id>`.
  - Added environment variable sanitization stripping host secrets (`GEMINI_API_KEY`, `GITHUB_TOKEN`, private keys) prior to test execution.
  - Implemented strict patch scope inspection rejecting diffs that attempt to modify `.env`, GitHub Actions workflows, or utilize directory traversal (`../`).
  - Implemented `GitHubAdapter` in `aegis/engineering/github_adapter.py` integrating with GitHub CLI (`gh`) and REST API, supporting automated branch creation (`aegis/repair-...`) and evidence-backed PR generation containing test commands, exit codes, output logs, security checklists, and rollback steps.
  - Implemented `RecoveryVerifier` in `aegis/engineering/recovery_verifier.py` providing independent probe-based verification of workload recovery (`CONFIRMED`, `PARTIAL`, `FAILED`, `INCONCLUSIVE`), decoupling PR creation from incident resolution.
  - Added Phase 4 test suite `tests/test_phase4.py` (7 tests).
- **Why:** Guarantees that AI-generated fixes are verified in a clean sandbox before submission, and prevents premature incident resolution.
- **Validation:** Ran `PYTHONPATH=. .venv/bin/pytest tests/test_phase4.py`. All 7 tests passed.
- **Limitations:** PR creation gracefully supports dry-run mode when running in unauthenticated test environments.

---

## 2026-10-24T22:30:00+05:30 — Phase 3: AI Diagnosis, Devin Integration & Deterministic Safety Policy

- **Changed:**
  - Implemented `MultiTierDiagnoser` in `aegis/intelligence/diagnosis.py` with 3-tier fallback (Tier 1: Local Ollama `qwen2.5:1.5b`; Tier 2: Cloud Gemini `gemini-flash-latest` with token budgeting and hourly rate limits; Tier 3: Deterministic expert rules).
  - Implemented secret redaction regex filter stripping API keys, tokens, and credentials from telemetry and logs before sending to AI models.
  - Implemented `DevinAdapter` in `aegis/engineering/devin_adapter.py` establishing Devin as the designated repair engineer, formatting structured `RepairTask` payloads with reproduction steps, acceptance criteria, scope boundaries, and security constraints.
  - Implemented `ActionPolicyEngine` in `aegis/control_plane/action_policy.py` categorizing actions into `AUTOMATIC_EXECUTE`, `REQUIRE_APPROVAL`, and `PROHIBITED`.
  - Added Phase 3 test suite `tests/test_phase3.py` (8 tests).
- **Why:** Ensures AI diagnosis cannot execute destructive actions, enforces token budgeting, and connects Devin for complex code repairs.
- **Validation:** Ran `PYTHONPATH=. .venv/bin/pytest tests/test_phase3.py`. All 8 tests passed.
- **Limitations:** Devin tasks are queued in SQLite when DEVIN_API_KEY is not configured; no simulated execution is fabricated.

---

## 2026-10-24T22:00:00+05:30 — Phase 2: Docker, Kubernetes & AI Infrastructure Workload Orchestration

- **Changed:**
  - Implemented `DockerAdapter` in `aegis/orchestration/docker_adapter.py` discovering containers, stats, health checks, restart counts, crash loops, OOM kills, and streaming sanitized logs.
  - Implemented `KubernetesAdapter` in `aegis/orchestration/k8s_adapter.py` using official client libraries, monitoring cluster nodes, deployments, and pods (`CrashLoopBackOff`, `ImagePullBackOff`, `OOMKilled`) with read-only default permissions.
  - Implemented `AIWorkloadMonitor` in `aegis/orchestration/ai_workloads.py` querying Ollama APIs (`/api/tags`, `/api/ps`) and tracking inference processes (PyTorch, vLLM, llama.cpp).
  - Implemented `DependencyGraph` in `aegis/orchestration/dependency_graph.py` calculating workload blast radius (`LOW`, `MEDIUM`, `HIGH`) and resource contention.
  - Added Phase 2 test suite `tests/test_phase2.py` (10 tests).
- **Why:** Extends Aegis from a single-machine host monitor into an infrastructure-aware orchestration and container control plane.
- **Validation:** Ran `PYTHONPATH=. .venv/bin/pytest tests/test_phase2.py`. All 10 tests passed.
- **Limitations:** Adapters gracefully degrade to inactive status when Docker socket or kubeconfig are absent.

---

## 2026-10-24T21:30:00+05:30 — Phase 1: Core Platform Architecture & Whole-System Monitoring

- **Changed:**
  - Created high-performance SQLite backend in WAL mode with migration engine (`migrations/001_init.sql`).
  - Built comprehensive Linux host telemetry collectors: CPU frequency, per-core metrics, thermal throttling states (`/sys/class/thermal`), Linux PSI pressure metrics (`/proc/pressure`), network error/drop counters, disk latency.
  - Implemented specialized collectors: `gpu.py` (NVIDIA/AMD/Intel graceful detection), `processes.py` (top consumers), `storage.py` (partitions & NVMe SMART), `inventory.py` (hardware and OS identity).
  - Implemented deterministic `ThermalGuardian` with rate-of-rise alerting and hysteresis, and `ResourceGuardian` with memory/disk/CPU saturation detection and cooldown holds.
  - Built telemetry query and pruning pipeline in `aegis/db.py`.
  - Added Phase 1 test suite `tests/test_phase1.py` (12 tests).
- **Why:** Establishes the core deterministic control plane and reliable telemetry pipeline.
- **Validation:** Ran `PYTHONPATH=. .venv/bin/pytest tests/test_phase1.py`. All 12 tests passed.
- **Limitations:** Unsupported sensors (e.g. absent dedicated GPU) are cleanly reported as `UNAVAILABLE` without fabricating data.
