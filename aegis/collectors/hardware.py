"""CPU, memory, disk, network, PSI, and general host telemetry.

All functions are synchronous and fast enough for >=1s sampling loops.
The monitoring engine runs them in its own thread, never on the API
event loop.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

_LOG = logging.getLogger("aegis.collectors.hardware")


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
    read_latency_ms: float | None = None
    write_latency_ms: float | None = None
    busy_pct: float | None = None
    errin_s: float | None = None
    errout_s: float | None = None
    dropin_s: float | None = None
    dropout_s: float | None = None


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


def _read_psi(path: str) -> dict[str, float] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        out: dict[str, float] = {}
        for line in p.read_text().splitlines():
            # e.g.: some avg10=0.00 avg60=0.00 avg300=0.00 total=0
            parts = line.split()
            if not parts:
                continue
            prefix = parts[0]  # some or full
            for token in parts[1:]:
                if "=" in token:
                    k, v = token.split("=", 1)
                    try:
                        out[f"{prefix}_{k}"] = float(v)
                    except ValueError:
                        pass
        return out
    except Exception:
        return None


def _check_thermal_throttling() -> dict[str, Any]:
    """Inspects Linux kernel cooling devices and sysfs throttle counts."""
    throttling = False
    details: list[dict[str, Any]] = []

    thermal_dir = Path("/sys/class/thermal")
    if thermal_dir.exists():
        for dev in thermal_dir.glob("cooling_device*"):
            try:
                c_type = (dev / "type").read_text().strip() if (dev / "type").exists() else "unknown"
                cur_state_str = (dev / "cur_state").read_text().strip() if (dev / "cur_state").exists() else "0"
                max_state_str = (dev / "max_state").read_text().strip() if (dev / "max_state").exists() else "0"
                cur_state = int(cur_state_str)
                max_state = int(max_state_str)
                if cur_state > 0:
                    throttling = True
                details.append({
                    "device": dev.name,
                    "type": c_type,
                    "cur_state": cur_state,
                    "max_state": max_state,
                    "throttling": cur_state > 0,
                })
            except Exception:
                continue

    # Also check CPU package throttle counters if available
    cpu0_throttle = Path("/sys/devices/system/cpu/cpu0/thermal_throttle/package_throttle_count")
    package_throttles = None
    if cpu0_throttle.exists():
        try:
            package_throttles = int(cpu0_throttle.read_text().strip())
            if package_throttles > 0:
                throttling = True
        except Exception:
            pass

    return {
        "throttling": throttling,
        "cooling_devices": details,
        "package_throttle_count": package_throttles,
    }


class HardwareCollector:
    """Samples host metrics; accumulates IO rates between successive calls."""

    def __init__(self):
        self._prev_disk: tuple[float, tuple[int, int, int, int, int, int, int]] | None = None
        self._prev_net: tuple[float, tuple[int, int, int, int, int, int]] | None = None

    def sample_cpu_mem(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        cpu = psutil.cpu_percent(interval=None)
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        load_avg = psutil.getloadavg()
        freq = psutil.cpu_freq()

        freq_info = {
            "current_mhz": round(freq.current, 1) if freq else None,
            "min_mhz": round(freq.min, 1) if freq and freq.min else None,
            "max_mhz": round(freq.max, 1) if freq and freq.max else None,
        }

        psi_memory = _read_psi("/proc/pressure/memory")
        psi_cpu = _read_psi("/proc/pressure/cpu")
        psi_io = _read_psi("/proc/pressure/io")
        throttling = _check_thermal_throttling()

        return {
            "cpu_percent": cpu,
            "cpu_percent_per_core": per_cpu,
            "cpu_freq": freq_info,
            "load_avg_1": round(load_avg[0], 2),
            "load_avg_5": round(load_avg[1], 2),
            "load_avg_15": round(load_avg[2], 2),
            "throttling": throttling,
            "pressure": {
                "memory": psi_memory,
                "cpu": psi_cpu,
                "io": psi_io,
            },
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
                rcnt, wcnt = dio.read_count, dio.write_count
                rtime, wtime = dio.read_time, dio.write_time
                busy = getattr(dio, "busy_time", 0)

                if self._prev_disk and now > self._prev_disk[0]:
                    dt = now - self._prev_disk[0]
                    p_rd, p_wr, p_rcnt, p_wcnt, p_rtime, p_wtime, p_busy = self._prev_disk[1]
                    if dt > 0:
                        rates.read_bytes_s = round((rd - p_rd) / dt, 1)
                        rates.write_bytes_s = round((wr - p_wr) / dt, 1)
                        d_rcnt = rcnt - p_rcnt
                        d_wcnt = wcnt - p_wcnt
                        if d_rcnt > 0:
                            rates.read_latency_ms = round((rtime - p_rtime) / d_rcnt, 2)
                        if d_wcnt > 0:
                            rates.write_latency_ms = round((wtime - p_wtime) / d_wcnt, 2)
                        d_busy = busy - p_busy
                        if d_busy > 0:
                            rates.busy_pct = min(100.0, round((d_busy / (dt * 1000.0)) * 100.0, 1))

                self._prev_disk = (now, (rd, wr, rcnt, wcnt, rtime, wtime, busy))
        except (RuntimeError, PermissionError):
            pass

        try:
            nio = psutil.net_io_counters()
            if nio:
                snd, rcv = nio.bytes_sent, nio.bytes_recv
                ein, eout = nio.errin, nio.errout
                din, dout = nio.dropin, nio.dropout

                if self._prev_net and now > self._prev_net[0]:
                    dt = now - self._prev_net[0]
                    p_snd, p_rcv, p_ein, p_eout, p_din, p_dout = self._prev_net[1]
                    if dt > 0:
                        rates.sent_bytes_s = round((snd - p_snd) / dt, 1)
                        rates.recv_bytes_s = round((rcv - p_rcv) / dt, 1)
                        rates.errin_s = round((ein - p_ein) / dt, 2)
                        rates.errout_s = round((eout - p_eout) / dt, 2)
                        rates.dropin_s = round((din - p_din) / dt, 2)
                        rates.dropout_s = round((dout - p_dout) / dt, 2)

                self._prev_net = (now, (snd, rcv, ein, eout, din, dout))
        except (RuntimeError, PermissionError):
            pass

        return rates

    def sample_disks(self) -> list[DiskStat]:
        out: list[DiskStat] = []
        for part in psutil.disk_partitions(all=False):
            opts = part.opts.split(",")
            if "ro" in opts or any(p in part.opts for p in ("loop", "ramfs", "tmpfs", "fuse", "squashfs")):
                continue
            if part.mountpoint.startswith(("/usr/lib/linux-tools", "/snap")):
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
