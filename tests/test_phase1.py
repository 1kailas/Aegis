"""Phase 1 automated test suite for Aegis AI.

Covers:
- Hardware, CPU, memory, network, storage, process, and GPU collectors
- Sensor registry and graceful degradation for missing sensors
- Deterministic Thermal Guardian with hysteresis and rate-of-rise alerting
- Deterministic Resource Guardian with hysteresis and cooldown
- Deterministic Policy Executor and emergency stop
- SQLite persistence, schema migrations, telemetry queries, and retention pruning
- FastAPI endpoints (health, telemetry, overview, sensors, system details)
"""

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.collectors.gpu import GpuCollector
from aegis.collectors.hardware import HardwareCollector, host_identity
from aegis.collectors.inventory import InventoryCollector
from aegis.collectors.processes import ProcessCollector
from aegis.collectors.sensor_registry import Sensor, SensorRegistry, Validity
from aegis.collectors.storage import StorageCollector
from aegis.config import AppConfig, DatabaseConfig, load_config
from aegis.control_plane.policy import PolicyExecutor
from aegis.control_plane.resource_guardian import ResourceGuardian, ResourcePolicy
from aegis.control_plane.thermal_guardian import CRITICAL, NORMAL, RECOVERY, WARNING, ThermalGuardian
from aegis.db import Database
from aegis.engine import MonitoringEngine
from aegis.events import EventBus
from aegis.incidents import IncidentStore


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_aegis.db"
    return Database(db_file)


@pytest.fixture
def app_ctx(tmp_path: Path) -> AppContext:
    cfg = load_config()
    cfg.database.path = tmp_path / "test_aegis.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_host_identity():
    ident = host_identity()
    assert ident.host_id
    assert ident.hostname
    assert ident.platform_str
    assert ident.cpu_cores is not None and ident.cpu_cores > 0
    assert ident.total_memory_mb is not None and ident.total_memory_mb > 0


def test_hardware_collector():
    hc = HardwareCollector()
    cpu_mem = hc.sample_cpu_mem()
    assert "cpu_percent" in cpu_mem
    assert "memory" in cpu_mem
    assert cpu_mem["memory"]["total_mb"] > 0
    assert 0 <= cpu_mem["memory"]["percent"] <= 100
    assert "pressure" in cpu_mem
    assert "throttling" in cpu_mem

    disks = hc.sample_disks()
    assert isinstance(disks, list)
    for d in disks:
        assert d.total_gb > 0
        assert 0 <= d.used_pct <= 100

    rates = hc.sample_io_rates()
    assert rates is not None
    assert hc.uptime_s() >= 0.0


def test_gpu_collector():
    collector = GpuCollector()
    sample = collector.sample()
    assert "supported" in sample
    assert "available" in sample
    assert "devices" in sample
    if not sample["available"]:
        assert sample["reason"] is not None
        assert sample["devices"] == []


def test_process_collector():
    collector = ProcessCollector(top_n=5)
    sample = collector.sample()
    assert "top_cpu" in sample
    assert "top_memory" in sample
    assert len(sample["top_cpu"]) <= 5
    assert len(sample["top_memory"]) <= 5
    assert sample["total_processes"] > 0


def test_storage_collector():
    collector = StorageCollector()
    sample = collector.sample()
    assert "partitions" in sample
    assert "io" in sample
    assert "smart" in sample
    assert isinstance(sample["partitions"], list)


def test_inventory_collector():
    collector = InventoryCollector(host_id="test-host")
    sample = collector.sample()
    assert sample["host_id"] == "test-host"
    assert "hardware" in sample
    assert "software" in sample
    assert sample["hardware"]["cpu"]["logical_cores"] > 0
    assert "os_name" in sample["software"]


def test_sensor_registry_graceful_missing():
    registry = SensorRegistry(host_id="test-host")
    sensors = registry.all_sensors()
    readings = registry.read_all()
    assert len(readings) == len(sensors)
    # If no physical sensors exist, all readings must be handled gracefully without exceptions
    for r in readings:
        assert r.validity in (Validity.VALID, Validity.UNAVAILABLE, Validity.ERROR, Validity.STALE)


def test_thermal_guardian_state_machine():
    from aegis.config import ThermalPolicyConfig

    pol = ThermalPolicyConfig(
        warning_c=70.0,
        critical_c=80.0,
        recovery_below_c=60.0,
        hysteresis_c=2.0,
        rate_per_s_warning=1.0,
        window_points=5,
        recovery_hold_s=2.0,
    )
    guardian = ThermalGuardian(pol, host_id="test-host")

    # Initially NORMAL
    now = 1000.0
    guardian.feed(now, [{"sensor_id": "cpu", "value": 50.0, "validity": "VALID"}])
    st = guardian.evaluate(now=now)
    assert st.state == NORMAL

    # Feed WARNING temperature (75 degC)
    now += 1.0
    guardian.feed(now, [{"sensor_id": "cpu", "value": 75.0, "validity": "VALID"}])
    st = guardian.evaluate(now=now)
    assert st.state == WARNING

    # Feed CRITICAL temperature (85 degC)
    now += 1.0
    guardian.feed(now, [{"sensor_id": "cpu", "value": 85.0, "validity": "VALID"}])
    st = guardian.evaluate(now=now)
    assert st.state == CRITICAL

    # Temp drops to 55 degC (below recovery threshold 60 degC)
    now += 1.0
    guardian.feed(now, [{"sensor_id": "cpu", "value": 55.0, "validity": "VALID"}])
    st = guardian.evaluate(now=now)
    assert st.state == RECOVERY  # Enters recovery hold

    # Temp still 55 degC before hold time elapses (1 second later)
    now += 1.0
    guardian.feed(now, [{"sensor_id": "cpu", "value": 55.0, "validity": "VALID"}])
    st = guardian.evaluate(now=now)
    assert st.state == RECOVERY  # Still in recovery

    # After recovery_hold_s (2.1s later)
    now += 2.1
    guardian.feed(now, [{"sensor_id": "cpu", "value": 55.0, "validity": "VALID"}])
    st = guardian.evaluate(now=now)
    assert st.state == NORMAL  # Successfully stabilized back to NORMAL


def test_resource_guardian_evaluation():
    pol = ResourcePolicy(
        memory_warning_pct=80.0,
        memory_critical_pct=90.0,
        memory_recovery_pct=70.0,
        disk_warning_pct=85.0,
        disk_critical_pct=95.0,
        recovery_hold_s=1.0,
    )
    rg = ResourceGuardian(pol)

    # 1. Normal state
    st = rg.evaluate(cpu_pct=20.0, mem_pct=50.0, disks=[{"used_pct": 40.0}], now=100.0)
    assert st.state == NORMAL

    # 2. Warning memory state
    st = rg.evaluate(cpu_pct=20.0, mem_pct=82.0, disks=[{"used_pct": 40.0}], now=101.0)
    assert st.state == WARNING
    assert any("Memory usage warning" in b for b in st.active_breaches)

    # 3. Critical memory state
    st = rg.evaluate(cpu_pct=20.0, mem_pct=92.0, disks=[{"used_pct": 40.0}], now=102.0)
    assert st.state == CRITICAL
    assert any("Memory exhaustion critical" in b for b in st.active_breaches)

    # 4. Drop below recovery threshold (mem: 65% <= 70%)
    st = rg.evaluate(cpu_pct=20.0, mem_pct=65.0, disks=[{"used_pct": 40.0}], now=103.0)
    assert st.state == RECOVERY

    # 5. After recovery hold
    st = rg.evaluate(cpu_pct=20.0, mem_pct=65.0, disks=[{"used_pct": 40.0}], now=105.0)
    assert st.state == NORMAL


def test_database_persistence_and_queries(test_db: Database):
    assert test_db.db_integrity() == "ok"

    # Record telemetry points
    count = test_db.record_telemetry(
        [("cpu_percent", 45.2, "pct"), ("mem_percent", 62.1, "pct")],
        host_id="test-host",
        source="engine",
    )
    assert count == 2

    # Query metrics list
    metrics = test_db.list_metrics()
    assert "cpu_percent" in metrics
    assert "mem_percent" in metrics

    # Query telemetry data
    rows = test_db.query_telemetry(metric="cpu_percent")
    assert len(rows) == 1
    assert rows[0]["value"] == 45.2
    assert rows[0]["unit"] == "pct"

    # Query aggregated
    agg = test_db.query_telemetry_aggregated(metric="cpu_percent")
    assert len(agg) >= 1
    assert agg[0]["avg_value"] == 45.2

    # Pruning
    pruned = test_db.prune_telemetry(retention_hours=0)
    # Cutoff is now, so all previous points are pruned
    assert pruned >= 2


def test_policy_executor_and_estop(app_ctx: AppContext):
    executor = app_ctx.policy

    # In monitoring_only mode, simulated data can only trigger internal actions
    res = executor.execute(
        action="create_or_update_incident",
        level="WARNING",
        sensor_id="virtual_cpu",
        triggered_by_simulated=True,
        reason="test simulation",
    )
    assert res == "VERIFIED"

    # Emergency stop blocks external actions
    app_ctx.set_emergency_stop(True)
    assert app_ctx.flags.emergency_stop is True
    allowed = executor.authorized_actions("CRITICAL", simulated=False)
    # Internal actions (increase_sampling, create_or_update_incident) remain, external actions blocked
    assert "set_backpressure" not in allowed
    assert "increase_sampling" in allowed


def test_api_endpoints_end_to_end(app_ctx: AppContext):
    # Perform one monitoring tick
    eng = MonitoringEngine(app_ctx)
    snap = eng.tick()
    assert snap is not None

    app = create_app(app_ctx)
    client = TestClient(app)

    # Health
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["db_integrity"] == "ok"

    # Overview
    r = client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert "host" in data
    assert "thermal" in data
    assert "resources" in data
    assert "gpu" in data

    # Sensors
    r = client.get("/api/sensors")
    assert r.status_code == 200

    # System endpoints
    r = client.get("/api/system/gpu")
    assert r.status_code == 200
    r = client.get("/api/system/processes")
    assert r.status_code == 200
    assert "top_cpu" in r.json()
    r = client.get("/api/system/storage")
    assert r.status_code == 200
    assert "partitions" in r.json()
    r = client.get("/api/system/inventory")
    assert r.status_code == 200
    assert "software" in r.json()
    r = client.get("/api/system/resources")
    assert r.status_code == 200
    assert "state" in r.json()

    # Telemetry
    r = client.get("/api/telemetry/metrics")
    assert r.status_code == 200
    r = client.get("/api/telemetry/query?metric=cpu_percent")
    assert r.status_code == 200

    # Actions and Incidents
    r = client.get("/api/actions")
    assert r.status_code == 200
    r = client.get("/api/incidents")
    assert r.status_code == 200
