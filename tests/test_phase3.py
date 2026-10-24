"""Phase 3 automated test suite for Aegis AI.

Covers:
- IncidentDiagnoser multi-tier architecture (local model, cloud fallback, deterministic expert rules)
- AI output validation against strict schemas and assessment labeling
- Sensitive data redaction in diagnosis prompts
- Cloud Gemini request budgeting and rate limiting
- Devin repair engineer adapter (honest status, task construction, job persistence)
- Remediation policy engine (risk classification, prohibited operations, emergency stop, audit logging)
- Intelligence and Devin API endpoints
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.control_plane.action_policy import (
    ActionCategory,
    RemediationAction,
    RemediationPolicyEngine,
    RiskLevel,
)
from aegis.db import Database
from aegis.engineering.devin_adapter import DevinAdapter, RepairTask
from aegis.events import EventBus
from aegis.incidents import IncidentStore
from aegis.intelligence.diagnosis import DiagnosisResult, IncidentDiagnoser


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_p3.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_diagnoser_deterministic_fallback():
    diagnoser = IncidentDiagnoser(ollama_url="http://127.0.0.1:9999", gemini_api_key=None)

    # Test OOM incident diagnosis
    inc = {
        "id": "INC-TEST-001",
        "category": "container_oom_killed",
        "severity": "critical",
        "title": "Container worker OOMKilled",
        "summary": "Container worker terminated with exit code 137",
        "evidence": [{"exit_code": 137, "oom_killed": True}],
    }
    diag = diagnoser.diagnose(inc)
    assert diag.incident_id == "INC-TEST-001"
    assert diag.provider == "deterministic_expert"
    assert diag.status == "ASSESSMENT"
    assert diag.is_code_or_config_issue is True
    assert diag.confidence_score >= 0.8
    assert "memory" in diag.root_cause_hypothesis.lower()
    assert len(diag.recommended_actions) > 0
    assert len(diag.diagnostic_steps) > 0


def test_diagnoser_redaction():
    diagnoser = IncidentDiagnoser()
    inc = {
        "id": "INC-TEST-002",
        "category": "auth_failure",
        "title": "Auth failure",
        "summary": "Connection failed with AIzaSyC2o32gQbZ-yQG-1N2h9fLfvm8lhnhs3cw and ghp_abcdef1234567890abcdef1234567890",
    }
    context = diagnoser._prepare_context(inc, None)
    assert "AIzaSy" not in context
    assert "[REDACTED_GEMINI_KEY]" in context
    assert "ghp_" not in context
    assert "[REDACTED_GITHUB_TOKEN]" in context


def test_diagnoser_gemini_rate_limiting():
    diagnoser = IncidentDiagnoser(gemini_api_key="mock-key", max_gemini_requests_per_hour=2)
    assert diagnoser._can_call_gemini() is True
    diagnoser._record_gemini_call()
    assert diagnoser._can_call_gemini() is True
    diagnoser._record_gemini_call()
    # 2 calls made; limit reached
    assert diagnoser._can_call_gemini() is False


def test_devin_adapter_honest_status(tmp_path):
    db = Database(tmp_path / "devin_test.db")
    devin = DevinAdapter(db, api_key=None)

    status = devin.status()
    assert status["provider"] == "devin"
    if not status["has_desktop_cli"]:
        assert status["status"] == "AWAITING_EXTERNAL_SETUP"
        assert status["configured"] is False


def test_devin_task_creation_and_queue(tmp_path):
    db = Database(tmp_path / "devin_test2.db")
    devin = DevinAdapter(db, api_key=None)

    inc = {
        "id": "INC-2026-001",
        "title": "Null pointer in worker",
        "summary": "Worker crashed on null payload",
        "evidence": [{"note": "Unchecked None in payload parser"}],
    }

    task = devin.create_repair_task(
        incident=inc,
        affected_files=["aegis/engine.py"],
        reproduction_steps=["Run test_engine"],
        acceptance_criteria=["Zero crashes"],
        sanitized_logs="Crash log at line 42",
    )

    assert task.incident_id == "INC-2026-001"
    assert "Unchecked None" in task.root_cause_hypotheses[0]
    prompt = task.build_devin_prompt()
    assert "Engineering Repair Request" in prompt
    assert "Prohibited Operations" in prompt

    # Verify task was persisted in SQLite jobs
    tasks = devin.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == task.task_id

    # Dispatch without API key gracefully queues
    res = devin.dispatch_task(task)
    assert res["success"] is False
    assert res["status"] == "AWAITING_EXTERNAL_SETUP"


def test_remediation_policy_prohibited_actions(tmp_path):
    db = Database(tmp_path / "policy_test.db")
    events = EventBus()
    engine = RemediationPolicyEngine(db, events)

    # Prohibited dangerous action
    act = engine.evaluate_proposal(
        action_type="rm -rf /var/data",
        target="host_filesystem",
        reason="test cleanup",
    )
    assert act.risk_level == RiskLevel.PROHIBITED
    assert act.category == ActionCategory.PROHIBITED

    # Execution must be strictly blocked
    res = engine.authorize_and_execute(act, mode="policy_autonomous", emergency_stop=False)
    assert res["authorized"] is False
    assert res["result"] == "REJECTED"


def test_remediation_policy_emergency_stop_and_modes(tmp_path):
    db = Database(tmp_path / "policy_test2.db")
    events = EventBus()
    engine = RemediationPolicyEngine(db, events)

    act = engine.evaluate_proposal(
        action_type="restart_container",
        target="worker-1",
        reason="unhealthy container",
    )
    assert act.risk_level == RiskLevel.LOW

    # 1. Emergency stop blocks everything
    res = engine.authorize_and_execute(act, mode="policy_autonomous", emergency_stop=True)
    assert res["authorized"] is False
    assert res["result"] == "BLOCKED"

    # 2. Monitoring only mode executes as dry-run
    res = engine.authorize_and_execute(act, mode="monitoring_only", emergency_stop=False)
    assert res["authorized"] is True
    assert res["result"] == "VERIFIED"
    assert res["mode"] == "dry_run"


def test_phase3_api_endpoints(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Create test incident
    inc = app_ctx.incidents.create(
        category="container_oom_killed",
        severity="critical",
        title="OOM Killed Test Container",
        summary="Container exited with code 137",
    )
    iid = inc["id"]

    # 2. Diagnose incident
    r_diag = client.post(f"/api/incidents/{iid}/diagnose")
    assert r_diag.status_code == 200
    diag_data = r_diag.json()
    assert diag_data["incident_id"] == iid
    assert diag_data["status"] == "ASSESSMENT"

    # 3. Devin status
    r_devin = client.get("/api/devin/status")
    assert r_devin.status_code == 200
    assert "provider" in r_devin.json()

    # 4. Create Devin task
    r_task = client.post("/api/devin/tasks", json={"incident_id": iid})
    assert r_task.status_code == 200
    task_id = r_task.json()["task_id"]

    # 5. List Devin tasks
    r_tasks = client.get("/api/devin/tasks")
    assert r_tasks.status_code == 200
    assert len(r_tasks.json()) >= 1

    # 6. Evaluate remediation proposal
    r_eval = client.post("/api/remediation/evaluate", json={
        "action_type": "restart_container",
        "target": "c1",
        "reason": "OOM recovery",
    })
    assert r_eval.status_code == 200
    assert r_eval.json()["risk_level"] == "LOW"

    # 7. Authorize remediation
    r_auth = client.post("/api/remediation/authorize", json={
        "action_type": "restart_container",
        "target": "c1",
        "reason": "OOM recovery",
    })
    assert r_auth.status_code == 200
    assert r_auth.json()["authorized"] is True
