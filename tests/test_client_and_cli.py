"""Tests for Aegis Agent daemon and unified developer CLI."""

from __future__ import annotations

import argparse
import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.cli.main import (
    cmd_canary,
    cmd_estop,
    cmd_incidents,
    cmd_keys,
    cmd_mode,
    cmd_nodes,
    cmd_status,
    get_client,
)
from aegis.client.agent import (
    AegisAgent,
    get_primary_ip,
    normalize_server_url,
    sample_host_capacity,
    sample_quick_telemetry,
)


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_client_cli.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_agent_telemetry_sampling():
    """Verify local host telemetry and capacity sampling."""
    cap = sample_host_capacity()
    assert cap["cpu_cores"] >= 1
    assert cap["total_ram_gb"] > 0
    assert "arch" in cap

    snap = sample_quick_telemetry()
    assert 0.0 <= snap["cpu_percent"] <= 100.0
    assert 0.0 <= snap["ram_percent"] <= 100.0
    assert snap["ram_used_gb"] >= 0.0
    assert snap["timestamp"] > 0

    ip = get_primary_ip()
    assert isinstance(ip, str) and len(ip) >= 7


def test_agent_registration_and_heartbeat(app_ctx: AppContext):
    """Verify AegisAgent registration and heartbeat against test client."""
    app = create_app(app_ctx)
    client = TestClient(app)

    agent = AegisAgent(
        server_url="http://testserver",
        node_id="worker-node-42",
        hostname="worker-42.local",
        ip_address="10.0.0.42",
        labels={"role": "compute", "zone": "us-east-1"},
    )
    # Patch agent client to use Starlette TestClient transport
    agent.client = client

    # 1. Register node
    ok = agent.register()
    assert ok is True
    assert agent._registered is True

    # Check that node is in cluster mesh
    nodes = app_ctx.cluster.list_nodes()
    match = next((n for n in nodes if n["node_id"] == "worker-node-42"), None)
    assert match is not None
    assert match["status"] == "ONLINE"
    assert match["labels"]["role"] == "compute"

    # 2. Send heartbeat
    hb_ok = agent.heartbeat()
    assert hb_ok is True

    # 3. Drain node
    drain_ok = agent.drain("Routine maintenance")
    assert drain_ok is True
    nodes_after = app_ctx.cluster.list_nodes()
    match_after = next((n for n in nodes_after if n["node_id"] == "worker-node-42"), None)
    assert match_after["status"] == "DRAINED"


def test_cli_status_and_nodes_commands(app_ctx: AppContext):
    """Verify CLI status and nodes command handlers."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # Register a test node in cluster
    app_ctx.cluster.register_node("test-node-1", "host-1", "10.0.0.1", {"role": "worker"})

    # Test status
    args = argparse.Namespace()
    ret_status = cmd_status(client, args)
    assert ret_status == 0

    # Test nodes list
    args_nodes = argparse.Namespace(nodes_action="list")
    ret_nodes = cmd_nodes(client, args_nodes)
    assert ret_nodes == 0

    # Test nodes drain
    args_drain = argparse.Namespace(nodes_action="drain", node_id="test-node-1", reason="test drain")
    ret_drain = cmd_nodes(client, args_drain)
    assert ret_drain == 0

    # Verify node is now drained
    nodes = app_ctx.cluster.list_nodes()
    assert nodes[0]["status"] == "DRAINED"


def test_cli_incidents_and_canary_commands(app_ctx: AppContext):
    """Verify CLI incidents and canary command handlers."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Incidents list
    args_inc = argparse.Namespace(incidents_action="list", state=None, limit=10)
    ret_inc = cmd_incidents(client, args_inc)
    assert ret_inc == 0

    # 2. Init a canary and test canary list + rollback
    rollout = app_ctx.canary.init_rollout(target="deployment/api-gw", total_steps=3)
    args_canary_list = argparse.Namespace(canary_action="list")
    ret_cl = cmd_canary(client, args_canary_list)
    assert ret_cl == 0

    args_rollback = argparse.Namespace(canary_action="rollback", rollout_id=rollout.rollout_id, reason="test")
    ret_rb = cmd_canary(client, args_rollback)
    assert ret_rb == 0


def test_cli_keys_and_modes(app_ctx: AppContext):
    """Verify CLI key creation, list, and operational modes."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Keys create
    args_create_key = argparse.Namespace(keys_action="create", name="agent-token-1", role="NODE_AGENT")
    ret_ck = cmd_keys(client, args_create_key)
    assert ret_ck == 0

    # 2. Keys list
    args_list_keys = argparse.Namespace(keys_action="list")
    ret_lk = cmd_keys(client, args_list_keys)
    assert ret_lk == 0

    # 3. Mode query and update
    args_mode_get = argparse.Namespace(new_mode=None)
    assert cmd_mode(client, args_mode_get) == 0

    args_mode_set = argparse.Namespace(new_mode="policy_autonomous")
    assert cmd_mode(client, args_mode_set) == 0
    assert app_ctx.flags.mode == "policy_autonomous"

    # 4. Emergency stop query and toggle
    args_estop_get = argparse.Namespace(state=None)
    assert cmd_estop(client, args_estop_get) == 0

    args_estop_on = argparse.Namespace(state="on")
    assert cmd_estop(client, args_estop_on) == 0
    assert app_ctx.flags.emergency_stop is True

    args_estop_off = argparse.Namespace(state="off")
    assert cmd_estop(client, args_estop_off) == 0
    assert app_ctx.flags.emergency_stop is False


def test_normalize_server_url_and_ip_support():
    """Verify raw IP addresses and URLs are normalized consistently."""
    assert normalize_server_url("192.168.1.100") == "http://192.168.1.100:8000"
    assert normalize_server_url("192.168.1.100:9000") == "http://192.168.1.100:9000"
    assert normalize_server_url("http://10.0.0.1") == "http://10.0.0.1:8000"
    assert normalize_server_url("http://10.0.0.1:8480") == "http://10.0.0.1:8480"
    assert normalize_server_url("https://aegis.cloud.corp") == "https://aegis.cloud.corp"
    assert normalize_server_url("") == "http://127.0.0.1:8000"
    assert normalize_server_url(None) == "http://127.0.0.1:8000"

    # Agent automatically normalizes positional IP argument
    agent = AegisAgent(server_url="172.16.0.5")
    assert agent.server_url == "http://172.16.0.5:8000"


def test_server_distribution_endpoints(app_ctx: AppContext):
    """Verify /agent.py, /install.sh, and /api/cluster/join-info endpoints."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. Download standalone agent.py
    r_agent = client.get("/agent.py")
    assert r_agent.status_code == 200
    assert "class AegisAgent" in r_agent.text
    assert "normalize_server_url" in r_agent.text

    # 2. Download dynamic install.sh
    r_install = client.get("/install.sh", headers={"Host": "192.168.1.55:8000"})
    assert r_install.status_code == 200
    assert "#!/usr/bin/env bash" in r_install.text
    assert "192.168.1.55:8000" in r_install.text

    # 3. Join info metadata
    r_join = client.get("/api/cluster/join-info", headers={"Host": "10.0.0.99:8000"})
    assert r_join.status_code == 200
    data = r_join.json()
    assert data["server_host"] == "10.0.0.99:8000"
    assert "10.0.0.99:8000" in data["curl_agent_run"]
    assert "10.0.0.99:8000" in data["curl_install_run"]
    assert "10.0.0.99:8000" in data["cli_join"]
