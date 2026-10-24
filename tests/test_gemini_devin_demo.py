"""Aegis AI — Devin Hacks Hackathon Features Test Suite.

Validates:
1. Gemini structured JSON diagnosis (Tier 2) & fallback chain
2. Monitored Website Targets flow (registration, probing, automated incident creation)
3. Devin repair task brief generation with incident diagnostics & polling
4. Safety Demonstration endpoint (deterministic HTTP 403 Forbidden rejection of 'rm -rf /')
5. Hardware / ResourceGuardian simulation endpoint
6. End-to-end closed-loop recovery verification and MTTR headline metrics
7. Secret scrubbing and /api/health configuration indicators
"""

from __future__ import annotations

import json
import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.intelligence.diagnosis import DiagnosisResult, DiagnosisSchema, IncidentDiagnoser


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_hackathon.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_gemini_structured_schema_validation():
    """Verify DiagnosisSchema supports root_cause, confidence, affected_files, reproduction_steps, acceptance_criteria."""
    sample_json = json.dumps({
        "root_cause": "Unhandled null pointer exception in checkout.py line 42",
        "confidence": 0.94,
        "affected_files": ["payment_gateway/checkout.py"],
        "reproduction_steps": ["POST /checkout with empty token", "Observe 500 error"],
        "acceptance_criteria": ["Return 400 Bad Request", "Ensure zero 500 regressions"],
        "is_code_or_config_issue": True,
    })
    validated = DiagnosisSchema.model_validate_json(sample_json)
    assert validated.root_cause == "Unhandled null pointer exception in checkout.py line 42"
    assert validated.root_cause_hypothesis == validated.root_cause
    assert validated.confidence == 0.94
    assert validated.confidence_score == 0.94
    assert "payment_gateway/checkout.py" in validated.affected_files
    assert len(validated.reproduction_steps) == 2
    assert len(validated.acceptance_criteria) == 2


def test_diagnoser_multi_tier_fallback_and_redaction():
    """Verify diagnoser redacts both Gemini and Devin keys and falls back deterministically."""
    diagnoser = IncidentDiagnoser(
        ollama_url="http://127.0.0.1:9999",
        gemini_api_key="mock-key-for-test",
    )
    inc = {
        "id": "INC-TEST-099",
        "category": "auth_failure",
        "title": "Auth failure",
        "summary": "Key failed: AIzaSyC2o32gQbZ-yQG-1N2h9fLfvm8lhnhs3cw and devin_1234567890abcdef123456",
    }
    context = diagnoser._prepare_context(inc, None)
    assert "AIzaSy" not in context
    assert "[REDACTED_GEMINI_KEY]" in context
    assert "devin_" not in context
    assert "[REDACTED_DEVIN_KEY]" in context

    # Test deterministic fallback
    res = diagnoser.diagnose(inc)
    assert res.provider == "deterministic_expert"
    assert res.incident_id == "INC-TEST-099"
    assert res.root_cause != ""


def test_health_endpoint_reports_configuration(app_ctx: AppContext):
    """Verify /api/health exposes gemini_configured and devin_configured without leaking secrets."""
    app = create_app(app_ctx)
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert "gemini_configured" in data
    assert "devin_configured" in data
    assert isinstance(data["gemini_configured"], bool)
    assert isinstance(data["devin_configured"], bool)
    # Ensure raw keys are NEVER in response
    resp_text = json.dumps(data)
    assert "AIzaSy" not in resp_text
    assert "Bearer" not in resp_text


def test_monitored_targets_crud_and_probing(app_ctx: AppContext):
    """Verify registering a website target, probing it, and listing via REST API."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Register new target
    payload = {
        "name": "Production Storefront",
        "url": "http://127.0.0.1:8000",
        "health_path": "/api/health",
        "github_repo_url": "https://github.com/1kailas/Aegis",
        "interval_s": 5.0,
    }
    r = client.post("/api/targets", json=payload)
    assert r.status_code == 200
    target_data = r.json()
    assert target_data["name"] == "Production Storefront"
    target_id = target_data["id"]

    # 2. List targets
    r_list = client.get("/api/targets")
    assert r_list.status_code == 200
    targets = r_list.json()
    assert any(t["id"] == target_id for t in targets)

    # 3. Probe target
    r_probe = client.post(f"/api/targets/{target_id}/probe")
    assert r_probe.status_code == 200
    probe_res = r_probe.json()
    assert probe_res["target_id"] == target_id
    assert "healthy" in probe_res
    assert "latency_ms" in probe_res

    # 4. Delete target
    r_del = client.delete(f"/api/targets/{target_id}")
    assert r_del.status_code == 200
    assert r_del.json()["deleted"] is True


def test_target_outage_opens_incident(app_ctx: AppContext):
    """Verify consecutive probe failures on a monitored target create an incident automatically."""
    monitor = app_ctx.targets
    # Add an unreachable endpoint
    t = monitor.add_target(
        name="Failing Service",
        url="http://127.0.0.1:19999",
        health_path="/health",
        github_repo_url="https://github.com/1kailas/Aegis",
    )
    tid = t["id"]

    # First probe failure
    monitor.probe_target(tid)
    # Second probe failure -> triggers incident creation
    monitor.probe_target(tid)

    active_inc = app_ctx.incidents.active_by_category(f"target_outage_{tid}")
    assert active_inc is not None
    assert "Failing Service" in active_inc["title"]
    assert active_inc["severity"] == "critical"
    assert active_inc["state"] == "DETECTED"


def test_devin_task_creation_with_diagnostics(app_ctx: AppContext):
    """Verify Devin RepairTask extracts diagnostics from incident evidence."""
    inc = app_ctx.incidents.create(
        category="payment_exception",
        severity="critical",
        title="Payment Gateway 500 Error",
        summary="Unhandled NullPointer in checkout.py",
        evidence=[{
            "kind": "ai_diagnosis",
            "detail": {
                "root_cause": "Missing null check on gateway response payload",
                "affected_files": ["checkout.py"],
                "reproduction_steps": ["Submit empty cart token"],
                "acceptance_criteria": ["Gracefully return HTTP 400 Bad Request"],
            },
        }],
    )

    task = app_ctx.devin.create_repair_task(
        incident=inc,
        affected_repo="https://github.com/1kailas/Aegis.git",
    )
    assert task.task_id.startswith("TASK-DEVIN-")
    assert "Missing null check" in task.root_cause_hypotheses[0]
    assert task.affected_files == ["checkout.py"]
    assert task.reproduction_steps == ["Submit empty cart token"]
    assert task.acceptance_criteria == ["Gracefully return HTTP 400 Bad Request"]

    prompt = task.build_devin_prompt()
    assert "Missing null check" in prompt
    assert "checkout.py" in prompt
    assert "Submit empty cart token" in prompt


def test_devin_polling_honest_unconfigured(app_ctx: AppContext):
    """Verify Devin adapter honestly reports AWAITING_DEVIN_API_KEY when key is not set."""
    app_ctx.devin.groq_engine = None
    app = create_app(app_ctx)
    client = TestClient(app)

    inc = app_ctx.incidents.create(
        category="db_timeout",
        severity="warning",
        title="DB connection timeout",
        summary="Pool exhausted",
    )
    task = app_ctx.devin.create_repair_task(inc)
    # Task dispatched without API key
    disp = app_ctx.devin.dispatch_task(task)
    assert disp["status"] == "AWAITING_EXTERNAL_SETUP"

    # Poll session endpoint
    r_poll = client.get(f"/api/devin/tasks/{task.task_id}/poll")
    assert r_poll.status_code == 200
    data = r_poll.json()
    assert data["configured"] is False
    assert "DEVIN_API_KEY is not configured" in data["message"]


def test_safety_demonstration_endpoint_403(app_ctx: AppContext):
    """Verify POST /api/policy/test-safety deterministically rejects dangerous commands with HTTP 403."""
    app = create_app(app_ctx)
    client = TestClient(app)

    r = client.post("/api/policy/test-safety", json={"command": "rm -rf /", "target": "/"})
    assert r.status_code == 403
    err_detail = r.json().get("detail", {})
    assert err_detail.get("blocked") is True
    assert err_detail.get("risk_level") == "PROHIBITED"
    assert "prohibited command pattern detected" in err_detail.get("reason", "")


def test_simulate_resource_action_endpoint(app_ctx: AppContext):
    """Verify POST /api/demo/simulate-resource-action triggers deterministic remediation and logs action."""
    app = create_app(app_ctx)
    client = TestClient(app)

    r = client.post("/api/demo/simulate-resource-action", json={"action_type": "evict_idle_model"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["action"] == "evict_idle_model"
    assert data["status"] == "CONTAINED"

    # Verify incident exists and contains lifecycle transition
    inc = app_ctx.incidents.get(data["incident_id"])
    assert inc is not None
    assert inc["state"] == "CONTAINED"


def test_close_loop_recovery_verification(app_ctx: AppContext):
    """Verify close_loop_verification marks incident as RECOVERY_VERIFIED when probe succeeds."""
    app = create_app(app_ctx)
    client = TestClient(app)

    inc = app_ctx.incidents.create(
        category="web_500",
        severity="critical",
        title="Web service 500 error",
        summary="Service failing /health checks",
    )
    # Transition to PR_OPENED
    app_ctx.incidents.transition(inc["id"], "PR_OPENED", "test_actor")
    task = app_ctx.devin.create_repair_task(inc)

    # Close the loop
    r_close = client.post(f"/api/devin/tasks/{task.task_id}/close-loop", json={"auto_apply": True})
    assert r_close.status_code == 200
    res = r_close.json()
    assert res["verified"] is True
    assert res["status"] == "RECOVERY_VERIFIED"

    # Verify incident closed state in DB
    updated_inc = app_ctx.incidents.get(inc["id"])
    assert updated_inc["state"] == "RECOVERY_VERIFIED"
    assert updated_inc["closed_at"] is not None
