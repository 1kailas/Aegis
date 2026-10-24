# Aegis AI — Agent Engineering & Operational Protocol

This document defines the operational protocol for autonomous engineering agents (such as **Devin**, Claude Code, Cursor, and Antigravity) developing or performing repairs within **Aegis AI**.

---

## 1. Cardinal Architectural Invariant

> **"Aegis monitors and orchestrates. Deterministic controllers enforce safety. AI investigates and engineers fixes. Independent verification determines whether the fix actually worked."**

When acting as an agent within or alongside Aegis:
- **Never allow AI to directly control critical safety mechanisms**: Physical, thermal, and saturation thresholds are deterministic code.
- **Never execute arbitrary shell commands on the host**: All operations must be allowlisted, typed, and evaluated through the `ActionPolicyEngine`.
- **Never claim a repair succeeded merely because a patch was generated or a PR was created**: Service recovery must be independently confirmed via live probes (`RecoveryVerifier`).
- **Never expose secrets or credentials**: API keys (`GEMINI_API_KEY`, `GITHUB_TOKEN`, `DEVIN_API_KEY`) must never appear in telemetry, event logs, prompt bodies, commit messages, or pull requests.

---

## 2. Agent Repair Workflow (Devin Protocol)

When an infrastructure or code incident is detected, Aegis formats a structured `RepairTask` and delegates it to Devin:

```
[Incident Detected]
       │
       ▼
[Multi-Tier Diagnoser]  ──▶  Identifies root cause & affected components
       │
       ▼
[Devin Adapter]         ──▶  Dispatches structured RepairTask
       │
       ▼
[Devin Agent Engine]    ──▶  Drafts targeted code/config patch
       │
       ▼
[Isolated Workspace]    ──▶  1. Inspects patch scope (rejects .env, workflows, traversals)
                             2. Applies patch in ephemeral git clone
                             3. Runs test suite with sanitized environment
       │
       ▼
[GitHub Adapter]        ──▶  Opens evidence-backed PR with test commands & exit codes
       │
       ▼
[Recovery Verifier]     ──▶  Independently verifies live workload metrics stabilize
```

### Prohibited Patch Patterns
The following modifications are automatically rejected by `IsolatedWorkspaceManager.inspect_patch_scope()`:
- Modifications to `.env`, `.env.*`, `*.pem`, `*.key`
- Modifications to GitHub Actions workflows (`.github/workflows/`)
- Directory traversal sequences (`../`)
- Modifications outside the repository boundary

---

## 3. Running Checks & Verification

Agents modifying this codebase MUST run and verify the test suite:

```bash
cd /home/kailas/Projects/idk
source .venv/bin/activate

# Execute the 40 automated tests
PYTHONPATH=. pytest tests/ -v
```

All 40 unit and integration tests must pass cleanly before any change is considered ready.

---

## 4. Operational API Reference for Agents

| Task | Endpoint | Payload / Behavior |
|---|---|---|
| **Retrieve Incident** | `GET /api/incidents` | Lists active incidents and evidence |
| **Trigger Diagnosis** | `POST /api/incidents/{id}/diagnose` | Returns multi-tier assessment |
| **Fetch Devin Task** | `GET /api/devin/tasks` | Lists queued and active repair briefs |
| **Validate Patch** | `POST /api/repair/validate` | `{"task_id": "...", "patch": "diff...", "test_commands": [...]}` |
| **Submit PR** | `POST /api/repair/submit_pr` | Submits evidence-backed GitHub PR |
| **Verify Recovery** | `POST /api/repair/verify_recovery` | `{"incident_id": "...", "target": "..."}` |
| **Emergency Halt** | `POST /api/policy/emergency_stop` | `{"enabled": true}` blocks all mutations |
