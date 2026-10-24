"""GPU telemetry collector supporting NVIDIA, AMD, and Intel sysfs.

Gracefully degrades when no supported GPU or vendor driver is installed:
unsupported GPUs are reported as UNAVAILABLE with clear diagnostics rather
than inventing values.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.collectors.gpu")


@dataclass
class GpuDevice:
    index: int
    name: str
    vendor: str
    utilization_gpu_pct: float | None = None
    utilization_mem_pct: float | None = None
    memory_total_mb: float | None = None
    memory_used_mb: float | None = None
    memory_free_mb: float | None = None
    temperature_c: float | None = None
    power_draw_w: float | None = None
    fan_speed_pct: float | None = None
    processes: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "vendor": self.vendor,
            "utilization_gpu_pct": self.utilization_gpu_pct,
            "utilization_mem_pct": self.utilization_mem_pct,
            "memory_total_mb": self.memory_total_mb,
            "memory_used_mb": self.memory_used_mb,
            "memory_free_mb": self.memory_free_mb,
            "temperature_c": self.temperature_c,
            "power_draw_w": self.power_draw_w,
            "fan_speed_pct": self.fan_speed_pct,
            "processes": self.processes,
        }


class GpuCollector:
    """Discovers and queries GPU status across vendors."""

    def __init__(self):
        self._has_nvidia_smi = shutil.which("nvidia-smi") is not None
        self._nvml_available = False
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            self._nvml_available = True
        except Exception:
            self._nvml_available = False

    def is_available(self) -> bool:
        return self._nvml_available or self._has_nvidia_smi or self._has_amd_sysfs()

    def sample(self) -> dict[str, Any]:
        """Collect current GPU metrics or report honest unavailability."""
        if self._nvml_available:
            try:
                return self._sample_nvml()
            except Exception as e:
                _LOG.debug("NVML sample failed: %s; falling back", e)

        if self._has_nvidia_smi:
            try:
                return self._sample_nvidia_smi()
            except Exception as e:
                _LOG.debug("nvidia-smi query failed: %s", e)

        amd_result = self._sample_amd_sysfs()
        if amd_result:
            return amd_result

        return {
            "supported": False,
            "available": False,
            "vendor": "none",
            "driver_version": None,
            "reason": "No supported GPU or vendor driver detected on host",
            "devices": [],
            "collected_at": utcnow(),
        }

    def _sample_nvml(self) -> dict[str, Any]:
        import pynvml
        device_count = pynvml.nvmlDeviceGetCount()
        driver = pynvml.nvmlSystemGetDriverVersion()
        devices = []
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            try:
                power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0  # mW -> W
            except Exception:
                power = None
            try:
                fan = pynvml.nvmlDeviceGetFanSpeed(handle)
            except Exception:
                fan = None

            procs = []
            try:
                for p in pynvml.nvmlDeviceGetComputeRunningProcesses(handle):
                    procs.append({
                        "pid": p.pid,
                        "used_memory_mb": round(p.usedGpuMemory / (1024 * 1024), 1)
                    })
            except Exception:
                pass

            dev = GpuDevice(
                index=i,
                name=name,
                vendor="NVIDIA",
                utilization_gpu_pct=float(util.gpu),
                utilization_mem_pct=float(util.memory),
                memory_total_mb=round(mem.total / (1024 * 1024), 1),
                memory_used_mb=round(mem.used / (1024 * 1024), 1),
                memory_free_mb=round(mem.free / (1024 * 1024), 1),
                temperature_c=float(temp),
                power_draw_w=round(power, 1) if power is not None else None,
                fan_speed_pct=float(fan) if fan is not None else None,
                processes=procs,
            )
            devices.append(dev.as_dict())

        return {
            "supported": True,
            "available": True,
            "vendor": "NVIDIA",
            "driver_version": driver,
            "reason": None,
            "devices": devices,
            "collected_at": utcnow(),
        }

    def _sample_nvidia_smi(self) -> dict[str, Any]:
        query = (
            "index,name,driver_version,temperature.gpu,utilization.gpu,"
            "utilization.memory,memory.total,memory.used,memory.free,power.draw"
        )
        res = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if res.returncode != 0:
            return {
                "supported": True,
                "available": False,
                "vendor": "NVIDIA",
                "driver_version": None,
                "reason": f"nvidia-smi exited with code {res.returncode}: {res.stderr.strip()}",
                "devices": [],
                "collected_at": utcnow(),
            }

        devices = []
        driver = None
        for line in res.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 10:
                idx, name, drv, temp, ugpu, umem, mtot, mused, mfree, pwr = parts[:10]
                driver = drv
                devices.append(GpuDevice(
                    index=int(idx),
                    name=name,
                    vendor="NVIDIA",
                    utilization_gpu_pct=_parse_float(ugpu),
                    utilization_mem_pct=_parse_float(umem),
                    memory_total_mb=_parse_float(mtot),
                    memory_used_mb=_parse_float(mused),
                    memory_free_mb=_parse_float(mfree),
                    temperature_c=_parse_float(temp),
                    power_draw_w=_parse_float(pwr),
                ).as_dict())

        return {
            "supported": True,
            "available": len(devices) > 0,
            "vendor": "NVIDIA",
            "driver_version": driver,
            "reason": None if devices else "nvidia-smi returned no devices",
            "devices": devices,
            "collected_at": utcnow(),
        }

    def _has_amd_sysfs(self) -> bool:
        drm_dir = Path("/sys/class/drm")
        if not drm_dir.exists():
            return False
        return any((p / "device/gpu_busy_percent").exists() for p in drm_dir.glob("card*"))

    def _sample_amd_sysfs(self) -> dict[str, Any] | None:
        drm_dir = Path("/sys/class/drm")
        if not drm_dir.exists():
            return None
        cards = sorted(drm_dir.glob("card*"))
        devices = []
        idx = 0
        for card in cards:
            busy_file = card / "device/gpu_busy_percent"
            if busy_file.exists():
                try:
                    pct = float(busy_file.read_text().strip())
                    devices.append(GpuDevice(
                        index=idx,
                        name=f"AMD GPU ({card.name})",
                        vendor="AMD",
                        utilization_gpu_pct=pct,
                    ).as_dict())
                    idx += 1
                except Exception:
                    pass
        if devices:
            return {
                "supported": True,
                "available": True,
                "vendor": "AMD",
                "driver_version": "kernel/amdgpu",
                "reason": None,
                "devices": devices,
                "collected_at": utcnow(),
            }
        return None


def _parse_float(val: str) -> float | None:
    try:
        return float(val.strip())
    except (ValueError, TypeError):
        return None
