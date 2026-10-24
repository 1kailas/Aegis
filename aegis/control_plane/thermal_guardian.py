"""Deterministic thermal guardian.

State machine: NORMAL -> WARNING -> CRITICAL -> RECOVERY -> NORMAL with
re-escalation if temperatures rise again. All decisions come from real
or clearly-labelled simulated readings; missing or stale sensor data is
never treated as evidence of safe operation (the level is held with an
explicit NO_SENSORS indicator instead).

Trend: least-squares slope over the rolling window. Estimated time to a
threshold is suppressed when the slope is too small, too few points
exist, the sensor is stale or the fit would be unreliable.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from aegis.config import ThermalPolicyConfig
from aegis.events import EventBus

NORMAL = "NORMAL"
WARNING = "WARNING"
CRITICAL = "CRITICAL"
RECOVERY = "RECOVERY"

APPROACH_MARGIN_C = 5.0  # rate-of-rise triggers this close to the threshold


@dataclass
class GuardianState:
    state: str
    sensor_id: str | None = None
    current_c: float | None = None
    trend_per_s: float | None = None
    eta_to_warning_s: float | None = None
    eta_to_critical_s: float | None = None
    data_quality: str = "OK"   # OK | NO_SENSORS | STALE
    reason: str = "initialized"
    entered_state_at: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "sensor_id": self.sensor_id,
            "current_c": self.current_c,
            "trend_per_s": self.trend_per_s,
            "eta_to_warning_s": self.eta_to_warning_s,
            "eta_to_critical_s": self.eta_to_critical_s,
            "data_quality": self.data_quality,
            "reason": self.reason,
            "entered_state_at": self.entered_state_at,
        }


class ThermalGuardian:
    """Per-sensor rolling windows feeding a single hottest-sensor decision."""

    def __init__(
        self,
        cfg: ThermalPolicyConfig,
        host_id: str,
        events: EventBus | None = None,
        on_transition: Callable[
            [float, str, str, str | None, str, float | None], None
        ] | None = None,
    ):
        self.cfg = cfg
        self.host_id = host_id
        self.events = events
        self.on_transition = on_transition
        self._lock = threading.Lock()
        self._windows: dict[str, list[tuple[float, float]]] = {}
        self._state = GuardianState(state=NORMAL, reason="initialized",
                                    entered_state_at=time.time())
        self._recovery_started_at: float | None = None

    # -- ingestion --------------------------------------------------------
    def feed(self, readings_ts: float, temps: list[dict[str, Any]]) -> None:
        """Ingest temperature readings (dict form from the registry/overrides)."""
        with self._lock:
            for r in temps:
                if r.get("validity") == "ERROR" or r.get("value") is None:
                    continue
                win = self._windows.setdefault(r["sensor_id"], [])
                win.append((readings_ts, float(r["value"])))
                max_pts = max(3, self.cfg.window_points) * 3
                if len(win) > max_pts:
                    del win[: len(win) - max_pts]

    # -- evaluation -------------------------------------------------------
    def evaluate(self, now: float | None = None) -> GuardianState:
        now = time.time() if now is None else now
        with self._lock:
            fresh: dict[str, float] = {}
            stale_sensors: list[str] = []
            for sid, win in self._windows.items():
                pts = [
                    (t, v) for t, v in win
                    if now - t <= self.cfg.stale_after_s and math.isfinite(v)
                ]
                if pts:
                    fresh[sid] = pts[-1][1]
                else:
                    stale_sensors.append(sid)

            if not fresh:
                self._hold_without_data(stale_sensors)
            else:
                sensor_id, current = max(fresh.items(), key=lambda kv: kv[1])
                self._advance(now, sensor_id, current)
            return self._state

    # -- internals ----------------------------------------------------------
    def _advance(self, now: float, sensor_id: str, current: float) -> GuardianState:
        cfg = self.cfg
        prev = self._state.state
        slope = self._trend(sensor_id, now)
        eta_w = _eta(current, cfg.warning_c, slope, cfg.slope_min_for_estimate)
        eta_c = _eta(current, cfg.critical_c, slope, cfg.slope_min_for_estimate)

        new_state = prev
        reason = self._state.reason

        # Absolute threshold escalation.
        if current >= cfg.critical_c:
            new_state = CRITICAL
            reason = f"{sensor_id} at {current:.1f}degC >= critical {cfg.critical_c}degC"
        elif current >= cfg.warning_c:
            new_state = WARNING
            reason = f"{sensor_id} at {current:.1f}degC >= warning {cfg.warning_c}degC"

        # Rate-of-rise escalation (only when near the threshold).
        if slope is not None and slope >= cfg.rate_per_s_warning \
                and current >= cfg.warning_c - APPROACH_MARGIN_C:
            if new_state == NORMAL:
                new_state = WARNING
                reason = (f"rapid rise {slope:.2f}deg/s at {current:.1f}degC"
                          f" within {APPROACH_MARGIN_C}degC of warning")
            elif new_state == RECOVERY:
                new_state = WARNING
                reason = (f"temperature rising again {slope:.2f}deg/s"
                          f" during recovery")

        # De-escalation path (hysteresis + recovery hold).
        if new_state == prev:
            if prev == WARNING and current <= cfg.warning_c - cfg.hysteresis_c:
                new_state = NORMAL
                reason = f"{sensor_id} recovered to {current:.1f}degC"
            elif prev == CRITICAL and current <= cfg.recovery_below_c:
                new_state = RECOVERY
                reason = (f"below recovery level; hold until cooling confirmed"
                          f" for {cfg.recovery_hold_s:.0f}s")
                self._recovery_started_at = now
            elif prev == RECOVERY and self._recovery_started_at is not None \
                    and now - self._recovery_started_at >= cfg.recovery_hold_s \
                    and current <= cfg.recovery_below_c:
                new_state = NORMAL
                reason = (f"stable below {cfg.recovery_below_c:.1f}degC"
                          f" for {cfg.recovery_hold_s:.0f}s")
            self._recovery_started_at = (
                now if (new_state == RECOVERY and prev != RECOVERY
                        and self._recovery_started_at is None)
                else self._recovery_started_at
            )
        else:
            self._recovery_started_at = now if new_state == RECOVERY else None

        entered = now if new_state != prev else self._state.entered_state_at
        self._state = GuardianState(
            state=new_state,
            sensor_id=sensor_id,
            current_c=current,
            trend_per_s=slope,
            eta_to_warning_s=eta_w,
            eta_to_critical_s=eta_c,
            data_quality="STALE" if stale_sensors else "OK",
            reason=reason,
            entered_state_at=entered,
        )
        if new_state != prev:
            self._record(now, prev, new_state, sensor_id, reason, current)
        return self._state

    def _hold_without_data(self, stale_sensors: list[str]) -> None:
        prev = self._state.state
        self._state.data_quality = "NO_SENSORS"
        self._state.current_c = None
        self._state.trend_per_s = None
        self._state.eta_to_warning_s = None
        self._state.eta_to_critical_s = None
        if prev in (WARNING, CRITICAL, RECOVERY):
            self._state.reason = (
                "sensor data stale or unavailable; holding level and NOT"
                " treating missing data as safe"
            )
        else:
            self._state.reason = "no temperature sensor data available"

    def _trend(self, sensor_id: str, now: float) -> float | None:
        win = self._windows.get(sensor_id) or []
        cfg = self.cfg
        pts = [
            (t, v) for t, v in win
            if now - t <= cfg.stale_after_s and math.isfinite(v)
        ]
        if len(pts) < cfg.min_points_for_slope:
            return None
        span = pts[-1][0] - pts[0][0]
        if span <= 0 or span < max(2.0, float(cfg.min_points_for_slope) - 1):
            return None  # points too close together: unreliable fit
        xs = [t - pts[0][0] for t, _ in pts]
        ys = [v for _, v in pts]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        if denom <= 1e-9:
            return None
        slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
        resid = max(abs(ys[i] - (my + slope * (xs[i] - mx))) for i in range(n))
        if resid > max(5.0, 3 * max(1.0, abs(slope) * span)):
            return None  # residual band too wide: no trustworthy estimate
        return slope

    def _record(self, now: float, prev: str, new: str, sensor_id: str | None,
                reason: str, current: float | None) -> None:
        if self.on_transition:
            try:
                self.on_transition(now, prev, new, sensor_id, reason, current)
            except Exception:
                pass

    @property
    def state(self) -> GuardianState:
        return self._state


def _eta(current: float, threshold: float, slope: float | None,
         min_slope: float) -> float | None:
    if slope is None or abs(slope) < min_slope or current >= threshold:
        return None
    return round((threshold - current) / slope, 1) if slope > 0 else None
