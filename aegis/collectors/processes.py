"""Process-level resource consumption collector.

Captures top resource-consuming processes by CPU utilization and memory usage
for root-cause analysis and incident correlation.
"""

from __future__ import annotations

import logging
from typing import Any

import psutil

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.collectors.processes")


class ProcessCollector:
    """Collects process metrics across the host."""

    def __init__(self, top_n: int = 10):
        self.top_n = top_n

    def sample(self) -> dict[str, Any]:
        """Collect top processes by CPU and memory."""
        procs: list[dict[str, Any]] = []
        for p in psutil.process_iter(
            ["pid", "name", "username", "cpu_percent", "memory_percent", "memory_info", "status"]
        ):
            try:
                info = p.info
                mem_info = info.get("memory_info")
                rss_mb = round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0.0
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"] or "unknown",
                    "username": info.get("username") or "",
                    "cpu_percent": round(info.get("cpu_percent") or 0.0, 1),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 1),
                    "memory_rss_mb": rss_mb,
                    "status": info.get("status") or "unknown",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as e:
                _LOG.debug("Error sampling process: %s", e)
                continue

        by_cpu = sorted(procs, key=lambda x: x["cpu_percent"], reverse=True)[: self.top_n]
        by_memory = sorted(procs, key=lambda x: x["memory_rss_mb"], reverse=True)[: self.top_n]

        return {
            "top_cpu": by_cpu,
            "top_memory": by_memory,
            "total_processes": len(procs),
            "collected_at": utcnow(),
        }
