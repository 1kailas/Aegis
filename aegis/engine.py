"""Monitoring engine.

Runs in its own daemon thread so every blocking operation (sysfs reads,
psutil calls, SQLite writes, later Docker/Ollama probes) stays off the
FastAPI event loop. Each tick is defensive: a failing collector is
logged and recorded, and the loop continues.

Policy actions run on state TRANSITIONS only, so repeated ticks never
spam the action log; sampling boost follows the active level.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from aegis.collectors.sensor_registry import Validity
from aegis.collectors.system_metrics import overall_validity

_LOG = logging.getLogger("aegis.engine")

PRUNE_INTERVAL_S = 900.0
MAX_CONSECUTIVE_ERRORS = 5


class MonitoringEngine(threading.Thread):
    def __init__(self, ctx):  # ctx: aegis.appstate.AppContext
        super().__init__(name="aegis-engine", daemon=True)
        self.ctx = ctx
        self.alive = False
        self._stop_event = threading.Event()
        self._consecutive_errors = 0
        self._last_prune = time.time()
        self._last_sensor_sync = 0.0
        self._tick_count = 0

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        self.alive = True
        self.ctx.log.info("Monitoring engine started")
        self.ctx.events.emit("system", "Monitoring engine started", "info")
        self._register_host()
        try:
            while not self._stop_event.is_set():
                started = time.time()
                try:
                    self.tick()
                    self._consecutive_errors = 0
                except Exception:
                    self._consecutive_errors += 1
                    _LOG.exception("Monitoring tick failed")
                    self.ctx.events.emit(
                        "system", "Monitoring tick failed", "critical",
                        consecutive_errors=self._consecutive_errors)
                    if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        _LOG.critical("Too many consecutive tick failures;"
                                      " engine exiting")
                        break
                self._nap(started)
        finally:
            self.alive = False
            self.ctx.events.emit("system", "Monitoring engine stopped", "warning")
            self.ctx.log.info("Monitoring engine stopped")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self.join(timeout=timeout)

    def _nap(self, started: float) -> None:
        interval = self.ctx.flags.active_interval_s
        remaining = interval - (time.time() - started)
        if remaining > 0:
            self._stop_event.wait(remaining)

    # -- host & sensor inventory ------------------------------------------
    def _register_host(self) -> None:
        ident = self.ctx.identity
        now = time.time()
        self.ctx.db.execute(
            "INSERT INTO host_inventory(host_id, hostname, platform, kernel,"
            " cpu_model, cpu_cores, total_memory_mb, registered_at,"
            " last_seen_at) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(host_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,"
            " platform=excluded.platform, kernel=excluded.kernel,"
            " cpu_model=excluded.cpu_model",
            (ident.host_id, ident.hostname, ident.platform_str, ident.kernel,
             ident.cpu_model, ident.cpu_cores, ident.total_memory_mb, now, now),
        )

    def _sync_sensors(self, force: bool = False) -> None:
        if force or time.time() - self._last_sensor_sync \
                >= self.ctx.config.monitoring.sensor_snapshot_seconds:
            self.ctx.registry.refresh()
            self._last_sensor_sync = time.time()
            rows = [
                (s.sensor_id, self.ctx.identity.host_id, s.kind, s.label,
                 s.source, s.source_path, s.unit, _now_iso())
                for s in self.ctx.registry.all_sensors()
            ]
            with self.ctx.db.tx() as conn:
                for r in rows:
                    conn.execute(
                        "INSERT INTO sensor_registry(sensor_id, host_id, kind,"
                        " label, source, source_path, unit, discovered_at)"
                        " VALUES(?,?,?,?,?,?,?,?)"
                        " ON CONFLICT(sensor_id) DO NOTHING",
                        r,
                    )

    # -- the tick -----------------------------------------------------------
    def tick(self) -> dict[str, Any]:
        ctx = self.ctx
        self._sync_sensors()
        self._tick_count += 1

        snap, _validity, _temps = ctx.metrics.collect()
        temp_readings = [
            r.as_dict() for r in ctx.registry.read_all()
            if r.sensor.kind == "temperature"
        ]

        # Simulated overrides (demo only; clearly labelled per reading).
        simulated = False
        if ctx.config.monitoring.simulated_overrides_enabled:
            merged = ctx.overrides.apply(
                [r for r in ctx.registry.read_all()
                 if r.sensor.kind == "temperature"])
            simulated = any(r.validity == Validity.SIMULATED_OVERRIDE
                            for r in merged)
            temp_readings = [r.as_dict() for r in merged]

        snap["temperatures"] = temp_readings
        snap["validity"] = overall_validity(temp_readings)
        snap["simulated"] = simulated
        ctx.metrics.record(snap, str(snap["validity"]), temp_readings)

        # Bounded numeric telemetry history rows.
        rows = [
            ("cpu_percent", snap.get("cpu_percent"), "pct"),
            ("load_avg_1", snap.get("load_avg_1"), None),
            ("mem_percent", snap["memory"]["percent"], "pct"),
            ("mem_used_mb", snap["memory"]["used_mb"], "MB"),
            ("swap_percent", snap["swap"]["percent"], "pct"),
            ("net_recv_bps", (snap.get("net_io") or {}).get("recv_bytes_s"), "B/s"),
            ("net_sent_bps", (snap.get("net_io") or {}).get("sent_bytes_s"), "B/s"),
            ("disk_read_bps", (snap.get("disk_io") or {}).get("read_bytes_s"), "B/s"),
            ("disk_write_bps", (snap.get("disk_io") or {}).get("write_bytes_s"), "B/s"),
            ("disk_read_latency_ms", (snap.get("disk_io") or {}).get("read_latency_ms"), "ms"),
            ("disk_write_latency_ms", (snap.get("disk_io") or {}).get("write_latency_ms"), "ms"),
            ("cpu_freq_mhz", (snap.get("cpu_freq") or {}).get("current_mhz"), "MHz"),
        ]
        pressure = snap.get("pressure") or {}
        if pressure.get("memory"):
            rows.append(("psi_mem_avg10", pressure["memory"].get("some_avg10"), "pct"))
        if pressure.get("cpu"):
            rows.append(("psi_cpu_avg10", pressure["cpu"].get("some_avg10"), "pct"))
        if pressure.get("io"):
            rows.append(("psi_io_avg10", pressure["io"].get("some_avg10"), "pct"))

        for t in temp_readings:
            if t["value"] is not None:
                rows.append((f"temp_{t['sensor_id']}", float(t["value"]), "degC"))
        ctx.db.record_telemetry(rows, ctx.identity.host_id, source="engine")
        ctx.db.record_snapshot(ctx.identity.host_id, snap, str(snap["validity"]))

        prev_state = ctx.guardian.state.state
        ctx.guardian.feed(time.time(), temp_readings)
        state = ctx.guardian.evaluate()
        ctx.flags.guardian_state = state.state
        changed = state.state != prev_state

        # Resource safety check
        disks_data = [d.__dict__ if hasattr(d, "__dict__") else d for d in snap.get("disks", [])]
        resource_state = ctx.resource_guardian.evaluate(
            cpu_pct=float(snap.get("cpu_percent") or 0.0),
            mem_pct=float(snap["memory"]["percent"]),
            disks=disks_data,
            throttling=snap.get("throttling"),
        )
        snap["resource_state"] = resource_state.as_dict()

        self._apply_policy(state, simulated, changed)
        self._maybe_prune()

        if self._tick_count % 10 == 0:
            ctx.events.emit(
                "metrics",
                f"heartbeat: cpu {snap.get('cpu_percent', 0):.0f}% "
                f"mem {snap['memory']['percent']:.0f}% "
                f"thermal {state.state}",
                "info",
            )
        return snap

    # -- policy hooks -------------------------------------------------------
    def _apply_policy(self, state: Any, simulated: bool, changed: bool) -> None:
        ctx = self.ctx
        level = state.state
        if level not in ("WARNING", "CRITICAL", "RECOVERY"):
            if ctx.flags.sampling_boosted:
                ctx.flags.sampling_boosted = False
                ctx.events.emit("metrics",
                                "Sampling returned to baseline interval", "info")
            return

        # Keep sampling fast for the whole duration of WARNING/CRITICAL.
        ctx.flags.sampling_boosted = level in ("WARNING", "CRITICAL")

        if not changed:
            return
        for action in ctx.policy.authorized_actions(level, simulated):
            try:
                ctx.policy.execute(
                    action, level, state.sensor_id, simulated, state.reason)
            except Exception:
                _LOG.exception("Action '%s' failed", action)
                ctx.events.emit("action", f"Action {action} raised an error",
                                "warning", action=action)

    def _maybe_prune(self) -> None:
        if time.time() - self._last_prune >= PRUNE_INTERVAL_S:
            pruned = self.ctx.db.prune_telemetry(
                self.ctx.config.database.telemetry_retention_hours)
            self._last_prune = time.time()
            if pruned:
                self.ctx.log.info("Pruned %d old telemetry rows", pruned)


def _now_iso() -> str:
    from aegis.db import utcnow
    return utcnow()
