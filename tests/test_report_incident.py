"""Tests for POST /api/incidents/report endpoint.

Verifies:
1. Ingestion of live unhandled error reports / exception tracebacks from client websites.
2. Automatic incident creation with critical severity.
3. Devin / Autonomous repair task creation and dispatching.
4. Correct response format with incident_id, task_id, and PR / patch metadata.
"""

from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_report.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_report_incident_endpoint(app_ctx: AppContext):
    """Verify live client error reports trigger incident creation and PR engine dispatch."""
    app = create_app(app_ctx)
    client = TestClient(app)

    payload = {
        "app_name": "Webliz Cloud Store",
        "service": "checkout-service",
        "error": "ZeroDivisionError: division by zero",
        "traceback": 'Traceback (most recent call last):\n  File "demo-target/app.py", line 42, in calculate_discounted_total\n    multiplier = 100.0 / (100.0 - discount_percent)\nZeroDivisionError: division by zero',
        "affected_file": "demo-target/app.py",
        "url": "http://127.0.0.1:5050",
        "repo_url": "https://github.com/1kailas/Aegis",
    }

    # Mock dispatch_task to prevent external LLM / Groq network calls during unit test
    with patch.object(app_ctx.devin, "dispatch_task") as mock_dispatch:
        mock_dispatch.return_value = {
            "session_id": "mock-devin-session-42",
            "pull_request_url": "https://github.com/1kailas/Aegis/pull/42",
            "branch": "aegis/repair-test",
            "patch": "--- a/demo-target/app.py\n+++ b/demo-target/app.py\n@@ -42,1 +42,1 @@\n- multiplier = 100.0 / (100.0 - discount_percent)\n+ multiplier = 1.0 if discount_percent == 100.0 else (100.0 / (100.0 - discount_percent))\n",
            "status": "pr_opened",
        }

        res = client.post("/api/incidents/report", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "incident_id" in data
        assert "task_id" in data
        assert data["pull_request_url"] == "https://github.com/1kailas/Aegis/pull/42"
        assert "patch" in data

        # Check incident in DB
        inc = app_ctx.incidents.get(data["incident_id"])
        assert inc is not None
        assert inc["category"] == "software_bug"
        assert inc["severity"] == "critical"
        assert "Webliz Cloud Store" in inc["title"]
        assert any(e.get("kind") == "target_exception" for e in inc["evidence"])
