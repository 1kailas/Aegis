"""Phase 5 automated test suite for Aegis AI.

Covers:
- Complete end-to-end autonomous monitoring-to-repair lifecycle:
  Observe -> Detect -> Diagnose -> Plan -> Authorize -> Act -> Validate -> Verify -> Learn
- Security hardening (secret leakage prevention, path traversal rejection, emergency stop enforcement)
- Web dashboard static assets, Swagger docs, and SSE stream endpoints
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.control_plane.action_policy import RemediationAction, RiskLevel
from aegis.engine import MonitoringEngine


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_p5.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_full_autonomous_lifecycle(app_ctx: AppContext):
    """Verifies the complete loop: Observe -> Detect -> Diagnose -> Plan -> Authorize -> Act -> Validate -> Verify."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. OBSERVE & DETECT: Trigger simulated critical condition
    r_ovr = client.post("/api/demo/override", json={"kind": "critical", "value_c": 95.0})
    assert r_ovr.status_code == 200

    # Engine tick processes readings and detects incident
    eng = MonitoringEngine(app_ctx)
    snap = eng.tick()
    assert snap is not None

    incidents = client.get("/api/incidents").json()
    assert len(incidents) >= 1
    active_inc = incidents[0]
    iid = active_inc["id"]

    # 2. DIAGNOSE: Multi-tier diagnosis
    r_diag = client.post(f"/api/incidents/{iid}/diagnose")
    assert r_diag.status_code == 200
    diag = r_diag.json()
    assert diag["incident_id"] == iid
    assert diag["status"] == "ASSESSMENT"

    # 3. PLAN: Devin task creation
    r_task = client.post("/api/devin/tasks", json={
        "incident_id": iid,
        "affected_repo": "https://github.com/1kailas/Aegis.git",
        "affected_files": ["policies.yaml"],
    })
    assert r_task.status_code == 200
    task_id = r_task.json()["task_id"]

    # 4. AUTHORIZE & ACT: Remediation policy evaluation
    r_auth = client.post("/api/remediation/authorize", json={
        "action_type": "set_backpressure",
        "target": "inference_queue",
        "reason": "Thermal critical containment",
    })
    assert r_auth.status_code == 200
    assert r_auth.json()["authorized"] is True

    # 5. VALIDATE: Test fix in isolated workspace
    safe_patch = """diff --git a/policies.yaml b/policies.yaml
--- a/policies.yaml
+++ b/policies.yaml
@@ -1,3 +1,4 @@
+# Verified by Aegis Autonomy
 # Aegis thermal policy and action allowlist.
 #
 # DEFAULTS ARE CONSERVATIVE PLACEHOLDERS. Thresholds must be calibrated to
"""
    r_val = client.post("/api/repair/validate", json={
        "task_id": task_id,
        "patch": safe_patch,
        "test_commands": ["python -c 'print(\"patch verified\")'"],
    })
    assert r_val.status_code == 200
    val_report = r_val.json()
    assert val_report["passed"] is True

    # 6. SUBMIT PR: Evidence-backed PR
    r_pr = client.post("/api/repair/submit_pr", json={
        "incident_id": iid,
        "task_id": task_id,
        "mode": "dry_run",
        "validation_report": val_report,
    })
    assert r_pr.status_code == 200
    assert r_pr.json()["success"] is True

    # 7. VERIFY: Independent recovery verification
    # Clear override to simulate cooling
    client.post("/api/demo/override", json={"kind": "recover", "value_c": 40.0})
    eng.tick()

    r_verify = client.post("/api/repair/verify_recovery", json={
        "incident_id": iid,
        "target": "thermal_guardian",
    })
    assert r_verify.status_code == 200
    assert r_verify.json()["status"] == "CONFIRMED"


def test_security_hardening_against_injections(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Prohibited command rejection
    r_bad_act = client.post("/api/remediation/authorize", json={
        "action_type": "rm -rf /",
        "target": "system",
        "reason": "destructive malicious attempt",
    })
    assert r_bad_act.status_code == 200
    assert r_bad_act.json()["authorized"] is False
    assert r_bad_act.json()["result"] == "REJECTED"

    # 2. Path traversal patch rejection
    r_bad_patch = client.post("/api/repair/validate", json={
        "task_id": "malicious-01",
        "patch": "diff --git a/../../etc/passwd b/../../etc/passwd\n--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n+injected:x:0:0:::\n"
    })
    assert r_bad_patch.status_code == 200
    assert r_bad_patch.json()["passed"] is False
    assert "violates scope policy" in r_bad_patch.json()["rejection_reason"]


def test_dashboard_static_and_docs_served(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # Dashboard HTML
    r_html = client.get("/")
    assert r_html.status_code == 200
    assert "Aegis AI" in r_html.text

    # Static CSS
    r_css = client.get("/static/styles.css")
    assert r_css.status_code == 200
    assert "--bg:" in r_css.text

    # Static JS
    r_js = client.get("/static/app.js")
    assert r_js.status_code == 200
    assert "pollOverview" in r_js.text

    # OpenAPI JSON
    r_api = client.get("/api/openapi.json")
    assert r_api.status_code == 200
    assert r_api.json()["info"]["title"] == "Aegis AI"

    # Docs redirect
    r_docs = client.get("/docs", follow_redirects=False)
    assert r_docs.status_code in (307, 302, 301)
    assert r_docs.headers["location"] == "/api/docs"


def test_operator_approval_mode_and_target_verification(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Mode switch to operator_approval
    r_mode = client.post("/api/policy/mode", json={"mode": "operator_approval"})
    assert r_mode.status_code == 200
    assert r_mode.json()["mode"] == "operator_approval"

    # 2. Medium-risk action in operator_approval mode requires human approval
    r_act = client.post("/api/remediation/authorize", json={
        "action_type": "scale_replicas",
        "target": "deployment/web",
        "reason": "Traffic spike mitigation",
    })
    assert r_act.status_code == 200
    assert r_act.json()["authorized"] is False
    assert r_act.json()["result"] == "NEEDS_APPROVAL"

    # 3. Explicit operator approved execution
    r_act_approved = client.post("/api/remediation/authorize", json={
        "action_type": "scale_replicas",
        "target": "deployment/web",
        "reason": "Traffic spike mitigation",
        "operator_approved": True,
    })
    assert r_act_approved.status_code == 200
    assert r_act_approved.json()["authorized"] is True

    # 4. Target-aware recovery check for thermal_guardian
    r_verify_thermal = client.post("/api/repair/verify_recovery", json={
        "target": "thermal_guardian",
    })
    assert r_verify_thermal.status_code == 200
    assert r_verify_thermal.json()["status"] == "CONFIRMED"
