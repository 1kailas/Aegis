# Project Submission — Aegis AI

## Project Information

- **Project Name:** Aegis AI
- **Short Description:** Autonomous infrastructure monitor and engineering control plane that continuously observes computing environments, orchestrates workloads across Docker and Kubernetes, monitors hardware health and AI model serving, diagnoses incidents using a multi-tier fallback engine, and delegates complex repairs to Devin as its intelligent repair engineer—validating fixes in isolated sandboxes and submitting evidence-backed GitHub pull requests with independent recovery verification.
- **Code Repository URL:** https://github.com/1kailas/Aegis.git
- **Technology Stack:** Python 3.11+, FastAPI, Uvicorn, SQLite (WAL mode), Pydantic v2, Docker API, Kubernetes Python Client, Ollama API, Google Gemini API, Devin Integration API, GitHub CLI / REST API, Vanilla HTML5/CSS3/ES6 JavaScript.

---

## Run and Demonstrate

- **Prerequisites:**
  - Linux (Ubuntu 22.04+, Debian 12+, Fedora, Arch, or WSL2)
  - Python 3.11+
  - Git
  - Optional: Docker daemon, Kubernetes config (`~/.kube/config`), Ollama, GitHub CLI (`gh`).
- **Install Command:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -e .
  ```
- **Start Command:**
  ```bash
  python -m aegis.main
  ```
- **Accessing Dashboard:**
  Open `http://localhost:8000` in any web browser.
- **Run Automated Test Suite:**
  ```bash
  PYTHONPATH=. .venv/bin/pytest tests/ -v
  ```
  *(40/40 tests passing across all 5 implementation phases)*

---

## Checks Performed & Validation Results

1. **Phase 1 Validation:**
   - Linux host telemetry (CPU per core, load averages, PSI pressure, memory, disk, network errors/drops, thermal throttling).
   - Specialized collectors (NVIDIA/AMD/Intel GPU detection with graceful `UNAVAILABLE` fallback, top processes, NVMe SMART).
   - Deterministic `ThermalGuardian` (rate-of-rise alerting, hysteresis, cooldown) and `ResourceGuardian`.
   - SQLite WAL persistence and time-series query APIs.
2. **Phase 2 Validation:**
   - Docker container discovery, resource metrics, crash loop detection, OOM kill detection, and log sanitization.
   - Kubernetes cluster nodes, pods, and deployments monitoring with graceful missing-cluster fallback.
   - AI infrastructure monitoring for Ollama and active inference runtimes.
   - Blast radius and dependency topology calculations.
3. **Phase 3 Validation:**
   - 3-tier diagnosis engine (Ollama → Gemini with hourly budgeting → Deterministic heuristics).
   - Automated credential and API key redaction.
   - Devin repair adapter with structured reproduction steps and honest capability reporting.
   - Deterministic `ActionPolicyEngine` rejecting dangerous shell commands and enforcing emergency stop.
4. **Phase 4 Validation:**
   - Isolated workspace creation with environment secret stripping.
   - Patch scope validation rejecting `.env` modifications and path traversals.
   - Sandbox test execution recording command exit codes and output logs.
   - Evidence-backed GitHub PR generation.
   - Independent recovery verification decouples PR status from service recovery.
5. **Phase 5 Validation:**
   - Unified responsive dashboard with 7 functional tabs and real-time SSE stream.
   - End-to-end autonomous lifecycle simulation (Detect → Diagnose → Plan → Authorize → Validate → PR → Verify Recovery).
   - Security injection resistance.

---

## Submission Checklist

- [x] Implemented directly in the repository adhering to all 5 implementation phases.
- [x] Code repository URL and full documentation provided.
- [x] Working setup and demo instructions provided and verified.
- [x] All 40 automated tests passing with zero failures.
- [x] Deterministic safety controllers enforce protection independently of AI models.
- [x] No arbitrary shell commands allowed on the host.
- [x] No secrets or credentials leaked in code, commits, logs, or pull requests.
- [x] Devin established as the designated repair engineer.
- [x] Verified pull request workflow with isolated sandboxes.
- [x] Independent post-repair recovery verification implemented.
- [x] Change log and AI usage record fully maintained.
