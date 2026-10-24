"""Aegis AI v2.0 Automated Test Suite.

Covers:
- Distributed multi-node cluster mesh (registration, heartbeats, stale detection, draining, cluster capacity)
- Automated canary rollout evaluation and closed-loop automated rollback on SLA breach
- Multi-channel notification dispatcher with sliding-window alert storm suppression (debouncing)
- Cryptographic RBAC token generation, permission scopes, and 403 Forbidden enforcement
- v2.0 REST API endpoints
"""

import time
import pytest
from fastapi.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.cluster.mesh import ClusterMeshManager
from aegis.config import load_config
from aegis.engineering.canary_verifier import CanaryState, CanaryVerificationEngine
from aegis.notifications.dispatcher import NotificationDispatcher
from aegis.security.rbac import RBACManager


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_v20.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_cluster_mesh_node_lifecycle_and_drain(app_ctx: AppContext):
    mgr = app_ctx.cluster

    # 1. Register worker node
    node = mgr.register_node(
        node_id="worker-node-1",
        hostname="k8s-worker-1",
        ip_address="10.0.1.25",
        labels={"zone": "us-east-1a", "role": "worker"},
    )
    assert node.node_id == "worker-node-1"
    assert node.status == "ONLINE"

    # 2. Record heartbeat with telemetry
    hb_ok = mgr.record_heartbeat(
        "worker-node-1",
        telemetry_summary={"cpu_percent": 35.0, "memory": {"used_mb": 4096, "total_mb": 16384}},
    )
    assert hb_ok is True

    # 3. Cluster Summary aggregation
    summary = mgr.cluster_summary()
    assert summary["total_nodes"] >= 1
    assert summary["online_nodes"] >= 1
    assert summary["cluster_memory_total_mb"] >= 16384
    assert summary["average_cpu_percent"] == 35.0

    # 4. Drain node
    drain_ok = mgr.drain_node("worker-node-1", reason="Thermal emergency on node chassis")
    assert drain_ok is True
    drained_node = next(n for n in mgr.list_nodes() if n["node_id"] == "worker-node-1")
    assert drained_node["status"] == "DRAINED"


def test_canary_verification_and_automated_rollback(app_ctx: AppContext):
    engine = app_ctx.canary

    # 1. Init canary rollout
    rollout = engine.init_rollout(target="deployment/payment-service", total_steps=3)
    rid = rollout.rollout_id
    assert rollout.state == CanaryState.CANARY_OBSERVING
    assert rollout.current_step == 1

    # 2. Step 1: Healthy metrics -> advances to step 2
    r_step1 = engine.evaluate_step(rid, live_error_rate=0.01, live_latency_ms=85.0)
    assert r_step1.current_step == 2
    assert r_step1.state == CanaryState.CANARY_OBSERVING

    # 3. Step 2: Error rate spike (12% > 5% max budget) -> AUTOMATED ROLLBACK
    rollback_called = []
    def do_rollback():
        rollback_called.append(True)
        return True

    r_rollback = engine.evaluate_step(
        rid,
        live_error_rate=0.12,
        live_latency_ms=120.0,
        rollback_fn=do_rollback,
    )
    assert r_rollback.state == CanaryState.ROLLED_BACK
    assert len(rollback_called) == 1
    assert "exceeded budget" in r_rollback.revert_trigger


def test_notification_dispatcher_webhooks_and_debouncing(app_ctx: AppContext):
    dispatcher = app_ctx.dispatcher

    # 1. Add mock notification channel
    chan = dispatcher.add_channel(
        name="Security Slack Channel",
        channel_type="webhook",
        webhook_url="http://127.0.0.1:9/mock-sink",
        min_severity="warning",
    )
    assert chan.name == "Security Slack Channel"
    assert chan.min_severity == "warning"

    # 2. First dispatch -> attempts notification
    res1 = dispatcher.dispatch_alert(
        title="Disk Exhaustion Imminent",
        message="Partition / is at 98% capacity",
        severity="critical",
        incident_id="INC-DISK-01",
    )
    assert res1["total_channels"] >= 1

    # 3. Immediate duplicate dispatch -> debounced to prevent alert storm
    res2 = dispatcher.dispatch_alert(
        title="Disk Exhaustion Imminent",
        message="Partition / is at 98% capacity",
        severity="critical",
        incident_id="INC-DISK-01",
    )
    assert res2["dispatched"] is False
    assert "debounced" in res2["reason"]


def test_rbac_scoped_keys_and_permissions(app_ctx: AppContext):
    rbac = app_ctx.rbac

    # 1. Create OPERATOR key
    op_token, op_rec = rbac.create_api_key("SRE Team Lead", role="OPERATOR")
    assert op_token.startswith("aegis_sec_")
    assert op_rec.role == "OPERATOR"
    assert rbac.has_permission(op_rec, "remediation:execute") is True

    # 2. Create READONLY key
    ro_token, ro_rec = rbac.create_api_key("Dashboard Viewer", role="READONLY")
    assert ro_rec.role == "READONLY"
    assert rbac.has_permission(ro_rec, "telemetry:read") is True
    assert rbac.has_permission(ro_rec, "remediation:execute") is False

    # 3. Authenticate raw token
    auth_rec = rbac.authenticate_token(op_token)
    assert auth_rec is not None
    assert auth_rec.key_id == op_rec.key_id

    # 4. HTTP API Enforcement: READONLY token blocked on state change (403 Forbidden)
    app = create_app(app_ctx)
    client = TestClient(app)

    # State change with READONLY token
    r_blocked = client.post(
        "/api/policy/emergency_stop",
        headers={"Authorization": f"Bearer {ro_token}"},
        json={"enabled": True},
    )
    assert r_blocked.status_code == 403
    assert "READONLY token cannot execute state changes" in r_blocked.json()["detail"]

    # State change with OPERATOR token
    r_allowed = client.post(
        "/api/policy/emergency_stop",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"enabled": True},
    )
    assert r_allowed.status_code == 200
    assert r_allowed.json()["emergency_stop"] is True


def test_v20_api_endpoints(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Cluster Node Registration & Heartbeat
    r_reg = client.post("/api/cluster/nodes/register", json={
        "node_id": "api-test-node",
        "hostname": "test-host",
        "ip_address": "192.168.1.100",
        "labels": {"role": "compute"},
    })
    assert r_reg.status_code == 200
    assert r_reg.json()["status"] == "ONLINE"

    r_hb = client.post("/api/cluster/nodes/api-test-node/heartbeat", json={
        "telemetry_summary": {"cpu_percent": 15.0},
    })
    assert r_hb.status_code == 200
    assert r_hb.json()["heartbeat_recorded"] is True

    # 2. Cluster Summary
    r_sum = client.get("/api/cluster/summary")
    assert r_sum.status_code == 200
    assert r_sum.json()["total_nodes"] >= 1

    # 3. Canary Rollout Endpoint
    r_canary_init = client.post("/api/canary/rollouts", json={
        "target": "deployment/auth-service",
        "total_steps": 2,
    })
    assert r_canary_init.status_code == 200
    cid = r_canary_init.json()["rollout_id"]

    r_canary_eval = client.post(f"/api/canary/rollouts/{cid}/evaluate", json={
        "error_rate": 0.0,
        "latency_ms": 35.0,
    })
    assert r_canary_eval.status_code == 200
    assert r_canary_eval.json()["current_step"] == 2

    # 4. Security Keys Endpoint
    r_key = client.post("/api/security/keys", json={
        "name": "Integration Key",
        "role": "NODE_AGENT",
    })
    assert r_key.status_code == 200
    assert "token" in r_key.json()
    assert r_key.json()["role"] == "NODE_AGENT"
