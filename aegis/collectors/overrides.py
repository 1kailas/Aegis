"""Simulated sensor overrides (fault injection).

This mechanism is strictly separate from real hardware telemetry:
* Overrides are always labelled SIMULATED_OVERRIDE.
* They are stored only in this process and never written to /sys or any
  hardware-adjacent location.
* They exist so dashboards, demos and tests can exercise deterministic
  thermal behaviour without ever stressing real hardware.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from aegis.collectors.sensor_registry import Reading, Sensor, Validity

# Virtual sensor exposed when overrides inject values on hosts that have
# no real temperature sensors (the guardian watches it during demos).
VIRTUAL_CPU_SENSOR_ID = "override:cpu-package"


@dataclass
class OverrideEntry:
    sensor_id: str
    value_c: float
    note: str
    injected_at: float
    expires_at: float | None


class OverrideStore:
    """Thread-safe store of simulated temperature overrides."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[str, OverrideEntry] = {}

    def inject(self, sensor_id: str, value_c: float, note: str = "",
               ttl_s: float | None = None) -> OverrideEntry:
        if not 0 < value_c < 200:
            raise ValueError(
                f"Override temperature {value_c}degC out of plausible range 0-200"
            )
        entry = OverrideEntry(
            sensor_id=sensor_id, value_c=value_c, note=note,
            injected_at=time.time(),
            expires_at=(time.time() + ttl_s) if ttl_s else None,
        )
        with self._lock:
            self._entries[sensor_id] = entry
        return entry

    def clear(self, sensor_id: str | None = None) -> int:
        with self._lock:
            if sensor_id is None:
                n = len(self._entries)
                self._entries.clear()
                return n
            return 1 if self._entries.pop(sensor_id, None) else 0

    def sweep_expired(self) -> None:
        now = time.time()
        with self._lock:
            for sid in [
                s for s, e in self._entries.items()
                if e.expires_at is not None and e.expires_at < now
            ]:
                self._entries.pop(sid)

    def active(self) -> dict[str, OverrideEntry]:
        self.sweep_expired()
        with self._lock:
            return dict(self._entries)

    # -- application -----------------------------------------------------
    def apply(self, readings: list[Reading]) -> list[Reading]:
        """Merge overrides over real readings; synthesise virtual ones."""
        self.sweep_expired()
        with self._lock:
            entries = list(self._entries.values())
        out = list(readings)
        for e in entries:
            if e.sensor_id.startswith("override:"):
                out.append(_override_reading(e))
                continue
            replaced = False
            for i, r in enumerate(out):
                if (r.sensor.sensor_id == e.sensor_id
                        and r.sensor.kind == "temperature"):
                    out[i] = _override_reading(e, sensor=r.sensor)
                    replaced = True
            if not replaced:
                out.append(_override_reading(e))
        return out

    def virtual_sensors(self) -> list[Sensor]:
        with self._lock:
            have = [s for s in self._entries if s.startswith("override:")]
        return [
            Sensor(sensor_id=s, kind="temperature",
                   label=s.split(":", 1)[1], source="override",
                   source_path=None, unit="degC")
            for s in have
        ]


def _override_reading(entry: OverrideEntry, sensor: Sensor | None = None) -> Reading:
    sensor = sensor or Sensor(
        sensor_id=entry.sensor_id,
        kind="temperature",
        label=entry.sensor_id.split(":", 1)[1],
        source="override",
        source_path=None,
        unit="degC",
    )
    return Reading(
        sensor=sensor,
        host_id=entry.sensor_id,
        ts=entry.injected_at,
        value=entry.value_c,
        validity=Validity.SIMULATED_OVERRIDE,
        note=entry.note or "simulated override",
    )
