"""Tests for Incident Causality & Service Dependency Topology Engine (Kea-style with Auto-PR).

Verifies:
1. Deterministic root-cause candidate ranking over service dependencies.
2. Anti-red-herring decoy rejection with explicit reason codes (NO_DEPENDENCY_PATH).
3. Live service topology and causal path extraction.
4. End-to-end Auto-PR metadata and Predict-Then-Verify closure.
"""

import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.intelligence.causality import CausalityEngine


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_causality.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_causality_analysis_structure(app_ctx: AppContext):
    """Verify CausalityEngine generates structured topology, ranking, and Auto-PR."""
    inc = app_ctx.incidents.create(
        category="software_bug",
        severity="critical",
        title="500 Internal Error on checkout payment",
        summary="ZeroDivisionError in demo-target/app.py",
        evidence=[
            {"target_name": "Checkout Payment Service", "target_url": "http://127.0.0.1:5050/health"},
            {"kind": "pr_patch", "engine": "groq", "model": "llama-3.1-8b-instant", "patch": "--- a/app.py\n+++ b/app.py\n- 0\n+ 1", "pull_request_url": "https://github.com/1kailas/Aegis/pull/123"},
        ],
    )

    analysis = app_ctx.causality.analyze_incident(inc)
    assert analysis["incident_id"] == inc["id"]
    assert analysis["target_service"] == "Checkout Payment Service"
    assert "topology" in analysis
    assert len(analysis["topology"]["nodes"]) >= 3
    assert len(analysis["topology"]["edges"]) >= 2
    assert analysis["topology"]["blast_radius_score"] in ("LOW", "MEDIUM", "HIGH")

    # Check root-cause candidates
    candidates = analysis["root_cause_ranking"]
    assert len(candidates) >= 2
    
    # 1. Primary candidate
    ranked = next(c for c in candidates if c["status"] == "RANKED_ROOT_CAUSE")
    assert "demo-target/app.py" in ranked["target_file"]
    assert ranked["factor_scores"]["dependency_path_score"] == 1.0
    assert ranked["factor_scores"]["composite_score"] >= 0.85

    # 2. Decoy / Red Herring candidate
    decoy = next(c for c in candidates if c["status"] == "REJECTED_DECOY")
    assert "Notifications" in decoy["name"]
    assert decoy["factor_scores"]["dependency_path_score"] == 0.0
    assert "NO_DEPENDENCY_PATH" in decoy["rejection_reason"]

    # Check Auto-PR metadata
    assert analysis["auto_pr"]["auto_pr_enabled"] is True
    assert analysis["auto_pr"]["pr_engine"] == "groq"
    assert "pull/123" in analysis["auto_pr"]["pull_request_url"]
    assert analysis["auto_pr"]["has_patch"] is True


def test_causality_api_endpoint(app_ctx: AppContext):
    """Verify GET /api/incidents/{id}/causality returns full analysis and 404 for unknown id."""
    app = create_app(app_ctx)
    client = TestClient(app)

    inc = app_ctx.incidents.create(
        category="http_5xx",
        severity="critical",
        title="Payment gateway timeout",
        summary="Consecutive 504 Gateway Timeouts",
    )

    res = client.get(f"/api/incidents/{inc['id']}/causality")
    assert res.status_code == 200
    data = res.json()
    assert data["incident_id"] == inc["id"]
    assert "root_cause_ranking" in data
    assert "predict_and_verify" in data
    assert "auto_pr" in data

    # 404 check
    res_404 = client.get("/api/incidents/INC-NONEXISTENT/causality")
    assert res_404.status_code == 404
