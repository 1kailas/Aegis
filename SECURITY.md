# Security Policy & Safety Architecture — Aegis AI

Aegis AI is designed as a security-critical infrastructure control plane. Autonomous actions are constrained by deterministic safety controllers and least-privilege boundary enforcement.

---

## 1. Threat Model & Mitigations

| Threat | Mitigation in Aegis |
|---|---|
| **AI Prompt Injection & Unsafe Shell Execution** | AI models are never given direct access to a host shell. Remediation actions are typed, allowlisted primitives (`restart_container`, `set_backpressure`, `increase_sampling`). Shell metacharacters and arbitrary commands (`rm -rf`, pipes, subshells) are rejected at the policy gate. |
| **Credential & Secret Leakage** | All environment variables, logs, telemetry payloads, and diagnostic prompts pass through automated regex scrubbers targeting GitHub tokens (`ghp_`, `gho_`), Gemini API keys (`AIzaSy...`), bearer tokens, and private keys. Sandboxed workspaces strip all host secrets prior to test execution. |
| **Runaway Remediation Cascades** | Rate limits, cooldown intervals, and hysteresis hold periods are hardcoded into `ThermalGuardian`, `ResourceGuardian`, and `DockerAdapter`. Containers are limited to a maximum of 3 restarts per 10-minute window. |
| **Malicious or Broken Patches** | Patches are never applied directly to host processes. They must pass scope inspection (rejecting `.env`, workflows, and directory traversals) and succeed in isolated ephemeral git clones before a PR is opened. |
| **Premature Incident Closure** | Pull request opening does NOT mark an incident resolved. Independent live probes (`RecoveryVerifier`) must verify physical metrics have stabilized. |
| **Emergency Halt** | A global hardware emergency stop switch (`POST /api/policy/emergency_stop` or dashboard button) unconditionally suspends all autonomous actions while keeping passive telemetry and alerting active. |

---

## 2. Policy Risk Classifications

- **`LOW` (Permitted Autonomously in `policy_autonomous` Mode)**:
  - `increase_sampling`, `create_or_update_incident`, `set_backpressure`, `clear_backpressure`, `restart_container`, `trim_cache`, `notify`.
- **`MEDIUM` / `HIGH` (Requires Explicit Operator Approval)**:
  - `scale_replicas`, `restart_deployment`, `prune_old_images`, configuration parameter modifications.
- **`PROHIBITED` (Unconditionally Blocked)**:
  - `rm -rf`, raw bash pipes, dropping databases, modifying `/etc`, modifying credentials or `.env`, deleting persistent volumes.

---

## 3. Reporting a Vulnerability

If you discover a security vulnerability in Aegis AI, please report it via private GitHub security advisory at `https://github.com/1kailas/Aegis/security/advisories`.
