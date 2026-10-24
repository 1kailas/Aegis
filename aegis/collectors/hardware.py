"""CPU, memory, disk, network and general host telemetry via psutil.

All functions are synchronous and fast enough for >=1s sampling loops.
The monitoring engine runs them in its own thread, never on the API
event loop.
"""

from __future__ import annotations

import platform
import socket
import time
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass
class DiskStat:
    mount: str
    total_gb: float
    free_gb: float
    used_pct: float


@dataclass
class IoRates:
    read_bytes_s: float | None = None
    write_bytes_s: float | None = None
    sent_bytes_s: float | None = None
    recv_bytes_s: float | None = None


@dataclass
class HostIdentity:
    host_id: str
    hostname: str
    platform_str: str
    kernel: str
    cpu_model: str | None = None
    cpu_cores: int | None = None
    total_memory_mb: int | None = None
    product: str | None = None


def host_identity() -> HostIdentity:
    ident = HostIdentity(
        host_id=socket.gethostname(),
        hostname=socket.gethostname(),
        platform_str=platform.platform(),
        kernel=platform.release(),
        cpu_cores=psutil.cpu_count(logical=True),
        total_memory_mb=int(psutil.virtual_memory().total // (1024 * 1024)),
        product=_machine_product(),
    )
    ident.cpu_model = _cpu_model()
    return ident


def _machine_product() -> str | None:
    try:
        with open("/sys/devices/virtual/dmi/id/product_name") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _cpu_model() -> str | None:
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


class HardwareCollector:
    """Samples host metrics; accumulates IO rates between successive calls."""

    def __init__(self):
        self._prev_disk: tuple[float, tuple[float, float]] | None = None
        self._prev_net: tuple[float, tuple[float, float]] | None = None

    def sample_cpu_mem(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        cpu = psutil.cpu_percent(interval=None)
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        return {
            "cpu_percent": cpu,
            "cpu_percent_per_core": per_cpu,
            "load_avg_1": psutil.getloadavg()[0],
            "memory": {
                "total_mb": round(vm.total / 1048576),
                "used_mb": round(vm.used / 1048576),
                "available_mb": round(vm.available / 1048576),
                "cached_mb": round(getattr(vm, "cached", 0) / 1048576),
                "percent": vm.percent,
            },
            "swap": {
                "total_mb": round(sw.total / 1048576),
                "used_mb": round(sw.used / 1048576),
                "percent": sw.percent,
            },
        }

    def sample_io_rates(self) -> IoRates:
        now = time.time()
        rates = IoRates()
        try:
            dio = psutil.disk_io_counters()
            if dio:
                rd, wr = dio.read_bytes, dio.write_bytes
                if self._prev_disk and now > self._prev_disk[0]:
                    dt = now - self._prev_disk[0]
                    rates.read_bytes_s = (rd - self._prev_disk[1][0]) / dt
                    rates.write_bytes_s = (wr - self._prev_disk[1][1]) / dt
                self._prev_disk = (now, (rd, wr))
        except (RuntimeError, PermissionError):
            pass
        nio = psutil.net_io_counters()
        if nio:
            snd, rcv = nio.bytes_sent, nio.bytes_recv
            if self._prev_net and now > self._prev_net[0]:
                dt = now - self._prev_net[0]
                rates.sent_bytes_s = (snd - self._prev_net[1][0]) / dt
                rates.recv_bytes_s = (rcv - self._prev_net[1][1]) / dt
            self._prev_net = (now, (snd, rcv))
        return rates

    def sample_disks(self) -> list[DiskStat]:
        out: list[DiskStat] = []
        for part in psutil.disk_partitions(all=False):
            # Skip virtual/ephemeral mounts; keep real filesystems only.
            if any(p in part.opts for p in ("loop", "ramfs", "tmpfs", "fuse")):
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except (OSError, PermissionError):
                continue
            out.append(
                DiskStat(
                    mount=part.mountpoint,
                    total_gb=round(u.total / 2**30, 1),
                    free_gb=round(u.free / 2**30, 1),
                    used_pct=round(u.percent, 1),
                )
            )
        return out

    def uptime_s(self) -> float:
        return max(0.0, time.time() - psutil.boot_time())
