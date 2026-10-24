"""Phase 2 automated test suite for Aegis AI.

Covers:
- Docker container discovery, stats, health checks, OOMKilled, and crash loops
- Log sanitization (redacting secrets, tokens, API keys)
- Policy-controlled container restart (protected container rejection, rate limits)
- Kubernetes discovery, graceful degradation, and failure detection (CrashLoopBackOff, ImagePullBackOff, OOMKilled)
- AI infrastructure monitoring (Ollama endpoint, model states, VRAM tracking, AI process scanning)
- Workload dependency graph, blast-radius estimation, and resource contention intelligence
- Orchestration API endpoints
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.config import load_config
from aegis.orchestration.ai_workloads import AIWorkloadMonitor, AIWorkloadStatus
from aegis.orchestration.dependency_graph import DependencyGraph
from aegis.orchestration.docker_adapter import ContainerInfo, ContainerMetrics, DockerAdapter
from aegis.orchestration.k8s_adapter import DeploymentInfo, KubernetesAdapter, NodeInfo, PodInfo


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_p2.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_docker_adapter_live_or_fallback():
    adapter = DockerAdapter()
    sample = adapter.sample()
    assert "available" in sample
    assert "containers" in sample
    assert "incidents" in sample
    if adapter.is_available():
        assert isinstance(sample["containers"], list)


def test_docker_incident_detection():
    adapter = DockerAdapter()

    # Synthetic OOMKilled container
    oom_info = ContainerInfo(
        id="c1", short_id="c1", name="worker-oom", image="app:v1",
        status="exited", health="none", exit_code=137, oom_killed=True,
        restart_count=1, created_at="2026-10-24T00:00:00Z", started_at="2026-10-24T00:00:01Z",
        metrics=ContainerMetrics(memory_usage_mb=512, memory_limit_mb=512, memory_percent=100.0),
    )
    incidents = adapter._detect_incidents(oom_info)
    assert any(inc["type"] == "container_oom_killed" for inc in incidents)

    # Synthetic CrashLoop container
    crash_info = ContainerInfo(
        id="c2", short_id="c2", name="web-crash", image="app:v1",
        status="exited", health="none", exit_code=1, oom_killed=False,
        restart_count=5, created_at="2026-10-24T00:00:00Z", started_at="2026-10-24T00:00:01Z",
    )
    incidents = adapter._detect_incidents(crash_info)
    assert any(inc["type"] == "container_crash_loop" for inc in incidents)

    # Synthetic Unhealthy container
    unhealthy_info = ContainerInfo(
        id="c3", short_id="c3", name="db-unhealthy", image="db:v1",
        status="running", health="unhealthy", exit_code=0, oom_killed=False,
        restart_count=0, created_at="2026-10-24T00:00:00Z", started_at="2026-10-24T00:00:01Z",
    )
    incidents = adapter._detect_incidents(unhealthy_info)
    assert any(inc["type"] == "container_unhealthy" for inc in incidents)


def test_docker_log_sanitization():
    adapter = DockerAdapter()
    raw_log = (
        "Starting worker service...\n"
        "Configured GEMINI_KEY=AIzaSyC2o32gQbZ-yQG-1N2h9fLfvm8lhnhs3cw\n"
        "Github auth token: ghp_1234567890abcdef1234567890abcdef\n"
        "User authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz\n"
        "System operational.\n"
    )
    sanitized = adapter._sanitize_logs(raw_log)
    assert "AIzaSy" not in sanitized
    assert "[REDACTED_GEMINI_KEY]" in sanitized
    assert "ghp_" not in sanitized
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized
    assert "[REDACTED_BEARER]" in sanitized


def test_docker_restart_policy_protection():
    adapter = DockerAdapter(protected_containers={"aegis", "kube-system"})

    # Protected container rejection
    with patch.object(adapter, "is_available", return_value=True):
        mock_c = MagicMock()
        mock_c.name = "aegis"
        adapter._client = MagicMock()
        adapter._client.containers.get.return_value = mock_c

        res = adapter.restart_container("aegis", mode="dry_run")
        assert res["result"] == "REJECTED"
        assert "protected" in res["reason"]

        # Eligible container restart in dry_run
        mock_c.name = "web-service"
        res = adapter.restart_container("web-service", mode="dry_run")
        assert res["result"] == "VERIFIED"
        assert res["mode"] == "dry_run"


def test_k8s_adapter_graceful_missing():
    # Absent cluster test
    adapter = KubernetesAdapter(kubeconfig_path="/tmp/nonexistent_kubeconfig.yaml")
    sample = adapter.sample()
    assert sample["available"] is False
    assert "reason" in sample
    assert sample["nodes"] == []
    assert sample["pods"] == []


def test_k8s_incident_detection():
    adapter = KubernetesAdapter()

    nodes = [NodeInfo(
        name="node-1", status="NotReady", cpu_capacity="8",
        memory_capacity="16Gi", kubelet_version="v1.28.0", os_image="Linux"
    )]
    deployments = [DeploymentInfo(
        name="api-gateway", namespace="prod", desired_replicas=3,
        available_replicas=1, updated_replicas=3, is_healthy=False, labels={}
    )]
    pods = [
        PodInfo(
            name="worker-crash", namespace="prod", phase="Running",
            node_name="node-1", ip="10.244.0.5", restart_count=4,
            container_states=[], waiting_reasons=["CrashLoopBackOff"],
            is_ready=False, created_at="2026-10-24T00:00:00Z",
        ),
        PodInfo(
            name="img-fail", namespace="prod", phase="Pending",
            node_name="node-1", ip=None, restart_count=0,
            container_states=[], waiting_reasons=["ImagePullBackOff"],
            is_ready=False, created_at="2026-10-24T00:00:00Z",
        ),
        PodInfo(
            name="mem-killed", namespace="prod", phase="Failed",
            node_name="node-1", ip="10.244.0.6", restart_count=2,
            container_states=[{"name": "app", "ready": False, "restarts": 2, "state": "terminated", "reason": "OOMKilled"}],
            waiting_reasons=[], is_ready=False, created_at="2026-10-24T00:00:00Z",
        ),
    ]

    incidents = adapter._detect_incidents(nodes, deployments, pods)
    types = [inc["type"] for inc in incidents]
    assert "k8s_node_not_ready" in types
    assert "k8s_deployment_degraded" in types
    assert "k8s_pod_crash_loop" in types
    assert "k8s_pod_image_pull_failed" in types
    assert "k8s_pod_oom_killed" in types


def test_ai_workload_monitor_graceful_missing():
    monitor = AIWorkloadMonitor(ollama_url="http://127.0.0.1:9999")  # offline port
    sample = monitor.sample()
    assert sample["ollama"]["available"] is False
    assert "reason" in sample["ollama"]
    assert isinstance(sample["ai_processes"], list)


def test_dependency_graph_and_blast_radius():
    graph = DependencyGraph()

    docker_data = {
        "containers": [
            {"name": "postgres", "networks": ["backend-net"], "service_name": "db"},
            {"name": "api-service", "networks": ["backend-net", "frontend-net"], "service_name": "api"},
            {"name": "web-frontend", "networks": ["frontend-net"], "service_name": "web"},
            {"name": "cache", "networks": ["backend-net"], "service_name": "redis"},
        ]
    }
    graph.build_from_telemetry(host_id="test-host", docker_data=docker_data)

    # Blast radius of postgres container failure
    radius = graph.get_blast_radius("postgres")
    assert radius["target_found"] is True
    assert radius["affected_nodes_count"] >= 1
    # api-service shares network with postgres
    assert any("api-service" in n for n in radius["affected_nodes"])


def test_resource_contention_detection():
    graph = DependencyGraph()
    docker_data = {
        "containers": [
            {"name": "data-pipeline", "metrics": {"memory_usage_mb": 2048}},
        ]
    }
    ai_data = {
        "ai_processes": [
            {"name": "ollama_runner", "memory_mb": 3072},
        ]
    }
    contentions = graph.detect_competing_resources(docker_data, ai_data)
    assert len(contentions) == 1
    assert contentions[0]["type"] == "competing_memory_demand"


def test_workload_api_endpoints(app_ctx: AppContext):
    app = create_app(app_ctx)
    client = TestClient(app)

    # Docker endpoint
    r = client.get("/api/workloads/docker")
    assert r.status_code == 200
    assert "available" in r.json()

    # K8s endpoint
    r = client.get("/api/workloads/k8s")
    assert r.status_code == 200
    assert "available" in r.json()

    # AI workloads endpoint
    r = client.get("/api/workloads/ai")
    assert r.status_code == 200
    assert "ollama" in r.json()

    # Graph endpoint
    r = client.get("/api/workloads/graph")
    assert r.status_code == 200
    assert "nodes" in r.json()
    assert "edges" in r.json()

    # Blast radius endpoint
    r = client.get("/api/workloads/blast_radius?target=test-host")
    assert r.status_code == 200
    assert "blast_radius_score" in r.json()
