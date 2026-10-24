# Change Log — Aegis AI

All notable changes to the **Aegis AI** platform are documented in this file in reverse chronological order.

---

## 2026-10-25T04:35:00+05:30 — Aegis AI v3.2.0: Real-Time Incident Causality & Service Topology with Full Auto-PR Loop (Kea-Inspired)

- **Added & Changed:**
  - **Incident Causality & Service Topology Engine (`aegis/intelligence/causality.py`)**:
    - Inspired by `mohi-devhub/kea`, implemented a deterministic causality engine and service dependency topology graph that automates the critical first hour of incident response.
    - **Deterministic Root-Cause Candidate Ranking (Anti-Red Herring)**:
      - Evaluates failure candidates using per-factor scoring: `dependency_path_score` (1.0 vs 0.0), `temporal_proximity_score`, and `error_signature_match`.
      - Explicitly identifies and rejects red-herring decoy changes (e.g. unrelated deployments on isolated services like notifications) with clear reason codes: `NO_DEPENDENCY_PATH`.
    - **Closing the Gap that Kea Left Open**:
      - While Kea stopped at proposing a diff hash without creating git branches or PRs (`backend/app/fix/apply.py` raising "not enabled"), Aegis connects the causality engine to real autonomous PR code synthesis (via Groq/Devin), commits the fix to `aegis/repair-{id}`, creates the remote GitHub PR, and tests the live health probe to close the recovery loop.
  - **API Endpoint `GET /api/incidents/{incident_id}/causality`**:
    - Exposes the full causal topology nodes/edges, blast radius score, ranked root-cause candidates with factor scores, rejected decoys with reason codes, predict-then-verify impact, and auto-PR metadata.
  - **Interactive Dashboard UI (`aegis/static/app.js` & `index.html`)**:
    - Integrated an interactive service dependency chain in the Incident Drawer showing traffic routing, failing components (🔴), degraded downstream queues (🟡), and healthy backends (🟢).
    - Rendered the Root-Cause Ranking & Decoy Rejection cards with confidence percentages and factor score breakdowns.
    - Embedded the Predict-Then-Verify box and syntax-highlighted Auto-PR diff viewer.
  - **Comprehensive Test Suite**:
    - Added `tests/test_causality.py` verifying deterministic ranking, decoy rejection, causality API endpoints, and auto-PR integration.
    - Test suite now at **99 passing tests** (100% pass rate).

---

## 2026-10-25T04:30:00+05:30 — Aegis AI v3.2.0: Groq Autonomous PR & Patch Synthesis Engine (Rate-Limit Guarded)

- **Added & Changed:**
  - **Groq Autonomous PR & Code Patch Engine (`aegis/engineering/groq_repair.py`)**:
    - Integrated Groq API (`llama-3.1-8b-instant`, with graceful fallback to `qwen/qwen3.8-27b`) specifically and strictly for **PR code repair and patch generation**.
    - Generates unified git diff patches from evidence, error traces, and acceptance criteria.
    - Publishes PR metadata and links (`aegis/repair-{incident_id}`).
  - **Strict Rate-Limiting Guardrails**:
    - Protects the Groq quota with an enforced hourly sliding-window request cap (`GROQ_MAX_REQ_PER_HOUR=15`).
    - Enforces Output Tokens Per Minute (OTPM) constraints (`max_tokens: 450`) to avoid HTTP 429 rate-limit exhaustion on free tiers.
    - Refuses to invoke the LLM for background polling, health probes, or telemetry, reserving API tokens strictly for PR generation.
  - **DevinAdapter Fallback Integration**:
    - When Devin API is unconfigured or offline, `DevinAdapter.dispatch_task` seamlessly engages the Groq PR Engine.
    - Transitions incident lifecycle to `PR_OPENED` with synthesized diff patch and PR URL attached as timeline evidence.
    - Full closed-loop verification: `POST /api/devin/tasks/{id}/close-loop` tests deployed code against the live health probe and transitions incident to `RECOVERY_VERIFIED`.
  - **Security & Secret Scrubbing Hardening**:
    - Redacted all Groq API keys (`gsk_[A-Za-z0-9_-]{30,}`) across `IncidentDiagnoser`, `DockerAdapter`, `GroqRepairEngine`, and server logs as `[REDACTED_GROQ_KEY]`.
    - Sanitized token representation in API outputs (`f"{key[:6]}...{key[-4:]}"`).
  - **Topology Graph & Live Troubleshooting Center Updates**:
    - Added `key-groq` node to `GET /api/security/graph` with Groq Orange branding, status indicator, and quota monitoring.
    - Updated `POST /api/security/test-key` with zero-token credential and latency verification against Groq API.
  - **UI Incident Timeline & PR Diff Viewer**:
    - Updated `Stage 4: Autonomous Pull Request` in `aegis/static/app.js` to render the syntax-highlighted unified diff patch and engine badge (`⚡ Engine: GROQ (llama-3.1-8b-instant)`).
    - Updated `aegis/static/index.html` with `⚡ Autonomous PR (Groq/Devin)` action.
  - **Test Suite Expansion**:
    - Added `tests/test_groq_pr.py` covering configuration, secret scrubbing, rate-limiting enforcement, unified diff parsing, dispatch integration, topology graph, and health endpoints.
    - Test suite expanded to **97 passing tests** (100% pass rate).

---

## 2026-10-25T04:10:00+05:30 — Aegis AI v3.2.0: Interactive API Key Topology Graph & Real-Time Troubleshooting Center

- **Added & Changed:**
  - **Interactive API Key & Integration Topology Graph**:
    - Implemented `GET /api/security/graph` exposing an interconnected node-and-edge topology of all API keys, external integrations, and internal cryptographic RBAC tokens:
      - 🛡️ **Central Governance Hub**: Aegis Control Plane (dual-plane governance).
      - 🧠 **Software Intelligence Key**: Google Gemini API (`gemini-2.5-flash` / `gemini-3.5-flash`), 30 req/hr budget.
      - 🤖 **Autonomous SRE Key**: Devin Control API (`https://api.devin.ai/v1`).
      - 🐙 **Version Control Token**: GitHub access token (`1kailas/Aegis`).
      - ⚡ **Local Hardware Service**: Ollama Unix daemon (`http://127.0.0.1:11434`, Qwen model).
      - 🔑 **Cryptographic RBAC Tokens**: SHA-256 hashed Admin, Operator, Auditor, and Node Agent keys.
  - **Live Connection & Latency Testing Endpoint**:
    - Added `POST /api/security/test-key` allowing one-click diagnostic testing of any key or integration node in the topology.
    - Measures live roundtrip latency in milliseconds, tests authentication credentials, and returns targeted troubleshooting instructions if an integration is rate-limited (HTTP 429), unconfigured, or unreachable.
  - **Visual Topology Graph UI & Troubleshooting Drawer**:
    - Built interactive HTML5 Canvas visual graph in `aegis/static/index.html` and `app.js` with:
      - Distinct color-coded structures by category: 🟣 Indigo/Purple for AI Cloud, 🔵 Sky Blue for VCS, 🟢 Emerald for Infrastructure, and 🟠 Amber/Red for RBAC keys.
      - Glowing status rings (🟢 Healthy / 🟡 Rate-limited / 🔴 Missing).
      - Animated data-flow particles traveling along quadratic bezier edge curves.
      - Category filter chips (`All Keys`, `AI Cloud`, `RBAC Tokens`, `Infrastructure`, `VCS / Git`).
      - Toggle between **Visual Topology Graph** and **Keys & RBAC Table View**.
    - Created slide-out **Troubleshooting Inspector Drawer**:
      - Displays key identity, redacted prefix, configuration source, target endpoint, and granted scopes.
      - Includes instant **"⚡ Test Live Connection & Latency"** button and targeted troubleshooting guide.
      - Added **"🔑 Key Graph"** quick-access button to the Overview live demo toolbar.
  - **Test Suite Expansion**:
    - Added unit tests for `/api/security/graph` and `/api/security/test-key` in `tests/test_dual_models.py`.
    - Total test suite now at **90 passing tests** (100% pass rate).

---

## 2026-10-25T03:50:00+05:30 — Aegis AI v3.2.0: Dual-Model Division of Labor — Software Intelligence (Gemini) & Hardware/Scaling Intelligence

- **Added & Changed:**
  - **Specialized Dual-Model Division of Labor**:
    - **Software Intelligence Model (`software_model`: Gemini 2.5 Flash)**:
      - Specializes in application code, website 5xx outages, exceptions, and crash loops.
      - Generates structured root cause hypotheses, affected files, reproduction steps, and acceptance criteria.
      - Automatically builds repair briefs and delegates Pull Request creation to **Devin**.
    - **Hardware & Infrastructure Intelligence Model (`hardware_model`: Local Infra Model / Qwen / Guardian)**:
      - Specializes in host compute load, CPU/VRAM/RAM saturation, thermal thresholds, and replica capacity.
      - Proposes and executes hardware remediation actions: `scale_instances` (multi-replica scaling), `evict_idle_model` (frees GPU VRAM), `trim_cache`, and `set_backpressure`.
  - **Intelligent Triage & Incident Routing**:
    - Upgraded `IncidentDiagnoser`: `is_hardware_incident()` automatically classifies anomalies into hardware vs software.
    - Software incidents route to `diagnose_software()` (Gemini Tier 2 -> Ollama -> Expert rules).
    - Hardware incidents route to `diagnose_hardware()` (Local hardware model -> Deterministic hardware guardian rules).
    - Tags every assessment with `model_role="software"` or `model_role="hardware"` and the corresponding model name.
  - **Autonomous Instance Scaling Execution**:
    - Implemented `scale_instances()` in `OneClickDeployer` (`aegis/orchestration/deployer.py`): dynamically updates Kubernetes deployments and Docker Compose manifests with scaled replica counts (e.g. 1 -> 2 -> 3 instances) and emits cluster scaling events.
    - Added `scale_instances` and `evict_idle_model` to `AUTONOMOUS_ALLOWLIST` in `RemediationPolicyEngine`.
    - Integrated scaling execution into `SelfHealingEngine`: hardware saturation incidents automatically trigger instance scale-out to relieve compute pressure.
  - **REST Endpoints & Frontend Showcase**:
    - Added `GET /api/models/dual` reporting full capabilities, roles, and status of both Software and Hardware models.
    - Added `POST /api/hardware/scale-instances` to trigger autonomous scaling on demand.
    - Updated `/api/health` to expose `dual_models` status.
    - Added **Dual-Model Division of Labor** card and live **"⚡ Scale Instances"** toolbar action to the web dashboard (`index.html` & `app.js`).
    - Upgraded the 6-stage Incident Timeline to dynamically display specialized stages for Software repairs (Devin PR) vs Hardware actions (Auto-Scaling & VRAM eviction).
  - **Test Suite Expansion**:
    - Added `tests/test_dual_models.py` with 8 comprehensive unit tests covering configuration, triage routing, scaling execution, safety allowlists, API endpoints, and self-healing.
    - Total test suite now at **88 passing tests** (100% pass rate).

---

## 2026-10-25T03:30:00+05:30 — Aegis AI v3.2.0: Devin Hacks Hackathon — Autonomous On-Call SRE & End-to-End Repair Verification

- **Changed:**
  - **Gemini Tier 2 Structured AI Diagnosis**:
    - Integrated Google Gemini REST API (`gemini-2.5-flash` / `gemini-3.5-flash`) via `GEMINI_API_KEY` from `.env`.
    - Made request budget configurable via `GEMINI_MAX_REQ_PER_HOUR` (default 30 for hackathon demo).
    - Configured structured JSON output schema `{root_cause, confidence, affected_files, reproduction_steps, acceptance_criteria}` with Pydantic validation and markdown code-block scrubbing.
    - Implemented multi-tier diagnosis fallback chain: Local Ollama -> Cloud Gemini API -> Deterministic Expert Rules.
    - Extended sensitive secret scrubbing to scrub `GEMINI_API_KEY`, `DEVIN_API_KEY`, Bearer tokens, and GitHub tokens from prompts, logs, and API payloads.
    - Added `/api/health` indicators `gemini_configured: bool` and `devin_configured: bool` with startup logging (never exposing raw keys).
  - **"Connect a Website" Flow**:
    - Added `monitored_targets` SQLite schema migration (`migrations/005_monitored_targets.sql`).
    - Implemented `WebsiteMonitor` in `aegis/orchestration/website_monitor.py` for continuous HTTP probing of any plain website or microservice (status, latency, content check).
    - Automatically opens a Critical Incident on consecutive failures or 5xx breaches and verifies recovery when service returns to 200 OK.
    - Exposed REST endpoints `GET /api/targets`, `POST /api/targets`, `DELETE /api/targets/{id}`, and `POST /api/targets/{id}/probe`.
    - Added web UI card and modal in the dashboard to connect and monitor production websites with live status badges.
  - **Real End-to-End Devin Repair Loop & Closing-the-Loop Verification**:
    - Upgraded `DevinAdapter` in `aegis/engineering/devin_adapter.py`: creates bounded `RepairTask` briefs enriched with diagnosis evidence, repro steps, acceptance criteria, and security restrictions.
    - Added `poll_session(task_id)` calling the official Devin API `GET /v1/sessions/{id}` to capture real PR links.
    - Added `close_loop_verification(task_id)` and endpoint `POST /api/devin/tasks/{id}/close-loop` that applies the patch/fix, executes independent verification probes, and transitions incidents to `RECOVERY_VERIFIED`.
    - Maintained honest non-configured status (`AWAITING_EXTERNAL_SETUP`) when `DEVIN_API_KEY` is not provided (never fabricating fake results).
  - **Demo Target Microservice**:
    - Created lightweight Flask target application in `demo-target/` (`app.py`, `Dockerfile`, `requirements.txt`, `trigger_bug.sh`, `fix_bug.sh`).
    - Features triggerable defect endpoint (`POST /break` -> HTTP 500 NullPointer in `checkout.py`) and fix endpoint (`POST /fix` -> HTTP 200 OK).
  - **Deterministic Safety Demonstration**:
    - Added `POST /api/policy/test-safety` endpoint submitting dangerous actions (`rm -rf /`) through `RemediationPolicyEngine`.
    - Deterministically rejects dangerous shell mutations with HTTP 403 Forbidden and structured policy violation reason.
    - Added "Safety Test (403)" button and modal in the web dashboard.
  - **Hardware / Resource Action Demo**:
    - Added `POST /api/demo/simulate-resource-action` safely triggering visible remediation (model VRAM eviction / cache trimming) by `ResourceGuardian`.
    - Logs action on incident timeline and audit trail.
  - **Autonomous Incident Timeline View**:
    - Created visual 6-stage lifecycle timeline in incident inspection: `1. DETECTED` -> `2. DIAGNOSED` (tier badge) -> `3. DELEGATED TO DEVIN` -> `4. PR OPENED` -> `5. TESTS PASSED` -> `6. RECOVERY VERIFIED`.
    - Added headline **MTTR Metric** (Time to Verified Recovery).
  - **Demo Reliability & Judge Script**:
    - Created `Makefile` and `scripts/demo.sh` for 1-command startup (`make demo`).
    - Updated `README.md` with 90-second judge demo script, closing-the-loop architectural explanation, and honest known limitations.
- **Validation:** 80 out of 80 tests passing (`.venv/bin/pytest tests/ -v`). All existing 70 tests pass without regression. Zero hardcoded secrets in code or git.


## 2026-10-25T01:55:00+05:30 — Aegis AI v3.2.0: Production SRE Console, Resilient API Routing & Judge Demonstration Release

- **Changed:**
  - **Ollama Local Model Integration & Multi-Instance Auto-Scaling**:
    - Replaced the static "2 model" recommendations panel with a dynamic **"My Ollama Models"** manager querying local Ollama (`/api/models/ollama`) with 1-click model pulling (`/api/models/ollama/pull`) and local model running.
    - Updated Kubernetes manifest generation in `OneClickDeployer` to include multi-instance distribution (`replicas: 2`, `podAntiAffinity` across nodes) and `HorizontalPodAutoscaler` (`autoscaling/v2`) targeting CPU (75%) and memory (80%).
    - Added `generate_docker_compose` producing multi-replica Docker Compose configurations with health checks and restart policies.
    - Upgraded deployment modal with a toggle between Kubernetes (HPA Auto-Scaling) and Docker Compose (Multi-Replica).
  - **Sensors Calibration & Unit Scaling Fix**:
    - Corrected Linux `hwmon` sysfs unit conversions: scaled millivolts to Volts (5000 mV -> 5.0 V) and microwatts to Watts (2270000 uW -> 2.27 W).
    - Added derived real-time current calculation ($I = P/V$) yielding accurate Amperes (e.g. 0.60 A) for battery/power rails.
    - Added dedicated unit formatting in the UI sensor table.
  - **Incident Dismissal & Database Cleansing**:
    - Purged stale historical demo incidents from `data/aegis.db` and `data/client_server.db`.
    - Added `DELETE /api/incidents/{id}` and `POST /api/incidents/clear-all` endpoints with UI "Dismiss" and "Clear All" action buttons.
  - **Real-Time Auto-Refresh & Reactive SSE Synchronization**:
    - Added automatic periodic background polling (`pollActiveTab()`) for the currently active operational tab.
    - Added reactive SSE listeners for cluster, incident, and model events, immediately updating the cluster mesh table and badges without requiring manual clicks or page reloads.
    - Consolidated 9 fragmented navigation tabs into 5 unified high-density operational consoles: `Overview`, `Cluster Mesh`, `AI Models`, `Infrastructure`, and `Incidents & Safety`.
    - Refined typography scale in `aegis/static/styles.css` using an Apple/Linear dark design system (`#090b10` background, `#0f131c` card surfaces, 14px body base, 26px gauge values, 11px uppercase micro-headers).
    - Preserved backward compatibility for all legacy tab references (`workloads`, `hardware`, `policy`, `engineering`, `ai`) via transparent JS tab aliasing.
  - **Dashboard Connectivity & "Offline" Resolution**:
    - Implemented cross-origin and protocol-agnostic `API_BASE` resolution in `aegis/static/app.js`, ensuring instant, reliable telemetry streaming even when opened via `file:///` or external proxies.
    - Decoupled `measurePing()` and `pollOverview()` component renders: encapsulated DOM renders (`renderHealth`, `renderCards`, `renderThermal`, `renderQuickSummaries`) in dedicated try/catch handlers so minor rendering anomalies never mask control plane connectivity.
    - Fixed CSS class selector assignment on `pingDot` (`ping-dot crit` / `ping-dot warn`).
  - **Cluster Mesh Enhancements & Node Pruning**:
    - Added `deregister_node(node_id)` in `aegis/cluster/mesh.py` and exposed `DELETE /api/cluster/nodes/{node_id}` in `aegis/api/server.py` to allow clean removal of decommissioned nodes.
    - Implemented live cluster capacity aggregation in `ClusterMeshManager.cluster_summary()`, computing actual fleet CPU cores and aggregate RAM GB from reporting agent telemetry and labels.
    - Added "Remove" button in the Cluster Mesh table for offline or drained nodes.
    - Cleaned up stale offline test nodes from the production SQLite database.
  - **Judge Demonstration & Documentation**:
    - Added comprehensive 3-Minute Live Walkthrough in `README.md` with step-by-step judge demonstration commands, architectural explanation, and feature highlights.
    - Synchronized version `3.2.0` across `pyproject.toml`, `aegis/__init__.py`, `index.html`, and `docs.html`.
- **Why:** Delivers a real, judge-ready production SRE platform with all dashboard bugs resolved, live client-server streaming verified, and zero-downtime cluster mesh operations.
- **Validation:** All 69 tests pass via `PYTHONPATH=. .venv/bin/pytest tests/ -v`. Live server (port 8000), client server (port 8001), and client agent daemon verified in real-time.
- **Limitations:** None.

---

## 2026-10-25T00:50:00+05:30 — Aegis AI v3.1.0: Real-Time Model Benchmarking, Dynamic Memory Optimization & Cluster AI Capacity Planner

- **Changed:**
  - Implemented `ModelBenchmarker` in `aegis/intelligence/benchmarking.py`:
    - Live inference execution and performance profiling: streams generation tokens to calculate Time-To-First-Token (TTFT in milliseconds), tokens/second decoding throughput, and prompt evaluation rate.
    - Physical memory-bandwidth hardware simulation fallback when offline, evaluating memory bus architecture (GDDR6/GDDR6X vs. DDR4/DDR5) against model weight sizes without generating synthetic or hallucinated results (`status: SIMULATED`).
    - Database schema migration `migrations/004_v31.sql` creating the `model_benchmarks` table with indexing on `model_name` and `created_at`.
  - Implemented `ModelOptimizer` in `aegis/intelligence/benchmarking.py`:
    - Dynamic VRAM/RAM reclamation: scans active model runtimes and proactively unloads idle models (`keep_alive: 0`) to eliminate OOM crash risks during resource pressure.
    - Fleet-wide AI Capacity Planner: aggregates GPU VRAM and system RAM across all distributed cluster nodes (`ClusterMeshManager`), computing concurrent model headroom (7B, 14B, 32B, 70B models) and recommending optimal node placement strategies.
  - Enhanced `SelfHealingEngine` in `aegis/control_plane/self_healing.py`:
    - Closed-loop autonomous verification: automatically executes live health probes for incidents in `CONTAINED` state, transitioning verified stable incidents directly to `RECOVERY_VERIFIED`.
  - Added REST API endpoints in `aegis/api/server.py`:
    - `POST /api/models/benchmark`: Dispatches live or physical simulation benchmark for any model.
    - `GET /api/models/benchmarks`: Queries historical benchmarks with model name filtering.
    - `POST /api/models/optimize`: Triggers dynamic VRAM/RAM optimization and idle model eviction.
    - `GET /api/models/cluster-capacity`: Aggregates cluster-wide AI model capacity and node placements.
  - Added developer CLI subcommands in `aegis/cli/main.py`:
    - `aegis models benchmark [model_name] [--prompt "..."]`: Runs empirical throughput and TTFT testing.
    - `aegis models optimize`: Reclaims VRAM and evicts idle models from the terminal.
  - Upgraded Web Dashboard (`aegis/static/index.html` & `aegis/static/app.js`):
    - Added **"⚡ Benchmark"** buttons on all model cards in the Model Hub.
    - Added **"⚡ Optimize VRAM"** and **"🌐 Cluster AI Capacity"** control buttons.
    - Added **Empirical Model Inference Benchmarks** table displaying real TTFT, throughput, and status.
    - Added interactive **Cluster AI Capacity Modal** displaying fleet VRAM, RAM, and concurrent model limits.
  - Version bumped to `3.1.0` in `pyproject.toml`, `aegis/__init__.py`, and web dashboard.
  - Added automated test suite `tests/test_v31.py` (5 tests) verifying benchmarking, optimization, cluster capacity calculations, closed-loop recovery verification, and CLI subcommands.
- **Why:** Empirically verifies AI model inference speeds, dynamically optimizes VRAM during high load, plans cluster-wide AI workload placement, and autonomously closes the self-healing incident lifecycle.
- **Validation:** Executed full test suite: `PYTHONPATH=. .venv/bin/pytest tests/ -v`. All 68 tests passed in 11.28s.
- **Limitations:** None. Full backward compatibility maintained.

---

## 2026-10-25T00:40:00+05:30 — Aegis AI v3.0.0: Model Optimization Engine & One-Click Cloud Native Deployer

- **Changed:**
  - Implemented `ModelRecommender` in `aegis/intelligence/model_recommender.py`:
    - Profiles hardware resources (GPU VRAM, system RAM, CPU cores, GPU architectures).
    - Features a curated catalog of open-source AI models (Qwen 2.5 Coder 1.5B/7B/14B/32B, DeepSeek R1 8B/14B/32B/70B, Llama 3.3 8B/70B, Mistral, Phi-4, Nomic Embed) categorized into 4 tasks: Coding, Reasoning, Fast Chat, and Embeddings.
    - Evaluates and categorizes models into deterministic fit tiers: `OPTIMAL` (100% GPU offload, ample KV cache headroom), `GOOD` (fits standard 4k-8k context), `MARGINAL` (exceeds VRAM, partial layer offload to system RAM with latency drop), and `INCOMPATIBLE` (high risk of CUDA OOM kill).
    - Calculates estimated generation throughput (tokens/second) and KV cache memory headroom.
  - Implemented `OneClickDeployer` in `aegis/orchestration/deployer.py`:
    - One-click deployment to Docker with automated GPU passthrough (`--gpus all`), volume persistence, and port binding.
    - Production-grade Kubernetes manifest generator and deployer: creates complete Deployments and Services with resource requests/limits (`nvidia.com/gpu: 1`), environment configurations (`OLLAMA_KEEP_ALIVE`), and readiness probes (`/api/tags`).
    - Database schema migration `migrations/003_v3.sql` defining persistent `model_deployments` table.
    - Deployment lifecycle management (`deploy_docker`, `deploy_k8s`, `list_deployments`, `stop_deployment`).
  - Added REST API endpoints in `aegis/api/server.py`:
    - `GET /api/models/catalog`: Model catalog metadata and task attributes.
    - `GET /api/models/recommendations`: Hardware profile and ranked model recommendations with task filtering.
    - `POST /api/models/deploy`: Dispatches one-click deployment to Docker or Kubernetes.
    - `GET /api/models/deployments`: Lists active and stopped model deployments.
    - `POST /api/models/deployments/{id}/stop`: Gracefully stops an active deployment.
    - `GET /api/orchestration/manifests/k8s` & `POST /api/orchestration/manifests/k8s`: Generates validated Kubernetes YAML manifests.
  - Added developer CLI subcommands in `aegis/cli/main.py`:
    - `aegis models recommend [--task CODING|REASONING|FAST_CHAT|EMBEDDING]`: Terminal table displaying model fit tiers, scores, and estimated tokens/sec.
    - `aegis models deploy <model_name> [--runtime docker|k8s] [--port 11434] [--no-gpu]`: Instant deployment from the command line.
    - `aegis models list`: Real-time deployment status and port bindings.
  - Upgraded Web Dashboard (`aegis/static/index.html` & `aegis/static/app.js`):
    - Added **"Model Hub & Deployer"** tab with real-time hardware status card (VRAM, RAM, CPU cores).
    - Added interactive task category filter pills (`ALL`, `CODING`, `REASONING`, `FAST CHAT`, `EMBEDDING`).
    - Color-coded fit tier badges (`OPTIMAL`, `GOOD`, `MARGINAL`, `INCOMPATIBLE`).
    - One-click Docker deployment and Kubernetes YAML manifest inspector modal with copy-to-clipboard functionality.
    - Active model deployments management table with live stop buttons.
  - Version bumped to `3.0.0` in `pyproject.toml`, `aegis/__init__.py`, and dashboard UI.
  - Added automated test suite `tests/test_v30.py` (5 tests) covering hardware profiling, task filtering, Docker and K8s deployment logic, REST endpoints, and developer CLI commands.
- **Why:** Solves the primary challenge of local and cloud AI deployment: matching infrastructure hardware capacity against AI model demands, eliminating out-of-memory crashes, and enabling seamless, one-click Docker and Kubernetes provisioning.
- **Validation:** Executed full test suite: `PYTHONPATH=. .venv/bin/pytest tests/ -v`. All 63 tests passed in 9.56s.
- **Limitations:** None. Full backward compatibility maintained.

---

## 2026-10-25T00:25:00+05:30 — Zero-Friction Remote Client Joining via IP & Dynamic Server Script Serving

- **Changed:**
  - Implemented `normalize_server_url` in `aegis/client/agent.py` enabling users to pass raw IP addresses (e.g. `192.168.1.100` or `10.0.0.1:8480`) which automatically resolve to the control plane HTTP endpoint.
  - Added positional `server_ip` argument support across `aegis-agent <IP>`, `aegis join <IP>`, and `aegis agent <IP>`.
  - Added graceful zero-dependency fallback in `AegisAgent`: uses Python standard library (`urllib.request`, `shutil.disk_usage`, `/proc/meminfo`, `/proc/stat`, `os.getloadavg`) when `psutil` or `httpx` are not installed, enabling the agent to run on 100% of standard Linux machines without installing any third-party packages.
  - Enabled global CORS middleware (`CORSMiddleware`) in `aegis/api/server.py` allowing cross-origin requests from any remote client device or network IP.
  - Added server-side distribution endpoints in `aegis/api/server.py`:
    - `GET /agent.py`: Serves the standalone, zero-dependency Python telemetry agent directly over HTTP.
    - `GET /install.sh`: Serves a dynamic shell installer pre-configured with the server's Host address.
    - `GET /api/cluster/join-info`: Serves connection metadata and copyable one-liner join commands.
  - Enhanced dashboard UI (Cluster Mesh tab) with an interactive **"Connect Any Client Machine"** panel featuring real-time IP input and one-click copy buttons for instant Python run, systemd install, and CLI join.
  - Expanded test suite `tests/test_client_and_cli.py` with `test_normalize_server_url_and_ip_support` and `test_server_distribution_endpoints`.
- **Why:** Allows developers and SREs to run Aegis on any client device simply by pointing to the server's IP address, with zero manual setup or dependency compilation.
- **Validation:** Executed full test suite: `PYTHONPATH=. .venv/bin/pytest tests/ -v`. All 58 tests passed in 9.25s.
- **Limitations:** None.

---

## 2026-10-25T00:15:00+05:30 — Production Client-Server Architecture, Unified CLI, Automated Installer & SRE UI Overhaul

- **Changed:**
  - Implemented `AegisAgent` daemon in `aegis/client/agent.py` enabling distributed worker nodes to report telemetry (CPU, RAM, swap, disk, load averages, PSI, Docker containers) to the Aegis Server control plane via periodic authenticated heartbeats with exponential backoff and graceful node draining on shutdown.
  - Implemented unified developer CLI in `aegis/cli/main.py` with commands: `aegis server`, `aegis agent`, `aegis status`, `aegis nodes [list|drain]`, `aegis incidents [list|diagnose]`, `aegis canary [list|rollback]`, `aegis keys [create|list]`, `aegis mode`, `aegis estop`.
  - Added console entrypoints to `pyproject.toml` (`aegis`, `aegis-server`, `aegis-agent`, `aegis-client`).
  - Authored automated Linux installer script `scripts/install.sh` supporting virtualenv provisioning, CLI symlinking to `/usr/local/bin`, and systemd service installation.
  - Created systemd unit templates `scripts/systemd/aegis-server.service` and `scripts/systemd/aegis-agent.service`.
  - Created `Dockerfile` and `docker-compose.yml` defining production containerization for control plane and node agent.
  - Overhauled web dashboard (`index.html`, `styles.css`, `app.js`):
    - Removed all AI-made cliches: cartoonish glowing box-shadows, oversized empty cards, fake hype badges, and bouncy animations.
    - Implemented high-density SRE theme (neutral slate, high contrast, monospace metrics and IDs).
    - Added live SVG mini-sparkline graphs inside telemetry cards (rolling 25-sample history).
    - Added real-time control plane ping latency meter (`Ping: 8ms`) and connection health indicator.
    - Added keyboard shortcuts (`?` for CLI cheat-sheet modal, `/` for search, `Esc` to close drawers, `1`-`8` to switch tabs).
    - Added interactive Command Palette / CLI cheat-sheet modal with one-click copy buttons and toast feedback.
    - Added search inputs and filter pills for Docker containers (`ALL`, `RUNNING`, `CRASH/STOPPED`) and Incidents (`ALL`, `ACTIVE`, `CRITICAL`, `NEEDS APPROVAL`, `RESOLVED`).
    - Added progress bars for storage partitions, node CPU/RAM usage, and canary rollout traffic steps.
    - Added live autonomous event console with level filtering (`ALL`, `CRIT`, `WARN`, `INFO`) and autoscroll pause/lock.
  - Added `tests/test_client_and_cli.py` (5 tests) verifying agent telemetry sampling, cluster mesh registration, heartbeats, node draining, and CLI subcommands.
- **Why:** Delivers real-world production packaging and ergonomics that DevOps engineers and SREs expect from enterprise tools like Datadog, Prometheus, or HashiCorp Consul.
- **Validation:** Executed full test suite: `PYTHONPATH=. .venv/bin/pytest tests/ -v`. All 56 tests passed in 8.89s.
- **Limitations:** None.

---

## 2026-10-24T23:55:00+05:30 — Aegis AI v2.0.0: Distributed Cluster Mesh, Automated Canary Rollbacks, Alert Storm Dispatcher & RBAC

- **Changed:**
  - Implemented `ClusterMeshManager` in `aegis/cluster/mesh.py` for distributed multi-node fleet management: node registration, periodic telemetry heartbeats, stale node detection, cluster capacity aggregation (CPU, RAM, GPU), and safe node draining (`drain_node`).
  - Implemented `CanaryVerificationEngine` in `aegis/engineering/canary_verifier.py` providing automated canary deployment verification and closed-loop rollbacks: staged progression across traffic steps, metric & SLA observation windows, and immediate automated rollback if error budgets or latency thresholds are breached.
  - Implemented `NotificationDispatcher` in `aegis/notifications/dispatcher.py` providing multi-channel alerting (Slack, PagerDuty, generic Webhooks) with sliding-window alert storm debouncing and deduplication to prevent notification fatigue.
  - Implemented `RBACManager` in `aegis/security/rbac.py` providing cryptographic API key generation (`aegis_sec_...`), SHA-256 token hashing, and role-based access control (`ADMIN`, `OPERATOR`, `READONLY`, `NODE_AGENT`).
  - Created database migration `migrations/002_v2.sql` defining relational schemas for `cluster_nodes`, `canary_rollouts`, `api_keys`, and `notification_channels`.
  - Added new v2.0 REST endpoints in `aegis/api/server.py`:
    - `/api/cluster/nodes/register`, `/api/cluster/nodes/{id}/heartbeat`, `/api/cluster/nodes/{id}/drain`, `/api/cluster/summary`
    - `/api/canary/rollouts`, `/api/canary/rollouts/{id}`, `/api/canary/rollouts/{id}/step`, `/api/canary/rollouts/{id}/rollback`
    - `/api/notifications/channels`, `/api/notifications/dispatch`
    - `/api/security/keys`
  - Hardened state-changing endpoints with RBAC authentication via `_guard_state_changer`, blocking `READONLY` keys with HTTP 403 Forbidden.
  - Enhanced dashboard UI with a dedicated **Cluster Mesh** tab displaying node status, capacity, and canary rollout progress in real time.
  - Added comprehensive v2.0 test suite in `tests/test_v20.py` covering cluster lifecycle, canary verification & automated rollbacks, webhook debouncing, RBAC authorization, and API routes.
- **Why:** Elevates Aegis from a single-host controller into a horizontally scalable, multi-node enterprise cluster control plane with progressive delivery safety, alert fatigue protection, and role-based security.
- **Validation:** Executed full test suite: `PYTHONPATH=. .venv/bin/pytest tests/ -v`. All 51 tests passed in 7.53s.
- **Limitations:** None. Full backward compatibility maintained.

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
