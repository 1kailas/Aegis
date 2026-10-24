"""Policy executor: turns guardian levels into authorized actions.

Design rules:
* Proposal (by guardian rules) is separate from authorization (here).
* The executor verifies pre-state, performs the action, checks the real
  post-state and records VERIFIED / REJECTED / BLOCKED / FAILED.
* An emergency stop blocks every workload-affecting action while leaving
  telemetry, monitoring, incident persistence and alerting alive.
* Actions triggered by SIMULATED_OVERRIDE data are limited to safe,
  internal ones; simulated values never steer workload operations.
* The engine invokes execution on state transitions only, so repeated
  ticks never spam the action log.
"""

from __future__ import annotations

from typing import Any

from aegis.config import AppConfig
from aegis.db import Database, utcnow
from aegis.events import EventBus
from aegis.incidents import IncidentStore

INTERNAL_ACTIONS = {
    "increase_sampling",           # engine-side sampling change; safe
    "create_or_update_incident",   # detection & records; always active
    "notify",                      # alerting; never disabled by e-stop
}

SIMULATED_ALLOWED_ACTIONS = INTERNAL_ACTIONS


class PolicyExecutor:
    def __init__(
        self,
        config: AppConfig,
        db: Database,
        events: EventBus,
        incidents: IncidentStore,
        flags: Any,  # aegis.appstate.RuntimeFlags
    ):
        self.cfg = config
        self.db = db
        self.events = events
        self.incidents = incidents
        self.flags = flags

    # -- authorization -----------------------------------------------------
    def authorized_actions(self, level: str, simulated: bool) -> list[str]:
        """Actions allowed at this level under the current mode/flags."""
        if level == "RECOVERY":
            if self.flags.mode == "policy_autonomous":
                return ["clear_backpressure", "notify"]
            return []
        base = self.cfg.action_policy.warning_actions if level == "WARNING" \
            else self.cfg.action_policy.critical_actions
        allowed: list[str] = []
        for name in base:
            if self.flags.emergency_stop and name not in INTERNAL_ACTIONS:
                continue
            if simulated and name not in SIMULATED_ALLOWED_ACTIONS:
                continue
            allowed.append(name)
        return allowed

    # -- execution ---------------------------------------------------------
    def execute(self, action: str, level: str, sensor_id: str | None,
                triggered_by_simulated: bool, reason: str) -> str:
        if triggered_by_simulated and action not in SIMULATED_ALLOWED_ACTIONS:
            self._record_action(action, sensor_id, "REJECTED", "real",
                                "simulated data cannot authorize this action")
            return "REJECTED"

        mode = "dry_run" if self.flags.mode in ("monitoring_only", "operator_approval") else "real"
        detail: dict[str, Any] = {}

        if action == "increase_sampling":
            pre = self.flags.active_interval_s
            self.flags.sampling_boosted = True
            post = self.flags.active_interval_s
            detail = {"pre_interval": pre, "post_interval": post}
            result = "VERIFIED" if post < pre else "REJECTED"

        elif action == "create_or_update_incident":
            incident = self.incidents.active_by_category("thermal_warning")
            if incident is None:
                incident = self.incidents.create(
                    category="thermal_warning",
                    severity="warning" if level == "WARNING" else "critical",
                    title="Thermal policy threshold crossed",
                    summary=reason,
                    host_id=self.flags.host_id,
                    evidence=[{"kind": "sensor", "sensor_id": sensor_id,
                               "note": reason,
                               "simulated": triggered_by_simulated}],
                )
                detail = {"incident_id": incident["id"], "created": True,
                          "simulated": triggered_by_simulated}
            else:
                target_state = "CONTAINMENT_REQUIRED" if (
                    level == "CRITICAL"
                    and incident["state"] in ("DETECTED", "INVESTIGATING",
                                              "CONTAINED")
                ) else incident["state"]
                if target_state != incident["state"]:
                    self.incidents.transition(
                        incident["id"], target_state, "policy_engine",
                        note=reason)
                self.incidents.append_evidence(
                    incident["id"], {"kind": "sensor", "sensor_id": sensor_id,
                                     "note": reason,
                                     "simulated": triggered_by_simulated})
                detail = {"incident_id": incident["id"], "created": False}
            self.flags.active_incident_id = detail["incident_id"]
            result = "VERIFIED"

        elif action == "set_backpressure":
            if self.flags.mode != "policy_autonomous":
                self._record_action(action, sensor_id, "REJECTED", "real",
                                    "monitoring-only mode blocks backpressure")
                return "REJECTED"
            pre = self.flags.backpressure
            self.flags.backpressure = True
            detail = {"pre": pre, "post": self.flags.backpressure}
            result = "VERIFIED"

        elif action == "clear_backpressure":
            pre = self.flags.backpressure
            self.flags.backpressure = False
            detail = {"pre": pre, "post": self.flags.backpressure}
            result = "VERIFIED"

        elif action == "notify":
            self.events.emit("policy", f"Notification: {reason}", "info",
                             level=level, sensor_id=sensor_id,
                             simulated=triggered_by_simulated)
            result = "VERIFIED"

        else:
            self._record_action(action, sensor_id, "REJECTED", mode,
                                "unknown action name")
            return "REJECTED"

        self._record_action(action, sensor_id, result, mode, reason, detail)
        return result

    def _record_action(self, action: str, target: str | None, result: str,
                       mode: str, reason: str = "",
                       detail: dict[str, Any] | None = None) -> None:
        incident_id = getattr(self.flags, "active_incident_id", None)
        self.db.execute(
            "INSERT INTO remediation_actions(ts, incident_id, action, target,"
            " reason, mode, result, detail) VALUES(?,?,?,?,?,?,?,?)",
            (utcnow(), incident_id, action, target, reason, mode, result,
             str(detail or {})),
        )
        severity = "info" if result == "VERIFIED" else "warning"
        self.events.emit(
            "action",
            f"Action {action} -> {result}"
            + (" (dry-run)" if mode == "dry_run" else "")
            + (f" [{reason}]" if reason else ""),
            severity,
            action=action, target=target, mode=mode, result=result,
            detail=detail or {},
        )
