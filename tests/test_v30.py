"""Aegis AI v3.0 Automated Test Suite.

Covers:
- AI Model Intelligence & Recommender Engine (hardware profiling, fit tiers, tokens/sec, KV cache headroom)
- Multi-tier classification (OPTIMAL, GOOD, MARGINAL, INCOMPATIBLE) across GPU and CPU hardware configurations
- One-Click Cloud Native Deployer (Docker container commands and Kubernetes manifest generation)
- Model Deployments lifecycle & SQLite persistence
- v3.0 REST API Endpoints (/api/models/catalog, /api/models/recommendations, /api/models/deploy, /api/orchestration/manifests/k8s)
- Developer CLI 'aegis models' (recommend, deploy, list)
"""

from __future__ import annotations

import argparse
import pytest
from starlette.testclient import TestClient

from aegis.api.server import create_app
from aegis.appstate import AppContext
from aegis.cli.main import cmd_models
from aegis.config import load_config
from aegis.intelligence.model_recommender import (
    DEFAULT_MODEL_CATALOG,
    FitTier,
    HardwareProfile,
    ModelRecommender,
    ModelTask,
)
from aegis.orchestration.deployer import DeploymentStatus, OneClickDeployer


@pytest.fixture
def app_ctx(tmp_path):
    cfg = load_config()
    cfg.database.path = tmp_path / "test_v30.db"
    ctx = AppContext(cfg)
    yield ctx
    ctx.close()


def test_model_recommender_hardware_profiling():
    """Verify hardware evaluation and fit tier assignment across GPU tiers."""
    recommender = ModelRecommender()
    assert len(recommender.catalog) >= 10

    # 1. High-End GPU (24GB VRAM, e.g. RTX 4090 / A5000)
    rtx_4090 = HardwareProfile(
        total_vram_gb=24.0,
        total_ram_gb=64.0,
        cpu_cores=16,
        has_gpu=True,
        gpu_names=["NVIDIA GeForce RTX 4090 24GB"],
    )
    res_4090 = recommender.get_recommendations(rtx_4090)
    ranked_4090 = res_4090["all_ranked"]
    summary_4090 = res_4090["summary"]

    assert summary_4090["total_models_evaluated"] >= 10
    assert summary_4090["optimal_count"] > 0

    # Qwen 2.5 Coder 7B should be OPTIMAL with 100% GPU offload and healthy headroom
    qwen7b = next(r for r in ranked_4090 if r["model"]["name"] == "qwen2.5-coder:7b")
    assert qwen7b["fit_tier"] == FitTier.OPTIMAL.value
    assert qwen7b["can_run_100_percent_gpu"] is True
    assert qwen7b["offload_layers_percent"] == 100.0
    assert qwen7b["memory_headroom_gb"] > 10.0
    assert qwen7b["estimated_tokens_per_sec"] >= 25.0

    # 70B model should be INCOMPATIBLE or MARGINAL on single 24GB GPU
    deepseek70b = next(r for r in ranked_4090 if r["model"]["name"] == "deepseek-r1:70b")
    assert deepseek70b["fit_tier"] in (FitTier.INCOMPATIBLE.value, FitTier.MARGINAL.value)

    # 2. Mid-Tier GPU (8GB VRAM, e.g. RTX 3070 / Laptop GPU)
    rtx_3070 = HardwareProfile(
        total_vram_gb=8.0,
        total_ram_gb=32.0,
        cpu_cores=8,
        has_gpu=True,
        gpu_names=["NVIDIA GeForce RTX 3070 8GB"],
    )
    res_3070 = recommender.get_recommendations(rtx_3070)
    qwen1_5b = next(r for r in res_3070["all_ranked"] if r["model"]["name"] == "qwen2.5-coder:1.5b")
    assert qwen1_5b["fit_tier"] == FitTier.OPTIMAL.value

    qwen32b = next(r for r in res_3070["all_ranked"] if r["model"]["name"] == "qwen2.5-coder:32b")
    assert qwen32b["fit_tier"] in (FitTier.MARGINAL.value, FitTier.INCOMPATIBLE.value)
    if qwen32b["fit_tier"] == FitTier.MARGINAL.value:
        assert qwen32b["can_run_100_percent_gpu"] is False
        assert len(qwen32b["warnings"]) > 0

    # 3. CPU-Only Machine (0GB VRAM, 16GB RAM)
    cpu_only = HardwareProfile(
        total_vram_gb=0.0,
        total_ram_gb=16.0,
        cpu_cores=8,
        has_gpu=False,
        gpu_names=[],
    )
    res_cpu = recommender.get_recommendations(cpu_only)
    assert res_cpu["hardware_profile"]["has_gpu"] is False

    # Small 1.5B or embedding models fit into CPU RAM
    nomic_emb = next(r for r in res_cpu["all_ranked"] if r["model"]["name"] == "nomic-embed-text:latest")
    assert nomic_emb["fit_tier"] in (FitTier.GOOD.value, FitTier.OPTIMAL.value)

    # Massive 70B model is INCOMPATIBLE with 16GB RAM
    r_70b = next(r for r in res_cpu["all_ranked"] if r["model"]["name"] == "llama3.3:70b")
    assert r_70b["fit_tier"] == FitTier.INCOMPATIBLE.value


def test_model_recommender_task_filtering():
    """Verify task filtering and top picks by domain category."""
    recommender = ModelRecommender()
    prof = HardwareProfile(
        total_vram_gb=16.0,
        total_ram_gb=32.0,
        cpu_cores=8,
        has_gpu=True,
        gpu_names=["NVIDIA RTX 4080"],
    )

    # Filter by CODING
    coding_res = recommender.get_recommendations(prof, task="CODING")
    for r in coding_res["all_ranked"]:
        assert r["model"]["task"] == "CODING"

    # Filter by REASONING
    reasoning_res = recommender.get_recommendations(prof, task="REASONING")
    for r in reasoning_res["all_ranked"]:
        assert r["model"]["task"] == "REASONING"

    # Check top_picks_by_task contains all main task categories
    all_res = recommender.get_recommendations(prof)
    top_picks = all_res["top_picks_by_task"]
    assert "CODING" in top_picks
    assert "REASONING" in top_picks
    assert "FAST_CHAT" in top_picks
    assert "EMBEDDING" in top_picks


def test_one_click_deployer_docker_and_k8s(app_ctx: AppContext):
    """Verify Docker container and Kubernetes manifest generation & persistence."""
    deployer = app_ctx.deployer

    # 1. Deploy Docker model
    docker_dep = deployer.deploy_docker(
        model_name="qwen2.5-coder:7b",
        port=11434,
        gpu=True,
    )
    assert docker_dep["model_name"] == "qwen2.5-coder:7b"
    assert docker_dep["runtime"] == "docker"
    assert docker_dep["port"] == 11434
    assert "--gpus all" in docker_dep["manifest"]
    assert "aegis-model-qwen2-5-coder-7b" in docker_dep["manifest"]

    # 2. Deploy Kubernetes model
    k8s_dep = deployer.deploy_k8s(
        model_name="deepseek-r1:14b",
        namespace="ai-workloads",
        port=11434,
        gpu=True,
        replicas=2,
    )
    assert k8s_dep["model_name"] == "deepseek-r1:14b"
    assert k8s_dep["runtime"] == "k8s"
    assert "Deployment" in k8s_dep["manifest"]
    assert "Service" in k8s_dep["manifest"]
    assert "namespace: ai-workloads" in k8s_dep["manifest"]
    assert "replicas: 2" in k8s_dep["manifest"]
    assert "nvidia.com/gpu: \"1\"" in k8s_dep["manifest"]

    # 3. Check persistent database listings
    deployments = deployer.list_deployments()
    assert len(deployments) >= 2
    dep_ids = [d["deployment_id"] for d in deployments]
    assert docker_dep["deployment_id"] in dep_ids
    assert k8s_dep["deployment_id"] in dep_ids

    # 4. Stop deployment
    stop_ok = deployer.stop_deployment(docker_dep["deployment_id"])
    assert stop_ok is True
    updated_deps = deployer.list_deployments()
    stopped_record = next(d for d in updated_deps if d["deployment_id"] == docker_dep["deployment_id"])
    assert stopped_record["status"] == DeploymentStatus.STOPPED.value


def test_v30_rest_api_endpoints(app_ctx: AppContext):
    """Verify v3.0 REST API endpoints for models, recommendations, and deployments."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. GET /api/models/catalog
    r_cat = client.get("/api/models/catalog")
    assert r_cat.status_code == 200
    catalog = r_cat.json()
    assert len(catalog) >= 10
    names = [m["name"] for m in catalog]
    assert "qwen2.5-coder:7b" in names
    assert "deepseek-r1:8b" in names

    # 2. GET /api/models/recommendations
    r_rec = client.get("/api/models/recommendations")
    assert r_rec.status_code == 200
    rec_data = r_rec.json()
    assert "hardware_profile" in rec_data
    assert "all_ranked" in rec_data
    assert "top_picks_by_task" in rec_data

    # 3. GET /api/models/recommendations with task filter
    r_filter = client.get("/api/models/recommendations?task=CODING")
    assert r_filter.status_code == 200
    f_data = r_filter.json()
    for item in f_data["all_ranked"]:
        assert item["model"]["task"] == "CODING"

    # 4. POST /api/models/deploy (Docker)
    r_deploy_docker = client.post("/api/models/deploy", json={
        "model_name": "qwen2.5-coder:1.5b",
        "runtime": "docker",
        "port": 11434,
        "gpu": False,
    })
    assert r_deploy_docker.status_code == 200
    d_res = r_deploy_docker.json()
    assert d_res["runtime"] == "docker"
    assert d_res["model_name"] == "qwen2.5-coder:1.5b"
    dep_id = d_res["deployment_id"]

    # 5. GET /api/models/deployments
    r_list = client.get("/api/models/deployments")
    assert r_list.status_code == 200
    items = r_list.json()
    assert any(i["deployment_id"] == dep_id for i in items)

    # 6. POST /api/models/deployments/{id}/stop
    r_stop = client.post(f"/api/models/deployments/{dep_id}/stop")
    assert r_stop.status_code == 200
    assert r_stop.json()["status"] == "stopped"

    # 7. POST /api/orchestration/manifests/k8s
    r_manifest = client.post("/api/orchestration/manifests/k8s", json={
        "model_name": "deepseek-r1:8b",
        "namespace": "production",
        "port": 11434,
        "gpu": True,
        "replicas": 3,
    })
    assert r_manifest.status_code == 200
    m_data = r_manifest.json()
    assert "yaml" in m_data
    assert "replicas: 3" in m_data["yaml"]
    assert "namespace: production" in m_data["yaml"]


def test_v30_cli_models_commands(app_ctx: AppContext):
    """Verify 'aegis models' CLI subcommands (recommend, deploy, list)."""
    app = create_app(app_ctx)
    client = TestClient(app)

    # 1. aegis models recommend
    args_rec = argparse.Namespace(models_action="recommend", task=None)
    ret_rec = cmd_models(client, args_rec)
    assert ret_rec == 0

    # 2. aegis models recommend --task CODING
    args_rec_task = argparse.Namespace(models_action="recommend", task="CODING")
    ret_rec_task = cmd_models(client, args_rec_task)
    assert ret_rec_task == 0

    # 3. aegis models deploy qwen2.5-coder:7b --runtime docker
    args_deploy = argparse.Namespace(
        models_action="deploy",
        model_name="qwen2.5-coder:7b",
        runtime="docker",
        port=11434,
        no_gpu=False,
    )
    ret_deploy = cmd_models(client, args_deploy)
    assert ret_deploy == 0

    # 4. aegis models list
    args_list = argparse.Namespace(models_action="list")
    ret_list = cmd_models(client, args_list)
    assert ret_list == 0
