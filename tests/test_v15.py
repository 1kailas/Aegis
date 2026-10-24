"""Aegis AI v1.5 Automated Test Suite.

Covers:
- Predictive telemetry forecasting & Time-To-Exhaustion (TTE) linear regression
- Closed-loop autonomous self-healing engine (triage, remediation, dead-letter escalation)
- Synthetic service prober & latency SLA monitoring (HTTP & TCP)
- Prometheus metrics exporter line-protocol format (/metrics)
- v1.5 API endpoints
"""

import time
import pytest
from fastapi.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.control_plane.self_healing import SelfHealingEngine
from aegis.intelligence.forecasting import TelemetryForecaster
from aegis.orchestration.probers import ProbeTarget, SyntheticProber


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_v15.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_telemetry_forecaster_linear_regression_and_tte():
    forecaster = TelemetryForecaster(
        warning_tte_seconds=3600.0,
        critical_tte_seconds=900.0,
        min_samples_for_prediction=4,
    )

    # 1. Rising trend: 10% per second
    t0 = 1000.0
    for i in range(6):
        forecaster.feed("memory_percent", 40.0 + (i * 10.0), ts=t0 + i)

    res = forecaster.forecast_exhaustion("memory_percent", capacity=100.0)
    assert res.current_value == 90.0
    assert 9.5 <= res.rate_per_sec <= 10.5
    assert res.tte_seconds is not None
    assert 0.5 <= res.tte_seconds <= 2.0  # (100 - 90) / 10 = ~1.0s
    assert res.severity == "CRITICAL"
    assert res.is_exhaustion_imminent is True

    # 2. Falling/stable trend: no exhaustion
    forecaster_falling = TelemetryForecaster(min_samples_for_prediction=4)
    for i in range(5):
        forecaster_falling.feed("memory_percent", 80.0 - (i * 5.0), ts=t0 + i)

    res_falling = forecaster_falling.forecast_exhaustion("memory_percent", capacity=100.0)
    assert res_falling.rate_per_sec <= 0.0
    assert res_falling.tte_seconds is None
    assert res_falling.severity == "NOMINAL"
    assert res_falling.is_exhaustion_imminent is False


def test_self_healing_engine_lifecycle_and_escalation(app_ctx: AppContext):
    engine = SelfHealingEngine(app_ctx, max_attempts_per_incident=2, cycle_cooldown_s=0.0)

    # 1. Create a thermal incident in DETECTED state
    inc = app_ctx.incidents.create(
        category="thermal_warning",
        severity="critical",
        title="Hardware Thermal Critical Alert",
        summary="Package temp exceeded 90C",
    )
    iid = inc["id"]

    # 2. Autonomous cycle in policy_autonomous mode
    app_ctx.set_mode("policy_autonomous")
    report = engine.run_reconciliation_cycle()
    assert report.incidents_evaluated >= 1
    assert report.diagnosed_count >= 1
    assert report.actions_attempted >= 1
    assert report.actions_succeeded >= 1

    # Verify incident progressed to CONTAINED
    updated = app_ctx.incidents.get(iid)
    assert updated["state"] == "CONTAINED"

    # 3. Dead-letter escalation: exceed max attempts
    engine._incident_attempts[iid] = 2
    # Reset cooldown to run immediately
    engine._last_cycle_ts = 0.0
    report_escalate = engine.run_reconciliation_cycle()
    assert report_escalate.escalated_count >= 1

    escalated_inc = app_ctx.incidents.get(iid)
    assert escalated_inc["state"] == "NEEDS_HUMAN_APPROVAL"


def test_synthetic_probers_http_and_tcp(app_ctx: AppContext):
    prober = SyntheticProber()

    # Add dummy target
    prober.add_target(ProbeTarget(
        name="test-tcp-google",
        target_type="tcp",
        endpoint="1.1.1.1:53",  # Cloudflare DNS port 53
        timeout_s=2.0,
    ))

    results = prober.probe_all()
    assert len(results) == 1
    res = results[0]
    assert res.target_name == "test-tcp-google"
    # Even if offline/firewalled, result object is structured properly
    assert isinstance(res.latency_ms, float)

    summary = prober.get_summary()
    assert "test-tcp-google" in summary
    assert "uptime_pct" in summary["test-tcp-google"]


def test_prometheus_metrics_format(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    body = res.text

    # Verify standard Prometheus line protocol keys
    assert "# HELP aegis_cpu_utilization_percent" in body
    assert "# TYPE aegis_cpu_utilization_percent gauge" in body
    assert "# HELP aegis_memory_utilization_percent" in body
    assert "# HELP aegis_thermal_guardian_state" in body
    assert "# HELP aegis_incidents_active_total" in body
    assert "host_id=" in body


def test_v15_api_endpoints(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Forecasting endpoint
    r_forecast = client.get("/api/forecasting/forecasts")
    assert r_forecast.status_code == 200
    assert "memory" in r_forecast.json()
    assert "disks" in r_forecast.json()

    # 2. Self-healing reconciliation trigger
    r_heal = client.post("/api/self_healing/reconcile")
    assert r_heal.status_code == 200
    assert "cycle_id" in r_heal.json()
    assert "incidents_evaluated" in r_heal.json()

    # 3. Add probe target
    r_probe_add = client.post("/api/probers/targets", json={
        "name": "api-health",
        "target_type": "http",
        "endpoint": "http://127.0.0.1:8000/api/health",
        "sla_latency_ms": 200.0,
    })
    assert r_probe_add.status_code == 200
    assert r_probe_add.json()["added"] == "api-health"

    # 4. Prober summary endpoint
    r_probes = client.get("/api/probers/summary")
    assert r_probes.status_code == 200
    assert isinstance(r_probes.json(), dict)
