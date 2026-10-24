"""Phase 4 automated test suite for Aegis AI.

Covers:
- Isolated workspace manager (ephemeral clone, environment sanitation, execution limits)
- Strict patch scope enforcement and rejection of unsafe patches (.env, workflows, path traversal)
- Validation pipeline (patch apply, test execution, recording exit codes and output)
- Evidence-backed GitHub pull request creation and secret redaction
- Independent post-repair service recovery verification
- Phase 4 API endpoints
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.engineering.github_adapter import GitHubAdapter
from aegis.engineering.recovery_verifier import RecoveryStatus, RecoveryVerifier
from aegis.engineering.workspace import (
    IsolatedWorkspaceManager,
    ValidationReport,
    ValidationStepResult,
)


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_p4.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_patch_scope_validation_safe(tmp_path):
    mgr = IsolatedWorkspaceManager(base_repo_dir=tmp_path, workspaces_root=tmp_path / "workspaces")
    safe_patch = """diff --git a/aegis/engine.py b/aegis/engine.py
--- a/aegis/engine.py
+++ b/aegis/engine.py
@@ -10,3 +10,3 @@
-# test comment
+# updated test comment
"""
    valid, files, reason = mgr.inspect_patch_scope(safe_patch)
    assert valid is True
    assert "aegis/engine.py" in files
    assert reason is None


def test_patch_scope_validation_rejection_env(tmp_path):
    mgr = IsolatedWorkspaceManager(base_repo_dir=tmp_path, workspaces_root=tmp_path / "workspaces")
    unsafe_patch = """diff --git a/.env b/.env
--- a/.env
+++ b/.env
@@ -1,1 +1,1 @@
-SECRET=123
+SECRET=456
"""
    valid, files, reason = mgr.inspect_patch_scope(unsafe_patch)
    assert valid is False
    assert "violates scope policy" in reason


def test_patch_scope_validation_rejection_path_traversal(tmp_path):
    mgr = IsolatedWorkspaceManager(base_repo_dir=tmp_path, workspaces_root=tmp_path / "workspaces")
    traversal_patch = """diff --git a/../../etc/passwd b/../../etc/passwd
--- a/../../etc/passwd
+++ b/../../etc/passwd
@@ -1,1 +1,1 @@
+malicious_entry
"""
    valid, files, reason = mgr.inspect_patch_scope(traversal_patch)
    assert valid is False
    assert "violates scope policy" in reason


def test_sanitized_environment_strips_secrets(tmp_path):
    mgr = IsolatedWorkspaceManager(base_repo_dir=tmp_path, workspaces_root=tmp_path / "workspaces")
    with patch.dict("os.environ", {"GEMINI_API_KEY": "secret123", "GITHUB_TOKEN": "ghp_tok", "SAFE_VAR": "hello"}):
        sanitized = mgr._build_sanitized_env()
        assert "SAFE_VAR" in sanitized
        assert "GEMINI_API_KEY" not in sanitized
        assert "GITHUB_TOKEN" not in sanitized


def test_github_adapter_status_and_body_evidence(tmp_path):
    adapter = GitHubAdapter(repo_dir=tmp_path, owner="1kailas", repo="Aegis", token="mock-token")
    status = adapter.status()
    assert status["owner"] == "1kailas"
    assert status["repo"] == "Aegis"
    assert status["authenticated"] is True

    inc = {
        "id": "INC-2026-TEST",
        "severity": "critical",
        "category": "container_oom",
        "summary": "Process OOM killed due to memory exhaustion",
    }
    val = ValidationReport(
        task_id="task-01",
        passed=True,
        scope_valid=True,
        changed_files=["aegis/engine.py"],
        steps=[ValidationStepResult(command="pytest -q", exit_code=0, duration_s=1.2, stdout="OK", stderr="", passed=True)],
    )

    body = adapter._build_pr_body(inc, val)
    assert "INC-2026-TEST" in body
    assert "pytest -q" in body
    assert "exit code 0" in body
    assert "aegis/engine.py" in body
    assert "Security & Policy Guardrails" in body

    # Test dry run PR
    res = adapter.create_repair_pr(inc, val, "aegis/repair-test", "fix: resolve oom", mode="dry_run")
    assert res.success is True
    assert res.pr_number is not None


def test_recovery_verifier_independent_outcomes(app_ctx: AppContext):
    verifier = app_ctx.recovery_verifier

    # 1. Healthy probe outcome -> CONFIRMED
    rec_pass = verifier.verify_workload_recovery(
        incident_id="INC-REC-1",
        target="service-api",
        check_fn=lambda: (True, "All probes responding 200 OK", {"latency_ms": 15}),
    )
    assert rec_pass.status == RecoveryStatus.CONFIRMED

    # 2. Unhealthy probe outcome -> FAILED
    rec_fail = verifier.verify_workload_recovery(
        incident_id="INC-REC-2",
        target="service-api",
        check_fn=lambda: (False, "Health endpoint returning 503 Service Unavailable", {"latency_ms": 5000}),
    )
    assert rec_fail.status == RecoveryStatus.FAILED

    # 3. Exception in check -> INCONCLUSIVE
    def crash_check():
        raise ConnectionResetError("Connection reset by peer")

    rec_err = verifier.verify_workload_recovery(
        incident_id="INC-REC-3",
        target="service-api",
        check_fn=crash_check,
    )
    assert rec_err.status == RecoveryStatus.INCONCLUSIVE


def test_phase4_api_endpoints(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. GitHub status
    r_gh = client.get("/api/github/status")
    assert r_gh.status_code == 200
    assert "owner" in r_gh.json()

    # 2. Repair validate rejection for bad scope (.env)
    r_val_bad = client.post("/api/repair/validate", json={
        "task_id": "test-bad",
        "patch": "diff --git a/.env b/.env\n--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n+SECRET=1\n"
    })
    assert r_val_bad.status_code == 200
    assert r_val_bad.json()["passed"] is False
    assert r_val_bad.json()["scope_valid"] is False

    # 3. Submit PR dry run
    inc = app_ctx.incidents.create(
        category="container_crash", severity="critical", title="Worker crash", summary="test"
    )
    val_report_mock = {
        "passed": True,
        "scope_valid": True,
        "changed_files": ["aegis/engine.py"],
        "steps": [{"command": "pytest", "exit_code": 0, "duration_s": 0.5, "passed": True}],
    }
    r_pr = client.post("/api/repair/submit_pr", json={
        "incident_id": inc["id"],
        "task_id": "task-test-pr",
        "mode": "dry_run",
        "validation_report": val_report_mock,
    })
    assert r_pr.status_code == 200
    assert r_pr.json()["success"] is True

    # 4. Verify recovery
    r_rec = client.post("/api/repair/verify_recovery", json={
        "incident_id": inc["id"],
        "target": "host",
    })
    assert r_rec.status_code == 200
    assert r_rec.json()["status"] == "CONFIRMED"
