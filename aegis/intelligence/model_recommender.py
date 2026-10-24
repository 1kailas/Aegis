"""AI Model Intelligence, Benchmarking & Recommendation Engine.

Analyzes physical host and cluster hardware (VRAM, system RAM, CPU cores, memory bandwidth)
against a comprehensive catalog of open-source AI models. Categorizes models into:
- BEST / OPTIMAL: 100% GPU offload, high tokens/sec, ample KV cache headroom.
- MARGINAL: Requires partial CPU layer spilling, noticeable latency penalty.
- WORST / INCOMPATIBLE: High risk of OOM kills, swap thrashing, or impossible to fit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_LOG = logging.getLogger("aegis.intelligence.model_recommender")


class ModelTask(str, Enum):
    CODING = "CODING"
    REASONING = "REASONING"
    FAST_CHAT = "FAST_CHAT"
    EMBEDDING = "EMBEDDING"


class FitTier(str, Enum):
    OPTIMAL = "OPTIMAL"
    GOOD = "GOOD"
    MARGINAL = "MARGINAL"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass
class ModelSpec:
    name: str
    display_name: str
    family: str
    parameters_b: float
    quantization: str
    task: ModelTask
    vram_required_gb: float       # Weights + minimal KV cache (4k)
    vram_recommended_gb: float    # Weights + full 32k KV cache
    ram_required_gb: float        # Minimum RAM for CPU-only execution
    benchmark_score: float        # Composite benchmark rating (0-100)
    description: str
    docker_image: str = "ollama/ollama:latest"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "family": self.family,
            "parameters_b": self.parameters_b,
            "quantization": self.quantization,
            "task": self.task.value,
            "vram_required_gb": self.vram_required_gb,
            "vram_recommended_gb": self.vram_recommended_gb,
            "ram_required_gb": self.ram_required_gb,
            "benchmark_score": self.benchmark_score,
            "description": self.description,
            "docker_image": self.docker_image,
        }


@dataclass
class ModelRecommendation:
    model: ModelSpec
    fit_tier: FitTier
    fit_score: int                 # 0 - 100
    recommended: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimated_tokens_per_sec: float = 0.0
    memory_headroom_gb: float = 0.0
    can_run_100_percent_gpu: bool = False
    offload_layers_percent: float = 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.as_dict(),
            "fit_tier": self.fit_tier.value,
            "fit_score": self.fit_score,
            "recommended": self.recommended,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "estimated_tokens_per_sec": round(self.estimated_tokens_per_sec, 1),
            "memory_headroom_gb": round(self.memory_headroom_gb, 2),
            "can_run_100_percent_gpu": self.can_run_100_percent_gpu,
            "offload_layers_percent": round(self.offload_layers_percent, 1),
        }


@dataclass
class HardwareProfile:
    total_vram_gb: float
    total_ram_gb: float
    cpu_cores: int
    has_gpu: bool
    gpu_names: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_vram_gb": round(self.total_vram_gb, 2),
            "total_ram_gb": round(self.total_ram_gb, 2),
            "cpu_cores": self.cpu_cores,
            "has_gpu": self.has_gpu,
            "gpu_names": self.gpu_names,
        }


# Curated catalog of current high-performance open-source AI models
DEFAULT_MODEL_CATALOG: list[ModelSpec] = [
    # --- Coding & Software Engineering ---
    ModelSpec(
        name="qwen2.5-coder:1.5b",
        display_name="Qwen 2.5 Coder (1.5B Q4_K_M)",
        family="qwen",
        parameters_b=1.5,
        quantization="Q4_K_M",
        task=ModelTask.CODING,
        vram_required_gb=1.6,
        vram_recommended_gb=2.5,
        ram_required_gb=3.5,
        benchmark_score=78.2,
        description="Ultra-lightweight code model for edge devices, instant autocomplete, and low-spec laptops.",
    ),
    ModelSpec(
        name="qwen2.5-coder:7b",
        display_name="Qwen 2.5 Coder (7B Q4_K_M)",
        family="qwen",
        parameters_b=7.0,
        quantization="Q4_K_M",
        task=ModelTask.CODING,
        vram_required_gb=5.2,
        vram_recommended_gb=7.5,
        ram_required_gb=10.0,
        benchmark_score=89.6,
        description="Benchmark leader for autonomous repository repair, unit test generation, and bug fixing.",
    ),
    ModelSpec(
        name="qwen2.5-coder:14b",
        display_name="Qwen 2.5 Coder (14B Q4_K_M)",
        family="qwen",
        parameters_b=14.0,
        quantization="Q4_K_M",
        task=ModelTask.CODING,
        vram_required_gb=9.8,
        vram_recommended_gb=13.5,
        ram_required_gb=18.0,
        benchmark_score=93.4,
        description="Elite code intelligence matching GPT-4o-mini on HumanEval and complex multi-file architectural refactoring.",
    ),
    ModelSpec(
        name="qwen2.5-coder:32b",
        display_name="Qwen 2.5 Coder (32B Q4_K_M)",
        family="qwen",
        parameters_b=32.0,
        quantization="Q4_K_M",
        task=ModelTask.CODING,
        vram_required_gb=21.5,
        vram_recommended_gb=26.0,
        ram_required_gb=36.0,
        benchmark_score=96.8,
        description="State-of-the-art open coding model for deep algorithmic debugging and large repository understanding.",
    ),

    # --- Reasoning & Complex Logic ---
    ModelSpec(
        name="deepseek-r1:8b",
        display_name="DeepSeek R1 Distill (8B Q4_K_M)",
        family="deepseek",
        parameters_b=8.0,
        quantization="Q4_K_M",
        task=ModelTask.REASONING,
        vram_required_gb=5.8,
        vram_recommended_gb=8.2,
        ram_required_gb=11.0,
        benchmark_score=91.2,
        description="Reinforcement-learning driven reasoning model with chain-of-thought problem decomposition.",
    ),
    ModelSpec(
        name="deepseek-r1:14b",
        display_name="DeepSeek R1 Distill (14B Q4_K_M)",
        family="deepseek",
        parameters_b=14.0,
        quantization="Q4_K_M",
        task=ModelTask.REASONING,
        vram_required_gb=10.2,
        vram_recommended_gb=14.0,
        ram_required_gb=19.0,
        benchmark_score=94.8,
        description="High-tier mathematical reasoning, root-cause deduction, and incident causal analysis.",
    ),
    ModelSpec(
        name="deepseek-r1:32b",
        display_name="DeepSeek R1 Distill (32B Q4_K_M)",
        family="deepseek",
        parameters_b=32.0,
        quantization="Q4_K_M",
        task=ModelTask.REASONING,
        vram_required_gb=22.0,
        vram_recommended_gb=27.0,
        ram_required_gb=38.0,
        benchmark_score=97.5,
        description="Top-tier autonomous reasoning engine for complex distributed failure cascades.",
    ),
    ModelSpec(
        name="deepseek-r1:70b",
        display_name="DeepSeek R1 Distill (70B Q4_K_M)",
        family="deepseek",
        parameters_b=70.0,
        quantization="Q4_K_M",
        task=ModelTask.REASONING,
        vram_required_gb=44.0,
        vram_recommended_gb=52.0,
        ram_required_gb=72.0,
        benchmark_score=99.1,
        description="Frontier reasoning capabilities rivaling OpenAI o1 on competitive benchmarks.",
    ),

    # --- General Purpose & Fast Chat ---
    ModelSpec(
        name="llama3.3:8b",
        display_name="Llama 3.3 (8B Q4_K_M)",
        family="llama",
        parameters_b=8.0,
        quantization="Q4_K_M",
        task=ModelTask.FAST_CHAT,
        vram_required_gb=5.6,
        vram_recommended_gb=8.0,
        ram_required_gb=11.0,
        benchmark_score=87.5,
        description="Versatile general-purpose model for operations chat, log summarization, and incident triage.",
    ),
    ModelSpec(
        name="mistral:7b",
        display_name="Mistral v0.3 (7B Q4_K_M)",
        family="mistral",
        parameters_b=7.0,
        quantization="Q4_K_M",
        task=ModelTask.FAST_CHAT,
        vram_required_gb=5.1,
        vram_recommended_gb=7.2,
        ram_required_gb=10.0,
        benchmark_score=85.2,
        description="Fast sliding-window attention model with rapid Time-To-First-Token.",
    ),
    ModelSpec(
        name="phi4:14b",
        display_name="Phi-4 (14B Q4_K_M)",
        family="phi",
        parameters_b=14.0,
        quantization="Q4_K_M",
        task=ModelTask.FAST_CHAT,
        vram_required_gb=9.5,
        vram_recommended_gb=13.0,
        ram_required_gb=18.0,
        benchmark_score=92.1,
        description="Microsoft's compact powerhouse trained on synthetic high-quality reasoning textbooks.",
    ),
    ModelSpec(
        name="llama3.3:70b",
        display_name="Llama 3.3 (70B Q4_K_M)",
        family="llama",
        parameters_b=70.0,
        quantization="Q4_K_M",
        task=ModelTask.FAST_CHAT,
        vram_required_gb=43.5,
        vram_recommended_gb=52.0,
        ram_required_gb=72.0,
        benchmark_score=98.4,
        description="Frontier open-weights foundation model. Requires multi-GPU VRAM (e.g. 2x RTX 3090/4090 or A100).",
    ),

    # --- Embeddings & Retrieval ---
    ModelSpec(
        name="nomic-embed-text:latest",
        display_name="Nomic Embed Text v1.5",
        family="nomic",
        parameters_b=0.14,
        quantization="FP16",
        task=ModelTask.EMBEDDING,
        vram_required_gb=0.6,
        vram_recommended_gb=1.0,
        ram_required_gb=1.5,
        benchmark_score=92.8,
        description="8192-token context embedding model for semantic search over system logs and incident archives.",
    ),
]


class ModelRecommender:
    """Evaluates physical computing hardware and recommends the optimal AI models."""

    def __init__(self, catalog: list[ModelSpec] | None = None):
        self.catalog = catalog or list(DEFAULT_MODEL_CATALOG)

    def extract_profile(
        self,
        snapshot: dict[str, Any] | None = None,
        gpu_data: dict[str, Any] | None = None,
    ) -> HardwareProfile:
        """Extracts hardware profile from live system telemetry."""
        total_vram_gb = 0.0
        gpu_names: list[str] = []
        has_gpu = False

        if gpu_data:
            devices = gpu_data.get("devices", [])
            for d in devices:
                has_gpu = True
                gpu_names.append(d.get("model", "GPU"))
                # If memory_total_mb is reported
                mem_mb = d.get("memory_total_mb", 0)
                if mem_mb:
                    total_vram_gb += mem_mb / 1024.0

        # System RAM and CPU cores (with psutil fallback)
        total_ram_gb = 16.0
        cpu_cores = 8
        try:
            import psutil
            total_ram_gb = psutil.virtual_memory().total / (1024.0 ** 3)
            cpu_cores = psutil.cpu_count(logical=True) or 8
        except Exception:
            pass

        if snapshot:
            host = snapshot.get("host") or {}
            if host.get("cpu_cores"):
                cpu_cores = host.get("cpu_cores")
            latest = snapshot.get("latest") or {}
            mem = latest.get("memory") or {}
            if mem.get("total_mb"):
                total_ram_gb = mem["total_mb"] / 1024.0

        return HardwareProfile(
            total_vram_gb=total_vram_gb,
            total_ram_gb=total_ram_gb,
            cpu_cores=cpu_cores,
            has_gpu=has_gpu,
            gpu_names=gpu_names,
        )

    def evaluate_model(self, model: ModelSpec, profile: HardwareProfile) -> ModelRecommendation:
        """Evaluates a single model against a hardware profile."""
        reasons: list[str] = []
        warnings: list[str] = []

        vram = profile.total_vram_gb
        ram = profile.total_ram_gb

        # 1. GPU Acceleration Evaluation
        can_run_gpu = False
        offload_pct = 0.0
        est_tps = 5.0
        fit_tier = FitTier.INCOMPATIBLE
        headroom = 0.0

        if profile.has_gpu and vram > 0:
            if vram >= model.vram_recommended_gb:
                # OPTIMAL: 100% in VRAM with plenty of KV cache headroom
                fit_tier = FitTier.OPTIMAL
                can_run_gpu = True
                offload_pct = 100.0
                headroom = vram - model.vram_recommended_gb
                est_tps = max(25.0, 110.0 - (model.parameters_b * 1.8))
                reasons.append(f"100% offload into {vram:.1f} GB VRAM with {headroom:.1f} GB headroom for 32k context.")
                reasons.append(f"Estimated throughput: ~{est_tps:.0f} tokens/s with zero CPU memory swapping.")

            elif vram >= model.vram_required_gb:
                # GOOD: Fits in VRAM with modest KV cache
                fit_tier = FitTier.GOOD
                can_run_gpu = True
                offload_pct = 100.0
                headroom = vram - model.vram_required_gb
                est_tps = max(18.0, 85.0 - (model.parameters_b * 1.6))
                reasons.append(f"Fits completely in VRAM for standard 4k-8k context ({headroom:.1f} GB headroom).")
                warnings.append("Extended context windows (>16k tokens) may begin to pressure VRAM.")

            elif (vram + ram * 0.7) >= model.vram_required_gb and ram >= model.ram_required_gb:
                # MARGINAL: Partial offload into system RAM
                fit_tier = FitTier.MARGINAL
                can_run_gpu = False
                offload_pct = max(10.0, min(85.0, (vram / model.vram_required_gb) * 100))
                headroom = 0.0
                est_tps = max(2.5, 12.0 - (model.parameters_b * 0.2))
                warnings.append(
                    f"Model requires {model.vram_required_gb:.1f} GB, but host only has {vram:.1f} GB VRAM."
                )
                warnings.append(
                    f"Partial layer offloading (~{offload_pct:.0f}% GPU / {100-offload_pct:.0f}% CPU RAM). "
                    f"Expect significant throughput drop (~{est_tps:.1f} tokens/s)."
                )

            else:
                # INCOMPATIBLE
                fit_tier = FitTier.INCOMPATIBLE
                can_run_gpu = False
                offload_pct = 0.0
                headroom = 0.0
                est_tps = 0.8
                warnings.append(
                    f"WORST: Model requires {model.vram_required_gb:.1f} GB VRAM / {model.ram_required_gb:.1f} GB RAM. "
                    f"Exceeds host capacity ({vram:.1f} GB VRAM, {ram:.1f} GB RAM)."
                )
                warnings.append("High probability of CUDA out-of-memory (OOM) kill or severe system unresponsiveness.")

        else:
            # CPU-Only Mode
            if ram >= model.ram_required_gb * 1.2:
                fit_tier = FitTier.GOOD if model.parameters_b <= 7.0 else FitTier.MARGINAL
                can_run_gpu = False
                offload_pct = 0.0
                headroom = ram - model.ram_required_gb
                est_tps = max(2.0, min(22.0, (profile.cpu_cores * 2.5) / (model.parameters_b * 0.8)))
                reasons.append(f"CPU-only mode: Fits in {ram:.1f} GB system RAM with {headroom:.1f} GB headroom.")
                if model.parameters_b > 7.0:
                    warnings.append(f"Large parameter count ({model.parameters_b}B) on CPU will produce modest throughput (~{est_tps:.1f} t/s).")
            elif ram >= model.ram_required_gb:
                fit_tier = FitTier.MARGINAL
                can_run_gpu = False
                offload_pct = 0.0
                est_tps = 1.5
                warnings.append(f"CPU RAM is near minimum threshold ({ram:.1f} GB vs {model.ram_required_gb:.1f} GB required).")
            else:
                fit_tier = FitTier.INCOMPATIBLE
                can_run_gpu = False
                offload_pct = 0.0
                est_tps = 0.5
                warnings.append(f"WORST: Insufficient RAM for CPU execution ({ram:.1f} GB vs {model.ram_required_gb:.1f} GB required).")

        # Fit score calculation (0 - 100)
        fit_score = 0
        if fit_tier == FitTier.OPTIMAL:
            fit_score = min(100, int(model.benchmark_score * 0.7 + 30))
        elif fit_tier == FitTier.GOOD:
            fit_score = min(88, int(model.benchmark_score * 0.65 + 20))
        elif fit_tier == FitTier.MARGINAL:
            fit_score = min(60, int(model.benchmark_score * 0.4 + 10))
        else:
            fit_score = max(5, int(model.benchmark_score * 0.15))

        recommended = fit_tier in (FitTier.OPTIMAL, FitTier.GOOD)

        return ModelRecommendation(
            model=model,
            fit_tier=fit_tier,
            fit_score=fit_score,
            recommended=recommended,
            reasons=reasons,
            warnings=warnings,
            estimated_tokens_per_sec=est_tps,
            memory_headroom_gb=headroom,
            can_run_100_percent_gpu=can_run_gpu,
            offload_layers_percent=offload_pct,
        )

    def get_recommendations(
        self,
        profile: HardwareProfile,
        task: ModelTask | str | None = None,
    ) -> dict[str, Any]:
        """Evaluates all catalog models and ranks them by suitability for the current hardware."""
        selected_task = None
        if task:
            try:
                selected_task = ModelTask(task.upper() if isinstance(task, str) else task)
            except ValueError:
                pass

        evaluated: list[ModelRecommendation] = []
        for model in self.catalog:
            if selected_task and model.task != selected_task:
                continue
            evaluated.append(self.evaluate_model(model, profile))

        # Sort by fit_score descending
        evaluated.sort(key=lambda r: r.fit_score, reverse=True)

        best_models = [r for r in evaluated if r.fit_tier in (FitTier.OPTIMAL, FitTier.GOOD)]
        marginal_models = [r for r in evaluated if r.fit_tier == FitTier.MARGINAL]
        worst_models = [r for r in evaluated if r.fit_tier == FitTier.INCOMPATIBLE]

        # Identify top recommendation per task category
        top_by_task: dict[str, dict[str, Any]] = {}
        for t in ModelTask:
            matches = [r for r in evaluated if r.model.task == t and r.recommended]
            if matches:
                top_by_task[t.value] = matches[0].as_dict()
            else:
                # If no optimal model, find best available
                task_all = [r for r in evaluated if r.model.task == t]
                if task_all:
                    top_by_task[t.value] = task_all[0].as_dict()

        return {
            "hardware_profile": profile.as_dict(),
            "summary": {
                "total_models_evaluated": len(evaluated),
                "optimal_count": len(best_models),
                "marginal_count": len(marginal_models),
                "incompatible_count": len(worst_models),
            },
            "top_picks_by_task": top_by_task,
            "best_models": [r.as_dict() for r in best_models],
            "marginal_models": [r.as_dict() for r in marginal_models],
            "worst_models": [r.as_dict() for r in worst_models],
            "all_ranked": [r.as_dict() for r in evaluated],
        }
