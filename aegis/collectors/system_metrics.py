"""Full system snapshot per monitoring tick: cpu, memory, disks, IO and
temperatures. Demo overrides are merged upstream so a single snapshot is
the complete truth one dashboard poll needs."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from aegis.collectors.hardware import HardwareCollector
from aegis.collectors.sensor_registry import SensorRegistry, Validity


class MetricsCollector:
    def __init__(
        self,
        hardware: HardwareCollector,
        registry: SensorRegistry,
        host_id: str,
        history_points_max: int = 900,
    ):
        self.hw = hardware
        self.registry = registry
        self.host_id = host_id
        self._history: deque[tuple[float, dict[str, Any], str]] = deque(
            maxlen=history_points_max
        )

    def collect(self) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        """Returns (snapshot, validity, temperature readings)."""
        snap = self.hw.sample_cpu_mem()
        io = self.hw.sample_io_rates()
        snap["disk_io"] = {
            "read_bytes_s": io.read_bytes_s,
            "write_bytes_s": io.write_bytes_s,
        }
        snap["net_io"] = {
            "sent_bytes_s": io.sent_bytes_s,
            "recv_bytes_s": io.recv_bytes_s,
        }
        snap["disks"] = [d.__dict__ for d in self.hw.sample_disks()]
        snap["uptime_s"] = self.hw.uptime_s()

        temps = [r.as_dict() for r in self.registry.read_all()
                 if r.sensor.kind == "temperature"]
        return snap, overall_validity(temps), temps

    def record(
        self,
        snap: dict[str, Any],
        validity: str,
        temps: list[dict[str, Any]],
    ) -> None:
        self._history.append((time.time(), snap, validity))

    def latest(self) -> dict[str, Any] | None:
        """Returns the most recent snapshot or collects a fresh one."""
        if self._history:
            return self._history[-1][1]
        snap, _, _ = self.collect()
        return snap

    def history(self, points: int | None = None) -> list[tuple]:
        items = list(self._history)
        return items[-points:] if points else items


def overall_validity(temps: list[dict[str, Any]]) -> str:
    """Classify a merged reading set into one sample validity string."""
    validities = {r["validity"] for r in temps}
    if not temps or validities == {"UNAVAILABLE"}:
        return "PARTIAL_NO_SENSORS"
    if "ERROR" in validities:
        return "PARTIAL_SENSOR_ERROR"
    if "STALE" in validities:
        return "PARTIAL_STALE_SENSOR"
    return "VALID"
