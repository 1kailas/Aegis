"""Aegis AI v3.1 Automated Test Suite.

Covers:
- Real-Time AI Model Benchmarker (TTFT ms, tokens/sec, prompt eval speed, physical memory bandwidth simulation)
- Database persistence for model benchmark runs (model_benchmarks table)
- Dynamic AI Memory Optimizer & idle model eviction
- Cluster-wide AI capacity planning & multi-node workload distribution
- Closed-Loop Self-Healing automated incident recovery verification (CONTAINED -> RESOLVED)
- v3.1 REST API endpoints (/api/models/benchmark, /api/models/benchmarks, /api/models/optimize, /api/models/cluster-capacity)
- Developer CLI 'aegis models benchmark' and 'aegis models optimize'
"""

from __future__ import annotations

import argparse
import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.cli.main import cmd_models
from aegis.config import load_config
from aegis.intelligence.benchmarking import BenchmarkStatus, ModelBenchmarker, ModelOptimizer


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_v31.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_model_benchmarker_simulation_and_persistence(app_ctx: AppContext):
    """Verify inference throughput benchmarking, TTFT calculation, and persistence."""
    benchmarker = app_ctx.benchmarker

    # 1. Execute benchmark for Qwen 2.5 Coder 7B
    rec1 = benchmarker.run_benchmark(
        model_name="qwen2.5-coder:7b",
        prompt="Explain microservices vs monolith architecture.",
        endpoint="http://127.0.0.1:19999",  # unreachable endpoint triggers accurate physical bandwidth simulation
    )
    assert rec1.model_name == "qwen2.5-coder:7b"
    assert rec1.status == BenchmarkStatus.SIMULATED
    assert rec1.tokens_per_sec > 0.0
    assert rec1.ttft_ms > 0.0
    assert rec1.total_tokens > 0
    assert rec1.duration_s > 0.0
    assert "hardware profile simulation" in rec1.details

    # 2. Execute benchmark for DeepSeek R1 14B
    rec2 = benchmarker.run_benchmark(
        model_name="deepseek-r1:14b",
        prompt="Solve the dining philosophers problem.",
        endpoint="http://127.0.0.1:19999",
    )
    assert rec2.model_name == "deepseek-r1:14b"
    assert rec2.tokens_per_sec > 0.0

    # 3. List benchmarks from database
    benches = benchmarker.list_benchmarks()
    assert len(benches) >= 2
    bids = [b["benchmark_id"] for b in benches]
    assert rec1.benchmark_id in bids
    assert rec2.benchmark_id in bids

    # 4. Filter benchmarks by model_name
    filtered = benchmarker.list_benchmarks(model_name="qwen2.5-coder:7b")
    assert len(filtered) >= 1
    assert all(b["model_name"] == "qwen2.5-coder:7b" for b in filtered)


def test_model_optimizer_and_cluster_capacity(app_ctx: AppContext):
    """Verify dynamic VRAM optimization and cluster-wide AI capacity planning."""
    optimizer = app_ctx.optimizer

    # 1. Run VRAM optimization
    opt_res = optimizer.optimize_vram(ollama_url="http://127.0.0.1:19999")
    assert opt_res["status"] == "OPTIMIZED"
    assert "unloaded_models" in opt_res
    assert "reclaimed_vram_mb" in opt_res

    # 2. Calculate cluster AI capacity across simulated fleet
    mock_nodes = [
        {
            "node_id": "gpu-worker-1",
            "hostname": "k8s-gpu-1.local",
            "ip_address": "10.0.1.101",
            "status": "ONLINE",
            "metadata": {"total_vram_gb": 24.0, "total_ram_gb": 64.0},
        },
        {
            "node_id": "gpu-worker-2",
            "hostname": "k8s-gpu-2.local",
            "ip_address": "10.0.1.102",
            "status": "ONLINE",
            "metadata": {"total_vram_gb": 16.0, "total_ram_gb": 32.0},
        },
        {
            "node_id": "cpu-worker-3",
            "hostname": "k8s-cpu-3.local",
            "ip_address": "10.0.1.103",
            "status": "ONLINE",
            "metadata": {"total_vram_gb": 0.0, "total_ram_gb": 64.0},
        },
    ]

    cap = optimizer.cluster_ai_capacity(mock_nodes)
    assert cap["online_nodes"] == 3
    assert cap["total_cluster_vram_gb"] == 40.0
    assert cap["total_cluster_ram_gb"] == 160.0

    headroom = cap["concurrent_model_headroom"]
    assert headroom["7b_models"] >= 4
    assert headroom["14b_models"] >= 2
    assert len(cap["node_placements"]) == 3


def test_self_healing_closed_loop_recovery_verification(app_ctx: AppContext):
    """Verify self-healing engine independently verifies CONTAINED incidents and resolves them."""
    incidents = app_ctx.incidents
    self_healing = app_ctx.self_healing

    # Create an incident in CONTAINED state
    inc = incidents.create(
        category="thermal",
        severity="warning",
        title="Thermal Throttling on Package 0",
        summary="CPU temperature rose above warning limit",
    )
    iid = inc["id"]
    incidents.transition(iid, "CONTAINED", "test", note="Remediation cooling profile applied")

    # Run self-healing reconciliation cycle
    report = self_healing.run_reconciliation_cycle()
    assert report.incidents_evaluated >= 1
    assert report.resolved_count >= 1

    # Verify incident state progressed to RECOVERY_VERIFIED
    updated_inc = incidents.get(iid)
    assert updated_inc["state"] == "RECOVERY_VERIFIED"
    assert "Autonomous recovery confirmed" in str(updated_inc.get("lifecycle", []))


def test_v31_rest_api_endpoints(app_ctx: AppContext):
    """Verify v3.1 REST API endpoints for benchmarks, optimization, and cluster capacity."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. POST /api/models/benchmark
    r_bench = client.post("/api/models/benchmark", json={
        "model_name": "qwen2.5-coder:7b",
        "prompt": "Write a quicksort in Python",
    })
    assert r_bench.status_code == 200
    b_data = r_bench.json()
    assert b_data["model_name"] == "qwen2.5-coder:7b"
    assert b_data["tokens_per_sec"] > 0
    assert b_data["ttft_ms"] > 0
    bid = b_data["benchmark_id"]

    # 2. GET /api/models/benchmarks
    r_list = client.get("/api/models/benchmarks")
    assert r_list.status_code == 200
    b_list = r_list.json()
    assert any(b["benchmark_id"] == bid for b in b_list)

    # 3. POST /api/models/optimize
    r_opt = client.post("/api/models/optimize")
    assert r_opt.status_code == 200
    opt_data = r_opt.json()
    assert opt_data["status"] == "OPTIMIZED"

    # 4. GET /api/models/cluster-capacity
    r_cap = client.get("/api/models/cluster-capacity")
    assert r_cap.status_code == 200
    cap_data = r_cap.json()
    assert "concurrent_model_headroom" in cap_data
    assert "total_cluster_vram_gb" in cap_data


def test_v31_cli_benchmark_and_optimize(app_ctx: AppContext):
    """Verify 'aegis models benchmark' and 'aegis models optimize' CLI subcommands."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. aegis models benchmark
    args_bench = argparse.Namespace(
        models_action="benchmark",
        model_name="qwen2.5-coder:1.5b",
        prompt="Explain raft consensus.",
    )
    ret_bench = cmd_models(client, args_bench)
    assert ret_bench == 0

    # 2. aegis models optimize
    args_opt = argparse.Namespace(models_action="optimize")
    ret_opt = cmd_models(client, args_opt)
    assert ret_opt == 0
