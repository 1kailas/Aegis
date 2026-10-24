"""Tests for Groq Autonomous PR Code Repair & Patch Generation Engine.

Verifies:
1. Environment and configuration loading for Groq.
2. Strict hourly rate-limiting guardrails (protecting API quota).
3. Secret scrubbing for GROQ_API_KEY across all subsystems.
4. Autonomous PR generation and unified diff extraction.
5. End-to-end dispatch through DevinAdapter fallback.
6. Topology graph and live key testing integration.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.engineering.devin_adapter import DevinAdapter
from aegis.engineering.groq_repair import GroqRepairEngine
from aegis.intelligence.diagnosis import IncidentDiagnoser
from aegis.orchestration.docker_adapter import DockerAdapter


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_groq.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_groq_config_loading(app_ctx: AppContext):
    """Verify Groq config fields are loaded properly with defaults."""
    cfg = app_ctx.config
    assert hasattr(cfg.models, "groq_model")
    assert cfg.models.groq_model in ("llama-3.1-8b-instant", "qwen/qwen3.8-27b")
    assert cfg.models.groq_max_requests_per_hour >= 1
    assert hasattr(cfg, "groq_api_key")


def test_groq_secret_scrubbing():
    """Verify GROQ_API_KEY is scrubbed from logs and prompts."""
    raw_key = "gsk_test0123456789abcdefghijklmnopqrstuvwxyzABCDEF"
    
    # 1. GroqRepairEngine scrubbing
    engine = GroqRepairEngine(api_key=raw_key)
    scrubbed = engine.scrub_key(f"Sending error with auth Bearer {raw_key}")
    assert raw_key not in scrubbed
    assert "[REDACTED_GROQ_KEY]" in scrubbed

    # 2. Diagnoser scrubbing
    diagnoser = IncidentDiagnoser(ollama_url="http://127.0.0.1:11434")
    incident = {
        "id": "INC-TEST-01",
        "category": "software",
        "severity": "critical",
        "title": "Auth failure",
        "summary": f"Header contained {raw_key} token",
    }
    built_prompt = diagnoser._prepare_context(incident, {})
    assert raw_key not in built_prompt
    assert "[REDACTED_GROQ_KEY]" in built_prompt

    # 3. Docker adapter scrubbing
    docker = DockerAdapter()
    docker_sanitized = docker._sanitize_logs(f"Container log output: token={raw_key}")
    assert raw_key not in docker_sanitized
    assert "[REDACTED_GROQ_KEY]" in docker_sanitized


def test_groq_rate_limiting_enforcement():
    """Verify strict hourly rate limiting prevents runaway request loops."""
    engine = GroqRepairEngine(api_key="gsk_mock_valid_key_12345678901234567890", max_requests_per_hour=3)
    
    # Initially allowed
    allowed, msg = engine.check_rate_limit()
    assert allowed is True
    assert "0/3" in msg

    # Simulate 3 requests in the last hour
    import time
    now = time.time()
    engine._request_history.extend([now - 120.0, now - 60.0, now - 10.0])

    # 4th request must be rejected
    allowed, reason = engine.check_rate_limit()
    assert allowed is False
    assert "Rate limit safety guardrail triggered" in reason
    assert engine.status()["quota_status"] == "RATE_LIMITED"


def test_groq_patch_extraction_and_pr_meta():
    """Verify clean extraction of unified diffs and PR metadata generation."""
    engine = GroqRepairEngine(api_key="gsk_mock_valid_key_12345678901234567890")
    
    raw_llm_output = (
        "Here is the fix for the bug:\n"
        "```diff\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        "- return 10 / 0\n"
        "+ return 10 / 1\n"
        "```\n"
        "Hope this helps!"
    )
    diff = engine._extract_diff(raw_llm_output, "app.py")
    assert "--- a/app.py" in diff
    assert "+++ b/app.py" in diff
    assert "- return 10 / 0" in diff
    assert "+ return 10 / 1" in diff
    assert "Hope this helps!" not in diff

    # Test PR publishing metadata
    pr_meta = engine.publish_pr("INC-101", "Fix 500 error", diff)
    assert pr_meta["success"] is True
    assert "aegis/repair-101" in pr_meta["branch_name"]
    assert "pull/" in pr_meta["pr_url"]


def test_groq_dispatch_integration(app_ctx: AppContext):
    """Verify DevinAdapter uses Groq to generate a real patch and PR when Devin is unconfigured."""
    # Mock Groq repair engine
    mock_groq = MagicMock(spec=GroqRepairEngine)
    mock_groq.is_configured.return_value = True
    mock_groq.generate_repair_patch.return_value = {
        "success": True,
        "model": "llama-3.1-8b-instant",
        "patch": "--- a/server.py\n+++ b/server.py\n@@ -1 +1 @@\n- bug\n+ fixed",
    }
    mock_groq.publish_pr.return_value = {
        "success": True,
        "pr_url": "https://github.com/1kailas/Aegis/pull/42",
        "branch_name": "aegis/repair-inc-42",
    }

    adapter = DevinAdapter(app_ctx.db, api_key=None, groq_engine=mock_groq)

    inc = app_ctx.incidents.create(
        category="software_bug",
        severity="critical",
        title="500 Internal Error on checkout",
        summary="Null pointer on checkout",
    )
    task = adapter.create_repair_task(inc)
    
    # Dispatch task
    res = adapter.dispatch_task(task)
    assert res["success"] is True
    assert res["status"] == "PR_OPENED"
    assert res["engine"] == "groq"
    assert res["pull_request_url"] == "https://github.com/1kailas/Aegis/pull/42"
    assert "fixed" in res["patch"]

    # Check incident was transitioned to PR_OPENED
    updated_inc = app_ctx.incidents.get(inc["id"])
    assert updated_inc["state"] == "PR_OPENED"
    ev_list = updated_inc["evidence"] if isinstance(updated_inc["evidence"], list) else json.loads(updated_inc["evidence"])
    assert any(e.get("kind") == "pr_patch" for e in ev_list)


def test_groq_api_graph_and_test_key_endpoints(app_ctx: AppContext):
    """Verify Groq appears in the topology graph and can be ping-tested."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Security graph check
    r_graph = client.get("/api/security/graph")
    assert r_graph.status_code == 200
    graph_data = r_graph.json()
    
    groq_node = next((n for n in graph_data["nodes"] if n["id"] == "key-groq"), None)
    assert groq_node is not None
    assert groq_node["role"] == "Autonomous Code Repair & PR Generator"
    assert groq_node["type"] == "cloud_ai"

    # 2. Test key endpoint for Groq
    r_test = client.post("/api/security/test-key", json={"node_id": "key-groq"})
    assert r_test.status_code == 200
    test_data = r_test.json()
    assert test_data["node_id"] == "key-groq"
    # If key configured in environment, status is HEALTHY
    if app_ctx.config.groq_api_key:
        assert test_data["status"] == "HEALTHY"
        assert test_data["success"] is True


def test_api_health_includes_groq_and_pr_engine(app_ctx: AppContext):
    """Verify /api/health and /api/models/dual report Groq status honestly."""
    app = create_app(app_ctx)
    client = TestClient(app)

    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    h_data = r_health.json()
    assert "groq_configured" in h_data
    assert "active_pr_engine" in h_data

    r_dual = client.get("/api/models/dual")
    assert r_dual.status_code == 200
    dual_data = r_dual.json()
    assert "pr_engine" in dual_data["software_model"]
