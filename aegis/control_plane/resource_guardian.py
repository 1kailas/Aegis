"""Deterministic resource safety controller for memory, disk, CPU saturation, and throttling.

Independent of every AI model; operates purely on deterministic thresholds,
hysteresis, and cooldowns.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from aegis.events import EventBus

_LOG = logging.getLogger("aegis.control_plane.resource_guardian")

NORMAL = "NORMAL"
WARNING = "WARNING"
CRITICAL = "CRITICAL"
RECOVERY = "RECOVERY"


@dataclass
class ResourceState:
    state: str = NORMAL
    active_breaches: list[str] = field(default_factory=list)
    memory_pct: float | None = None
    disk_pct: float | None = None
    cpu_pct: float | None = None
    throttling_active: bool = False
    reason: str = "initialized"
    entered_state_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "active_breaches": self.active_breaches,
            "memory_pct": self.memory_pct,
            "disk_pct": self.disk_pct,
            "cpu_pct": self.cpu_pct,
            "throttling_active": self.throttling_active,
            "reason": self.reason,
            "entered_state_at": self.entered_state_at,
        }


@dataclass
class ResourcePolicy:
    memory_warning_pct: float = 85.0
    memory_critical_pct: float = 95.0
    memory_recovery_pct: float = 75.0

    disk_warning_pct: float = 85.0
    disk_critical_pct: float = 95.0
    disk_recovery_pct: float = 80.0

    cpu_warning_pct: float = 90.0
    cpu_critical_pct: float = 98.0
    cpu_recovery_pct: float = 75.0

    recovery_hold_s: float = 30.0
    cooldown_s: float = 60.0


class ResourceGuardian:
    """Monitors host resource saturation (memory, disk, CPU, throttling) deterministically."""

    def __init__(
        self,
        policy: ResourcePolicy | None = None,
        events: EventBus | None = None,
        on_transition: Callable[[float, str, str, str, dict[str, Any]], None] | None = None,
    ):
        self.policy = policy or ResourcePolicy()
        self.events = events
        self.on_transition = on_transition
        self._lock = threading.Lock()
        self._state = ResourceState(state=NORMAL, reason="initialized", entered_state_at=time.time())
        self._recovery_started_at: float | None = None
        self._last_action_ts: float = 0.0

    @property
    def state(self) -> ResourceState:
        with self._lock:
            return self._state

    def evaluate(
        self,
        cpu_pct: float,
        mem_pct: float,
        disks: list[dict[str, Any]],
        throttling: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> ResourceState:
        now = time.time() if now is None else now
        with self._lock:
            max_disk_pct = 0.0
            for d in disks:
                pct = d.get("used_pct", 0.0)
                if pct > max_disk_pct:
                    max_disk_pct = pct

            throttling_active = bool(throttling and throttling.get("throttling"))

            critical_reasons: list[str] = []
            warning_reasons: list[str] = []

            # Check Memory
            if mem_pct >= self.policy.memory_critical_pct:
                critical_reasons.append(f"Memory exhaustion critical: {mem_pct:.1f}% >= {self.policy.memory_critical_pct}%")
            elif mem_pct >= self.policy.memory_warning_pct:
                warning_reasons.append(f"Memory usage warning: {mem_pct:.1f}% >= {self.policy.memory_warning_pct}%")

            # Check Disks
            if max_disk_pct >= self.policy.disk_critical_pct:
                critical_reasons.append(f"Disk space critical: {max_disk_pct:.1f}% >= {self.policy.disk_critical_pct}%")
            elif max_disk_pct >= self.policy.disk_warning_pct:
                warning_reasons.append(f"Disk space warning: {max_disk_pct:.1f}% >= {self.policy.disk_warning_pct}%")

            # Check CPU
            if cpu_pct >= self.policy.cpu_critical_pct:
                critical_reasons.append(f"CPU saturation critical: {cpu_pct:.1f}% >= {self.policy.cpu_critical_pct}%")
            elif cpu_pct >= self.policy.cpu_warning_pct:
                warning_reasons.append(f"CPU saturation warning: {cpu_pct:.1f}% >= {self.policy.cpu_warning_pct}%")

            # Check Throttling
            if throttling_active:
                warning_reasons.append("CPU thermal throttling active on host")

            prev_state = self._state.state
            target_state = NORMAL
            reason = "all resources within nominal bounds"
            breaches: list[str] = []

            if critical_reasons:
                target_state = CRITICAL
                breaches = critical_reasons
                reason = "; ".join(critical_reasons)
                self._recovery_started_at = None
            elif warning_reasons:
                target_state = WARNING
                breaches = warning_reasons
                reason = "; ".join(warning_reasons)
                self._recovery_started_at = None
            elif prev_state in (WARNING, CRITICAL, RECOVERY):
                # Check recovery conditions with hysteresis
                recovered = (
                    mem_pct <= self.policy.memory_recovery_pct
                    and max_disk_pct <= self.policy.disk_recovery_pct
                    and cpu_pct <= self.policy.cpu_recovery_pct
                    and not throttling_active
                )
                if recovered:
                    if prev_state == RECOVERY:
                        if self._recovery_started_at and (now - self._recovery_started_at >= self.policy.recovery_hold_s):
                            target_state = NORMAL
                            reason = f"resources stabilized below recovery thresholds for {self.policy.recovery_hold_s}s"
                            self._recovery_started_at = None
                        else:
                            target_state = RECOVERY
                            reason = "holding in recovery state until cooldown elapses"
                    else:
                        target_state = RECOVERY
                        self._recovery_started_at = now
                        reason = f"metrics nominal; entering {self.policy.recovery_hold_s}s recovery verification hold"
                else:
                    # Still elevated, hold previous warning/critical
                    target_state = prev_state
                    reason = f"metrics remain above recovery thresholds (mem: {mem_pct:.1f}%, disk: {max_disk_pct:.1f}%)"

            if target_state != prev_state:
                self._state = ResourceState(
                    state=target_state,
                    active_breaches=breaches,
                    memory_pct=mem_pct,
                    disk_pct=max_disk_pct,
                    cpu_pct=cpu_pct,
                    throttling_active=throttling_active,
                    reason=reason,
                    entered_state_at=now,
                )
                if self.on_transition:
                    self.on_transition(
                        now,
                        prev_state,
                        target_state,
                        reason,
                        self._state.as_dict(),
                    )
            else:
                self._state.memory_pct = mem_pct
                self._state.disk_pct = max_disk_pct
                self._state.cpu_pct = cpu_pct
                self._state.throttling_active = throttling_active
                self._state.active_breaches = breaches
                self._state.reason = reason

            return self._state
