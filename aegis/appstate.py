"""Application context: shared wiring for engine, API and CLI.

Holds runtime flags (mode, emergency stop, sampling boost), the
collectors, the thermal guardian, the policy executor and the incident
store. Settings changed at runtime are persisted to app_state so a
restart retains them.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from aegis.collectors.gpu import GpuCollector
from aegis.collectors.hardware import HardwareCollector, host_identity
from aegis.collectors.inventory import InventoryCollector
from aegis.collectors.overrides import OverrideStore
from aegis.collectors.processes import ProcessCollector
from aegis.collectors.sensor_registry import SensorRegistry
from aegis.collectors.storage import StorageCollector
from aegis.collectors.system_metrics import MetricsCollector
from aegis.config import AppConfig
from aegis.control_plane.policy import PolicyExecutor
from aegis.control_plane.resource_guardian import ResourceGuardian, ResourcePolicy
from aegis.control_plane.thermal_guardian import ThermalGuardian
from aegis.db import Database, utcnow
from aegis.events import EventBus
from aegis.incidents import IncidentStore
from aegis.logging_config import setup_logging

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from aegis.engine import MonitoringEngine

_LOG = logging.getLogger("aegis.state")


class RuntimeFlags:
    """Thread-safe runtime toggles consulted by policy decisions."""

    guardian_state: str = "NORMAL"

    def __init__(self, config: AppConfig, db: Database):
        self.host_id = host_identity().host_id
        self._lock = threading.Lock()
        self._mode = db.get_state("mode", config.monitoring.mode)
        self._emergency_stop = db.get_state("emergency_stop", "false") == "true"
        self._sampling_boosted = False
        self.backpressure = False
        self.active_incident_id: str | None = None
        self.monitoring = config.monitoring

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def set_mode(self, value: str) -> None:
        if value not in ("monitoring_only", "policy_autonomous"):
            raise ValueError(f"Invalid monitoring mode '{value}'")
        with self._lock:
            self._mode = value

    @property
    def emergency_stop(self) -> bool:
        with self._lock:
            return self._emergency_stop

    def set_emergency_stop(self, value: bool) -> None:
        with self._lock:
            self._emergency_stop = bool(value)

    @property
    def sampling_boosted(self) -> bool:
        with self._lock:
            return self._sampling_boosted

    @sampling_boosted.setter
    def sampling_boosted(self, value: bool) -> None:
        with self._lock:
            self._sampling_boosted = bool(value)

    @property
    def active_interval_s(self) -> float:
        """Interval the engine currently runs at (boosted on warning+)."""
        m = self.monitoring
        if not self._sampling_boosted:
            return m.collect_interval_s
        if self.guardian_state == "CRITICAL":
            return m.critical_interval_s
        return m.warning_interval_s


class AppContext:
    """One instance per process; passed to the API factory and the engine."""

    def __init__(self, config: AppConfig, started_at: float | None = None):
        self.config = config
        self.started_at = started_at or time.time()
        self.log = setup_logging(config.logging.dir, config.logging.level)

        self.db = Database(config.database.path)
        self.events = EventBus()
        self.overrides = OverrideStore()
        self.identity = host_identity()

        self.registry = SensorRegistry(self.identity.host_id)
        self.metrics = MetricsCollector(
            HardwareCollector(),
            self.registry,
            self.identity.host_id,
            history_points_max=config.monitoring.history_points_max,
        )
        self.gpu = GpuCollector()
        self.processes = ProcessCollector()
        self.storage = StorageCollector()
        self.inventory = InventoryCollector(self.identity.host_id)
        self.incidents = IncidentStore(self.db)

        self.flags = RuntimeFlags(config, self.db)
        self.guardian = ThermalGuardian(
            config.thermal_policy,
            self.identity.host_id,
            self.events,
            on_transition=self._record_transition,
        )
        r_pol = ResourcePolicy(
            memory_warning_pct=config.resource_policy.memory_warning_pct,
            memory_critical_pct=config.resource_policy.memory_critical_pct,
            memory_recovery_pct=config.resource_policy.memory_recovery_pct,
            disk_warning_pct=config.resource_policy.disk_warning_pct,
            disk_critical_pct=config.resource_policy.disk_critical_pct,
            disk_recovery_pct=config.resource_policy.disk_recovery_pct,
            cpu_warning_pct=config.resource_policy.cpu_warning_pct,
            cpu_critical_pct=config.resource_policy.cpu_critical_pct,
            cpu_recovery_pct=config.resource_policy.cpu_recovery_pct,
            recovery_hold_s=config.resource_policy.recovery_hold_s,
            cooldown_s=config.resource_policy.cooldown_s,
        )
        self.resource_guardian = ResourceGuardian(
            r_pol,
            self.events,
            on_transition=self._record_resource_transition,
        )
        self.policy = PolicyExecutor(
            config, self.db, self.events, self.incidents, self.flags
        )
        self.engine: MonitoringEngine | None = None  # set after construction

    # -- guardian bookkeeping ---------------------------------------------
    def _record_transition(self, ts_epoch: float, prev: str, new: str,
                           sensor_id: str | None, reason: str,
                           current: float | None) -> None:
        self.db.execute(
            "INSERT INTO thermal_transitions(ts, host_id, sensor_id,"
            " from_state, to_state, trigger_value_c, note)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)",
            (utcnow(), self.identity.host_id, sensor_id, prev, new,
             current, reason),
        )
        self.flags.guardian_state = new
        self.events.emit(
            "thermal",
            f"Thermal {prev} -> {new}: {reason}",
            "critical" if new == "CRITICAL"
            else "warning" if new == "WARNING" else "info",
            sensor_id=sensor_id, current_c=current, reason=reason,
        )

    def _record_resource_transition(self, ts_epoch: float, prev: str, new: str,
                                    reason: str, state_dict: dict) -> None:
        self.events.emit(
            "resource",
            f"Resource {prev} -> {new}: {reason}",
            "critical" if new == "CRITICAL"
            else "warning" if new == "WARNING" else "info",
            detail=state_dict,
        )
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail)"
            " VALUES(?,?,?,?,?)",
            (utcnow(), "resource_guardian", f"transition_{new}", "host_resources",
             str(state_dict)),
        )
        if new in ("WARNING", "CRITICAL"):
            active = self.incidents.active_by_category("resource_saturation")
            if not active:
                self.incidents.create(
                    category="resource_saturation",
                    severity="warning" if new == "WARNING" else "critical",
                    title="System resource threshold breach",
                    summary=reason,
                    host_id=self.identity.host_id,
                    evidence=[{"kind": "resource", "state": state_dict, "note": reason}],
                )
            else:
                self.incidents.append_evidence(
                    active["id"],
                    {"kind": "resource", "state": state_dict, "note": reason},
                )
        elif new == "NORMAL":
            active = self.incidents.active_by_category("resource_saturation")
            if active and active["state"] in ("DETECTED", "INVESTIGATING", "CONTAINED"):
                self.incidents.transition(
                    active["id"], "RESOLVED", "resource_guardian",
                    note="All resources returned within nominal bounds",
                )

    # -- persisted toggles ---------------------------------------------------
    def set_mode(self, value: str) -> None:
        self.flags.set_mode(value)
        self.db.set_state("mode", value)
        self.events.emit("policy", f"Monitoring mode set to {value}", "info")
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail)"
            " VALUES(?,?,?,?,?)",
            (utcnow(), "operator", "set_mode", value, "mode change via API"),
        )

    def set_emergency_stop(self, value: bool) -> None:
        self.flags.set_emergency_stop(value)
        self.db.set_state("emergency_stop", "true" if value else "false")
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail)"
            " VALUES(?,?,?,?,?)",
            (utcnow(), "operator", "emergency_stop",
             "true" if value else "false", "emergency stop toggled via API"),
        )
        if value:
            self.events.emit("policy", "EMERGENCY STOP engaged: autonomous"
                             " remediation is blocked until cleared",
                             "warning")

    # -- snapshot ------------------------------------------------------------
    def snapshot(self) -> dict:
        """Everything the dashboard overview needs in one consistent read."""
        history = self.metrics.history()
        guardian = self.guardian.state.as_dict()
        return {
            "host": {
                "host_id": self.identity.host_id,
                "hostname": self.identity.hostname,
                "platform": self.identity.platform_str,
                "kernel": self.identity.kernel,
                "cpu_model": self.identity.cpu_model,
                "cpu_cores": self.identity.cpu_cores,
                "total_memory_mb": self.identity.total_memory_mb,
                "product": self.identity.product,
            },
            "engine_running": bool(self.engine and self.engine.alive),
            "mode": self.flags.mode,
            "emergency_stop": self.flags.emergency_stop,
            "sampling_boosted": self.flags.sampling_boosted,
            "collect_interval": self.flags.active_interval_s,
            "db": {
                "path": str(self.config.database.path.resolve()),
                "integrity": self.db.db_integrity(),
            },
            "latest": history[-1][1] if history else None,
            "latest_validity": history[-1][2] if history else "NO_DATA",
            "thermal": guardian,
            "resources": self.resource_guardian.state.as_dict(),
            "gpu": self.gpu.sample(),
            "events": [e.__dict__ for e in self.events.latest(10)],
            "overrides_enabled": self.config.monitoring.simulated_overrides_enabled,
            "active_overrides": {
                s: {"value_c": e.value_c, "note": e.note}
                for s, e in self.overrides.active().items()
            },
        }

    def close(self) -> None:
        self.db.close()
