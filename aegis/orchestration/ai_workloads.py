"""AI infrastructure and model-serving monitoring adapter.

Monitors local inference servers (Ollama, vLLM, llama.cpp), model availability,
VRAM/RAM allocation, latency, and resource contention with other workloads.
Operates defensively behind modular interfaces so offline servers never crash the agent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import psutil

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.orchestration.ai_workloads")


@dataclass
class LoadedModel:
    name: str
    size_bytes: int
    size_vram_bytes: int
    digest: str
    expires_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size_mb": round(self.size_bytes / (1024 * 1024), 1),
            "size_vram_mb": round(self.size_vram_bytes / (1024 * 1024), 1),
            "digest": self.digest,
            "expires_at": self.expires_at,
        }


@dataclass
class AIWorkloadStatus:
    available: bool
    endpoint: str
    latency_ms: float | None
    installed_models: list[str] = field(default_factory=list)
    active_models: list[dict[str, Any]] = field(default_factory=list)
    total_vram_mb: float = 0.0
    process_info: dict[str, Any] | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "endpoint": self.endpoint,
            "latency_ms": self.latency_ms,
            "installed_models": self.installed_models,
            "active_models": self.active_models,
            "total_vram_mb": self.total_vram_mb,
            "process_info": self.process_info,
            "reason": self.reason,
            "collected_at": utcnow(),
        }


class AIWorkloadMonitor:
    """Monitors local model-serving runtimes and model states."""

    def __init__(self, ollama_url: str = "http://127.0.0.1:11434", timeout_s: float = 2.0):
        self.ollama_url = ollama_url.rstrip("/")
        self.timeout_s = timeout_s

    def sample(self) -> dict[str, Any]:
        """Collects telemetry from Ollama and detects active AI processes."""
        ollama_status = self._sample_ollama()
        ai_procs = self._discover_ai_processes()
        incidents = self._detect_incidents(ollama_status, ai_procs)

        return {
            "ollama": ollama_status.as_dict(),
            "ai_processes": ai_procs,
            "incidents": incidents,
            "collected_at": utcnow(),
        }

    def _sample_ollama(self) -> AIWorkloadStatus:
        start = time.time()
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                # 1. Ping /api/tags
                res_tags = client.get(f"{self.ollama_url}/api/tags")
                latency_ms = round((time.time() - start) * 1000.0, 1)

                if res_tags.status_code != 200:
                    return AIWorkloadStatus(
                        available=False,
                        endpoint=self.ollama_url,
                        latency_ms=latency_ms,
                        reason=f"Ollama returned HTTP {res_tags.status_code}",
                    )

                data_tags = res_tags.json()
                models = [m.get("name", "") for m in data_tags.get("models", [])]

                # 2. Check active running models via /api/ps
                active_models = []
                total_vram = 0.0
                try:
                    res_ps = client.get(f"{self.ollama_url}/api/ps")
                    if res_ps.status_code == 200:
                        for item in res_ps.json().get("models", []):
                            vram = item.get("size_vram", 0)
                            total_vram += vram / (1024 * 1024)
                            active_models.append(LoadedModel(
                                name=item.get("name", "unknown"),
                                size_bytes=item.get("size", 0),
                                size_vram_bytes=vram,
                                digest=item.get("digest", "")[:12],
                                expires_at=item.get("expires_at"),
                            ).as_dict())
                except Exception as e:
                    _LOG.debug("Ollama /api/ps check note: %s", e)

                # Process details for Ollama daemon
                proc_info = self._find_proc_by_name("ollama")

                return AIWorkloadStatus(
                    available=True,
                    endpoint=self.ollama_url,
                    latency_ms=latency_ms,
                    installed_models=models,
                    active_models=active_models,
                    total_vram_mb=round(total_vram, 1),
                    process_info=proc_info,
                    reason=None,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return AIWorkloadStatus(
                available=False,
                endpoint=self.ollama_url,
                latency_ms=None,
                reason=f"Cannot reach Ollama at {self.ollama_url} ({type(e).__name__})",
            )
        except Exception as e:
            return AIWorkloadStatus(
                available=False,
                endpoint=self.ollama_url,
                latency_ms=None,
                reason=f"Unexpected Ollama probe error: {e}",
            )

    def _discover_ai_processes(self) -> list[dict[str, Any]]:
        results = []
        target_names = {"ollama", "vllm", "llama-server", "triton", "python", "python3"}
        for p in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_info"]):
            try:
                name = (p.info.get("name") or "").lower()
                cmd = " ".join(p.info.get("cmdline") or []).lower()
                is_ai = False
                runtime_type = None

                if "ollama" in name or "ollama" in cmd:
                    is_ai = True
                    runtime_type = "ollama"
                elif "vllm" in cmd:
                    is_ai = True
                    runtime_type = "vllm"
                elif "llama-server" in name or "llama-server" in cmd:
                    is_ai = True
                    runtime_type = "llama.cpp"
                elif any(kw in cmd for kw in ("torch", "transformers", "accelerate", "litellm")):
                    is_ai = True
                    runtime_type = "pytorch_huggingface"

                if is_ai:
                    mem = p.info.get("memory_info")
                    rss_mb = round(mem.rss / (1024 * 1024), 1) if mem else 0.0
                    results.append({
                        "pid": p.info["pid"],
                        "runtime": runtime_type,
                        "name": p.info.get("name"),
                        "cpu_percent": round(p.info.get("cpu_percent") or 0.0, 1),
                        "memory_mb": rss_mb,
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return results

    def _find_proc_by_name(self, target: str) -> dict[str, Any] | None:
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            try:
                if target in (p.info.get("name") or "").lower():
                    mem = p.info.get("memory_info")
                    return {
                        "pid": p.info["pid"],
                        "cpu_percent": round(p.info.get("cpu_percent") or 0.0, 1),
                        "memory_mb": round(mem.rss / (1024 * 1024), 1) if mem else 0.0,
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    def _detect_incidents(
        self, status: AIWorkloadStatus, ai_procs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        incidents: list[dict[str, Any]] = []

        # 1. High latency
        if status.available and status.latency_ms and status.latency_ms > 2000.0:
            incidents.append({
                "type": "ai_inference_high_latency",
                "severity": "warning",
                "summary": f"Ollama endpoint latency elevated: {status.latency_ms}ms",
                "evidence": {"endpoint": status.endpoint, "latency_ms": status.latency_ms},
            })

        # 2. Resource contention: multiple AI processes or heavy CPU/RAM usage
        total_ai_mem = sum(p["memory_mb"] for p in ai_procs)
        vm = psutil.virtual_memory()
        total_sys_mb = vm.total / (1024 * 1024)
        if total_sys_mb > 0 and (total_ai_mem / total_sys_mb) >= 0.70:
            incidents.append({
                "type": "ai_resource_saturation",
                "severity": "warning",
                "summary": f"AI workloads consuming {total_ai_mem:.0f}MB ({round((total_ai_mem / total_sys_mb) * 100)}% of host memory)",
                "evidence": {"ai_memory_mb": total_ai_mem, "host_total_mb": round(total_sys_mb)},
            })

        return incidents
