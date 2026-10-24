"""Tests for Dual-Model Architecture: Software Intelligence vs Hardware Intelligence.

Validates:
1. Model Configuration & Separation (Software Model vs Hardware Model)
2. Triage Routing (Code/5xx -> Software Model; Compute/Thermals -> Hardware Model)
3. Hardware Instance Auto-Scaling Execution & Manifest Generation
4. Autonomous Remediation Allowlist & Safety Gate for Scaling
5. REST Endpoints (/api/models/dual, /api/hardware/scale-instances, /api/health)
6. Self-Healing Closed-Loop execution for both Software (Devin PR) and Hardware (Scaling)
"""

from __future__ import annotations

import json
import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.control_plane.action_policy import ActionCategory, RiskLevel
from aegis.intelligence.diagnosis import IncidentDiagnoser


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_dual_models.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_dual_model_config():
    """Verify AppConfig supports specialized software_model and hardware_model."""
    cfg = load_config()
    assert hasattr(cfg.models, "software_model")
    assert hasattr(cfg.models, "hardware_model")
    assert cfg.models.software_model != ""
    assert cfg.models.hardware_model != ""


def test_software_incident_routing():
    """Verify application code & 5xx outages route to the Software Model."""
    diagnoser = IncidentDiagnoser(gemini_api_key=None)
    inc = {
        "id": "INC-SOFT-01",
        "category": "target_outage",
        "severity": "critical",
        "title": "HTTP 500 Outage on checkout service",
        "summary": "Checkout microservice returning HTTP 500: Database connection timeout",
        "evidence": [{"status_code": 500, "url": "http://127.0.0.1:5050/checkout"}],
    }
    assert diagnoser.is_hardware_incident(inc) is False
    diag = diagnoser.diagnose(inc)
    assert diag.model_role == "software"
    assert diag.is_code_or_config_issue is True


def test_hardware_incident_routing():
    """Verify compute saturation, thermals, and resource pressure route to the Hardware Model."""
    diagnoser = IncidentDiagnoser(gemini_api_key=None)
    inc = {
        "id": "INC-HARD-01",
        "category": "hardware_resource_pressure",
        "severity": "critical",
        "title": "Compute saturation: CPU at 96% and VRAM exhausted",
        "summary": "Host compute capacity exhausted under peak inference load",
        "evidence": [{"cpu_percent": 96.0, "vram_percent": 95.0}],
    }
    assert diagnoser.is_hardware_incident(inc) is True
    diag = diagnoser.diagnose(inc)
    assert diag.model_role == "hardware"
    assert diag.is_code_or_config_issue is False
    actions = [a["action"] for a in diag.recommended_actions]
    assert "scale_instances" in actions or "evict_idle_model" in actions


def test_action_policy_allows_scale_instances(app_ctx: AppContext):
    """Verify scale_instances is allowlisted as low-risk autonomous action."""
    action = app_ctx.remediation.evaluate_proposal(
        action_type="scale_instances",
        target="ai-inference-cluster",
        reason="Hardware model scaling replicas",
    )
    assert action.risk_level == RiskLevel.LOW
    assert action.category == ActionCategory.AUTOMATIC_EXECUTE


def test_deployer_scale_instances(app_ctx: AppContext):
    """Verify OneClickDeployer scales replica count and updates manifests."""
    deployer = app_ctx.deployer
    res1 = deployer.scale_instances(target="worker-mesh", delta=1)
    assert res1["success"] is True
    assert res1["previous_replicas"] == 1
    assert res1["new_replicas"] == 2

    # Scale again
    res2 = deployer.scale_instances(target="worker-mesh", delta=1)
    assert res2["success"] is True
    assert res2["previous_replicas"] == 2
    assert res2["new_replicas"] == 3


def test_hardware_scale_instances_api(app_ctx: AppContext):
    """Verify POST /api/hardware/scale-instances creates scaling incident and returns scaled count."""
    app = create_app(app_ctx)
    client = TestClient(app)

    res = client.post("/api/hardware/scale-instances", json={"target": "demo-cluster", "delta": 1})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["model_role"] == "hardware"
    assert data["new_replicas"] >= 2
    assert "incident_id" in data

    # Verify incident was recorded
    inc = app_ctx.incidents.get(data["incident_id"])
    assert inc is not None
    assert inc["category"] == "hardware_autoscaling"
    assert inc["state"] == "RECOVERY_VERIFIED"


def test_dual_models_api_endpoint(app_ctx: AppContext):
    """Verify /api/models/dual and /api/health report both Software & Hardware models."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Check /api/models/dual
    res = client.get("/api/models/dual")
    assert res.status_code == 200
    data = res.json()
    assert "software_model" in data
    assert "hardware_model" in data
    assert data["software_model"]["role"] == "Software Intelligence"
    assert data["hardware_model"]["role"] == "Hardware & Scaling Intelligence"
    assert len(data["hardware_model"]["capabilities"]) > 0

    # 2. Check /api/health includes dual_models
    h_res = client.get("/api/health")
    assert h_res.status_code == 200
    h_data = h_res.json()
    assert "dual_models" in h_data
    assert "software_model" in h_data["dual_models"]
    assert "hardware_model" in h_data["dual_models"]


def test_self_healing_handles_hardware_scaling(app_ctx: AppContext):
    """Verify SelfHealingEngine executes scale_instances on hardware saturation incident."""
    app_ctx.flags.set_mode("policy_autonomous")
    inc = app_ctx.incidents.create(
        category="hardware_resource_pressure",
        severity="critical",
        title="Compute load saturation (>95%)",
        summary="Sustained compute saturation breach on AI worker cluster",
    )
    report = app_ctx.self_healing.run_reconciliation_cycle()
    assert report.diagnosed_count >= 1
    assert report.actions_succeeded >= 1

    # Verify incident contained
    updated = app_ctx.incidents.get(inc["id"])
    assert updated["state"] == "CONTAINED"


def test_security_key_topology_graph_endpoint(app_ctx: AppContext):
    """Verify GET /api/security/graph returns full node/edge topology and summary metrics."""
    app = create_app(app_ctx)
    client = TestClient(app)

    res = client.get("/api/security/graph")
    assert res.status_code == 200
    data = res.json()
    assert "nodes" in data
    assert "edges" in data
    assert "summary" in data

    # Check node categories
    node_ids = [n["id"] for n in data["nodes"]]
    assert "hub-core" in node_ids
    assert "key-gemini" in node_ids
    assert "key-devin" in node_ids
    assert "key-github" in node_ids
    assert "key-ollama" in node_ids

    # Check summary metrics
    summary = data["summary"]
    assert summary["total_keys"] >= 4
    assert summary["cloud_ai_count"] >= 2
    assert "healthy" in summary


def test_security_test_key_endpoint(app_ctx: AppContext):
    """Verify POST /api/security/test-key tests live connectivity and measures latency."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Test Ollama
    r_ollama = client.post("/api/security/test-key", json={"node_id": "key-ollama"})
    assert r_ollama.status_code == 200
    d_ollama = r_ollama.json()
    assert "latency_ms" in d_ollama
    assert "status" in d_ollama

    # 2. Test GitHub
    r_gh = client.post("/api/security/test-key", json={"node_id": "key-github"})
    assert r_gh.status_code == 200
    d_gh = r_gh.json()
    assert d_gh["success"] is True

    # 3. Test RBAC Key
    r_rbac = client.post("/api/security/test-key", json={"node_id": "key-rbac-dev"})
    assert r_rbac.status_code == 200
    d_rbac = r_rbac.json()
    assert d_rbac["success"] is True
    assert d_rbac["status"] == "VERIFIED"

