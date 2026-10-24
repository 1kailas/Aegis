"""Linux hardware sensor discovery.

Sources, in priority order:
* /sys/class/hwmon  - native kernel hardware monitor interface
* /sys/class/nvme   - NVMe controller temperatures (via bundled hwmon)
* psutil.sensors_*  - cross-platform fallback that also reads /sys/class/thermal

Every reading carries a validity state. A missing sensor is never
interpreted as a safe condition: it surfaces as UNAVAILABLE with a reason.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

HWMON_DIR = Path("/sys/class/hwmon")
NVME_DIR = Path("/sys/class/nvme")


class Validity(str, Enum):
    VALID = "VALID"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    ERROR = "ERROR"
    SIMULATED_OVERRIDE = "SIMULATED_OVERRIDE"


@dataclass(frozen=True)
class Sensor:
    sensor_id: str
    kind: str              # temperature | fan | voltage | power
    label: str
    source: str            # hwmon | psutil | nvme
    source_path: str | None
    unit: str | None


@dataclass
class Reading:
    sensor: Sensor
    host_id: str
    ts: float
    value: float | None
    validity: Validity
    error: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor.sensor_id,
            "kind": self.sensor.kind,
            "label": self.sensor.label,
            "source": self.sensor.source,
            "unit": self.sensor.unit,
            "ts": self.ts,
            "value": self.value,
            "validity": self.validity.value,
            "error": self.error,
            "note": self.note,
        }


# Convenience alias used by callers that return plain dicts.
SensorReading = Reading


def upscale_temperature(value: float) -> float:
    """Some drivers report millidegrees; normalize anything above 1000."""
    return value / 1000.0 if value >= 1000 else value


def _read_text(chip_dir: Path, name: str) -> str | None:
    try:
        return (chip_dir / name).read_text().strip()
    except OSError:
        return None


class SensorRegistry:
    """Discovers sensors once and re-reads values on demand."""

    def __init__(self, host_id: str):
        self.host_id = host_id
        self._sensors: dict[str, Sensor] = {}
        self._discover()

    # -- discovery --------------------------------------------------------
    def refresh(self) -> None:
        self._discover()

    def _discover(self) -> None:
        self._sensors.clear()
        self._discover_hwmon()
        self._discover_nvme()
        self._discover_psutil_fallback()

    def _discover_hwmon(self) -> None:
        for chip_dir in sorted(HWMON_DIR.glob("hwmon*")):
            name = _read_text(chip_dir, "name") or chip_dir.name
            for entry in sorted(chip_dir.iterdir()):
                if not entry.name.endswith("_input"):
                    continue
                stem = entry.name[: -len("_input")]
                label = _read_text(chip_dir, f"{stem}_label") or f"{name} {stem}"
                if stem.startswith("temp"):
                    kind, unit = "temperature", "degC"
                elif stem.startswith("fan"):
                    kind, unit = "fan", "rpm"
                elif stem.startswith("in"):
                    kind, unit = "voltage", "V"
                elif stem.startswith("power") or stem.startswith("curr"):
                    kind, unit = "power", "W"
                else:
                    continue
                sid = f"hwmon:{name}.{entry.name}"
                self._sensors.setdefault(
                    sid,
                    Sensor(sensor_id=sid, kind=kind, label=label, source="hwmon",
                           source_path=str(entry), unit=unit),
                )

    def _discover_nvme(self) -> None:
        for nvm in sorted(NVME_DIR.glob("nvme*")):
            hw = next(nvm.glob("device/hwmon*"), None) or next(nvm.glob("hwmon*"), None)
            if hw is None:
                continue
            for entry in sorted(hw.glob("temp?_input")):
                sid = f"nvme:{nvm.name}.{entry.parent.name}.{entry.name}"
                self._sensors.setdefault(
                    sid,
                    Sensor(sensor_id=sid, kind="temperature",
                           label=f"{nvm.name} composite", source="nvme",
                           source_path=str(entry), unit="degC"),
                )

    def _discover_psutil_fallback(self) -> None:
        try:
            import psutil

            for chip, entries in (psutil.sensors_temperatures() or {}).items():
                for i, e in enumerate(entries):
                    label = e.label or f"{chip}{i}"
                    sid = f"psutil:{chip}.{label}"
                    if sid in self._sensors or _hwmon_dup(chip, label, self._sensors):
                        continue  # hwmon already exposes this sensor
                    self._sensors[sid] = Sensor(
                        sensor_id=sid, kind="temperature", label=label,
                        source="psutil", source_path=None, unit="degC")
            for chip, entries in (psutil.sensors_fans() or {}).items():
                for i, e in enumerate(entries):
                    label = e.label or f"{chip}{i}"
                    sid = f"psutil_fan:{chip}.{label}"
                    if sid in self._sensors or _hwmon_dup(chip, label, self._sensors):
                        continue
                    self._sensors[sid] = Sensor(
                        sensor_id=sid, kind="fan", label=label, source="psutil",
                        source_path=None, unit="rpm")
        except (OSError, ValueError, ImportError):
            pass

    # -- queries ----------------------------------------------------------
    def all_sensors(self) -> list[Sensor]:
        return list(self._sensors.values())

    def sensors_by_kind(self, kind: str) -> list[Sensor]:
        return [s for s in self._sensors.values() if s.kind == kind]

    def get_sensor(self, sensor_id: str) -> Sensor | None:
        return self._sensors.get(sensor_id)

    # -- reading ----------------------------------------------------------
    def read(self, sensor: Sensor) -> Reading:
        if sensor.source in {"hwmon", "nvme"} and sensor.source_path:
            return self._read_sysfs(sensor)
        if sensor.source == "psutil":
            return self._read_psutil(sensor)
        return Reading(sensor=sensor, host_id=self.host_id, ts=time.time(),
                       value=None, validity=Validity.UNAVAILABLE,
                       error=f"unsupported source '{sensor.source}'")

    def read_all(self) -> list[Reading]:
        return [self.read(s) for s in self._sensors.values()]

    # -- reader internals ----------------------------------------------------
    def _read_sysfs(self, sensor: Sensor) -> Reading:
        assert sensor.source_path
        path = Path(sensor.source_path)
        try:
            raw = path.read_text().strip()
            value = float(raw)
        except FileNotFoundError:
            return _reading(sensor, self.host_id, None, Validity.UNAVAILABLE,
                            "sysfs input missing")
        except ValueError as exc:
            return _reading(sensor, self.host_id, None, Validity.ERROR,
                            f"unparsable value: {exc}")
        except OSError as exc:
            return _reading(sensor, self.host_id, None, Validity.ERROR, str(exc))

        if sensor.kind == "temperature":
            value = upscale_temperature(value)

        validity = Validity.VALID
        try:
            age = time.time() - path.stat().st_mtime
            if age > 120 and sensor.kind == "temperature":
                validity = Validity.STALE
        except OSError:
            pass
        return _reading(sensor, self.host_id, value, validity)

    def _read_psutil(self, sensor: Sensor) -> Reading:
        import psutil

        # sid format: psutil:<chip>.<label-item>
        chip, item = sensor.sensor_id.split(":", 1)[1].split(".", 1)
        try:
            if sensor.kind == "temperature":
                entries = (psutil.sensors_temperatures() or {}).get(chip, [])
            elif sensor.kind == "fan":
                entries = (psutil.sensors_fans() or {}).get(chip, [])
            else:
                entries = []
        except (OSError, ValueError) as exc:
            return _reading(sensor, self.host_id, None, Validity.ERROR, str(exc))

        match = [e for e in entries if (e.label or "") == item]
        if not match:
            return _reading(sensor, self.host_id, None, Validity.UNAVAILABLE,
                            "sensor disappeared from source")
        value = getattr(match[0], "current", None)
        if value is None:
            return _reading(sensor, self.host_id, None, Validity.UNAVAILABLE,
                            "no reading available")
        return _reading(sensor, self.host_id, float(value), Validity.VALID)


def _hwmon_dup(chip: str, label: str, known: dict[str, Sensor]) -> bool:
    chip_l, label_l = chip.lower(), label.lower()
    for s in known.values():
        if (s.source == "hwmon" and chip_l in s.sensor_id.lower()
                and label_l in s.label.lower()):
            return True
    return False


def _reading(sensor: Sensor, host_id: str, value: float | None,
             validity: Validity, error: str | None = None) -> Reading:
    return Reading(sensor=sensor, host_id=host_id, ts=time.time(),
                   value=value, validity=validity, error=error)
