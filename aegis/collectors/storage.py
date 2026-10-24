"""Storage, NVMe, and filesystem telemetry collector.

Collects disk partition capacity, I/O rates, latency, utilization,
and NVMe/SSD health indicators where available.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.collectors.storage")


@dataclass
class PartitionStat:
    mount: str
    device: str
    fstype: str
    total_gb: float
    free_gb: float
    used_gb: float
    used_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "mount": self.mount,
            "device": self.device,
            "fstype": self.fstype,
            "total_gb": self.total_gb,
            "free_gb": self.free_gb,
            "used_gb": self.used_gb,
            "used_pct": self.used_pct,
        }


class StorageCollector:
    """Monitors storage capacity, I/O latency, and SMART indicators."""

    def __init__(self):
        self._prev_io: tuple[float, Any] | None = None
        self._has_smartctl = shutil.which("smartctl") is not None

    def sample(self) -> dict[str, Any]:
        """Collect storage telemetry."""
        partitions = self._sample_partitions()
        io_metrics = self._sample_io_latency()
        smart_data = self._sample_smart_health()

        return {
            "partitions": [p.as_dict() for p in partitions],
            "io": io_metrics,
            "smart": smart_data,
            "collected_at": utcnow(),
        }

    def _sample_partitions(self) -> list[PartitionStat]:
        stats: list[PartitionStat] = []
        for part in psutil.disk_partitions(all=False):
            # Skip virtual/pseudo filesystems and read-only mounted loop/tool images
            opts = part.opts.split(",")
            if "ro" in opts or any(p in part.opts for p in ("loop", "ramfs", "tmpfs", "fuse", "squashfs")):
                continue
            if part.mountpoint.startswith(("/usr/lib/linux-tools", "/snap")):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                stats.append(PartitionStat(
                    mount=part.mountpoint,
                    device=part.device,
                    fstype=part.fstype,
                    total_gb=round(usage.total / (1024**3), 2),
                    free_gb=round(usage.free / (1024**3), 2),
                    used_gb=round(usage.used / (1024**3), 2),
                    used_pct=round(usage.percent, 1),
                ))
            except (OSError, PermissionError):
                continue
        return stats

    def _sample_io_latency(self) -> dict[str, Any]:
        now = time.time()
        try:
            curr_io = psutil.disk_io_counters()
        except (RuntimeError, PermissionError):
            return {"read_bytes_s": 0.0, "write_bytes_s": 0.0, "read_latency_ms": 0.0, "write_latency_ms": 0.0}

        if not curr_io:
            return {"read_bytes_s": 0.0, "write_bytes_s": 0.0, "read_latency_ms": 0.0, "write_latency_ms": 0.0}

        rates: dict[str, Any] = {
            "read_bytes_s": 0.0,
            "write_bytes_s": 0.0,
            "read_iops": 0.0,
            "write_iops": 0.0,
            "read_latency_ms": 0.0,
            "write_latency_ms": 0.0,
            "busy_pct": 0.0,
        }

        if self._prev_io:
            prev_ts, prev_io = self._prev_io
            dt = now - prev_ts
            if dt > 0:
                d_rbytes = curr_io.read_bytes - prev_io.read_bytes
                d_wbytes = curr_io.write_bytes - prev_io.write_bytes
                d_rcount = curr_io.read_count - prev_io.read_count
                d_wcount = curr_io.write_count - prev_io.write_count
                d_rtime = curr_io.read_time - prev_io.read_time
                d_wtime = curr_io.write_time - prev_io.write_time
                d_busy = getattr(curr_io, "busy_time", 0) - getattr(prev_io, "busy_time", 0)

                rates["read_bytes_s"] = round(d_rbytes / dt, 1)
                rates["write_bytes_s"] = round(d_wbytes / dt, 1)
                rates["read_iops"] = round(d_rcount / dt, 1)
                rates["write_iops"] = round(d_wcount / dt, 1)

                if d_rcount > 0:
                    rates["read_latency_ms"] = round(d_rtime / d_rcount, 2)
                if d_wcount > 0:
                    rates["write_latency_ms"] = round(d_wtime / d_wcount, 2)
                if dt > 0 and d_busy > 0:
                    # busy_time is in milliseconds
                    rates["busy_pct"] = min(100.0, round((d_busy / (dt * 1000.0)) * 100.0, 1))

        self._prev_io = (now, curr_io)
        return rates

    def _sample_smart_health(self) -> dict[str, Any]:
        """Reads NVMe or smartctl health indicators."""
        # Check native Linux /sys/class/nvme
        nvme_dir = Path("/sys/class/nvme")
        if nvme_dir.exists():
            controllers = list(nvme_dir.glob("nvme*"))
            if controllers:
                nvme_devices = []
                for c in controllers:
                    model = (c / "model").read_text().strip() if (c / "model").exists() else c.name
                    firmware = (c / "firmware_rev").read_text().strip() if (c / "firmware_rev").exists() else None
                    state = (c / "state").read_text().strip() if (c / "state").exists() else "live"
                    nvme_devices.append({
                        "device": c.name,
                        "model": model,
                        "firmware": firmware,
                        "state": state,
                        "healthy": state in ("live", "running", "active"),
                    })
                return {
                    "available": True,
                    "source": "sysfs_nvme",
                    "devices": nvme_devices,
                }

        if self._has_smartctl:
            return {
                "available": True,
                "source": "smartctl",
                "devices": [],
                "note": "smartctl present; privileged scan required",
            }

        return {
            "available": False,
            "source": "none",
            "reason": "NVMe sysfs controllers and smartctl not accessible on host",
            "devices": [],
        }
