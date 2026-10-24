"""Hardware and software inventory collector.

Discovers system specifications, hardware layout, OS distribution, kernel,
and installed runtime components.
"""

from __future__ import annotations

import logging
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.collectors.inventory")


class InventoryCollector:
    """Gathers comprehensive hardware and software inventory."""

    def __init__(self, host_id: str):
        self.host_id = host_id

    def sample(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "hostname": socket.gethostname(),
            "collected_at": utcnow(),
            "hardware": self._hardware_info(),
            "software": self._software_info(),
        }

    def _hardware_info(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        freq = psutil.cpu_freq()

        interfaces: dict[str, Any] = {}
        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            for iface, addr_list in addrs.items():
                if_stat = stats.get(iface)
                interfaces[iface] = {
                    "is_up": if_stat.isup if if_stat else False,
                    "speed_mbps": if_stat.speed if if_stat else 0,
                    "mtu": if_stat.mtu if if_stat else 0,
                    "addresses": [
                        {"family": str(a.family), "address": a.address, "netmask": a.netmask}
                        for a in addr_list
                    ],
                }
        except Exception as e:
            _LOG.debug("Interface discovery error: %s", e)

        return {
            "cpu": {
                "model": self._cpu_model(),
                "physical_cores": psutil.cpu_count(logical=False),
                "logical_cores": psutil.cpu_count(logical=True),
                "freq_current_mhz": round(freq.current, 1) if freq else None,
                "freq_min_mhz": round(freq.min, 1) if freq and freq.min else None,
                "freq_max_mhz": round(freq.max, 1) if freq and freq.max else None,
            },
            "memory": {
                "total_mb": round(vm.total / (1024 * 1024)),
                "swap_total_mb": round(sw.total / (1024 * 1024)),
            },
            "network_interfaces": interfaces,
        }

    def _software_info(self) -> dict[str, Any]:
        os_release = {}
        os_release_path = Path("/etc/os-release")
        if os_release_path.exists():
            try:
                for line in os_release_path.read_text().splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os_release[k.strip()] = v.strip().strip('"')
            except Exception:
                pass

        boot_time = psutil.boot_time()
        uptime_s = round(max(0.0, time.time() - boot_time), 1)

        docker_info = self._check_docker()

        return {
            "os_name": os_release.get("PRETTY_NAME", platform.platform()),
            "platform": platform.platform(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "python_version": sys.version.split()[0],
            "uptime_s": uptime_s,
            "boot_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(boot_time)),
            "docker": docker_info,
        }

    def _cpu_model(self) -> str | None:
        try:
            with open("/proc/cpuinfo") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return None

    def _check_docker(self) -> dict[str, Any]:
        try:
            import docker
            client = docker.from_env(timeout=2)
            version = client.version()
            return {
                "available": True,
                "version": version.get("Version"),
                "api_version": version.get("ApiVersion"),
            }
        except Exception as e:
            return {
                "available": False,
                "reason": str(e),
            }
