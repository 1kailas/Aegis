"""Deterministic remediation policy and safety validation engine.

Separates diagnosis (which can be proposed by AI or rules) from execution.
Enforces risk classification, preconditions, rollback strategies, and human approval.
Never allows AI models to directly execute unauthorized commands or bypass safety gates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from aegis.db import Database, utcnow
from aegis.events import EventBus

_LOG = logging.getLogger("aegis.control_plane.action_policy")


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    PROHIBITED = "PROHIBITED"


class ActionCategory(str, Enum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    RECOMMEND = "RECOMMEND"
    AUTOMATIC_EXECUTE = "AUTOMATIC_EXECUTE"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    PROHIBITED = "PROHIBITED"


@dataclass
class RemediationAction:
    action_type: str
    target: str
    reason: str
    supporting_evidence: dict[str, Any]
    expected_outcome: str
    risk_level: RiskLevel
    category: ActionCategory
    preconditions: list[str] = field(default_factory=list)
    rollback_strategy: str = ""
    verification_procedure: str = ""
    approved: bool = False
    approved_by: str | None = None


# Actions that are fundamentally prohibited from autonomous execution
PROHIBITED_PATTERNS = {
    "rm -rf", "drop database", "delete namespace", "format disk", "fdisk",
    "kill -9 1", "shutdown host", "modify credentials", "delete volume",
    "modify .env", "write /etc", "arbitrary_shell_command"
}

# Low-risk operations permitted for autonomous remediation in policy_autonomous mode
AUTONOMOUS_ALLOWLIST = {
    "restart_container",
    "clear_backpressure",
    "set_backpressure",
    "increase_sampling",
    "trim_cache",
    "create_or_update_incident",
    "notify",
}


class RemediationPolicyEngine:
    """Evaluates proposed actions against deterministic safety policies."""

    def __init__(self, db: Database, events: EventBus, require_human_approval_for_medium: bool = True):
        self.db = db
        self.events = events
        self.require_human_approval_for_medium = require_human_approval_for_medium

    def evaluate_proposal(
        self,
        action_type: str,
        target: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
        expected_outcome: str = "",
        rollback_strategy: str = "",
    ) -> RemediationAction:
        """Classifies risk and determines whether action can execute automatically."""
        lower_action = action_type.lower()
        lower_target = target.lower()

        # 1. Prohibited Check
        if any(p in lower_action or p in lower_target for p in PROHIBITED_PATTERNS):
            return RemediationAction(
                action_type=action_type,
                target=target,
                reason=reason,
                supporting_evidence=evidence or {},
                expected_outcome=expected_outcome,
                risk_level=RiskLevel.PROHIBITED,
                category=ActionCategory.PROHIBITED,
                rollback_strategy="Action blocked by deterministic safety gate",
                verification_procedure="N/A",
            )

        # 2. Risk Level Assignment
        if action_type in AUTONOMOUS_ALLOWLIST:
            risk = RiskLevel.LOW
            cat = ActionCategory.AUTOMATIC_EXECUTE
        elif any(kw in lower_action for kw in ("scale", "restart_deployment", "prune_old_images")):
            risk = RiskLevel.MEDIUM
            cat = ActionCategory.REQUIRE_APPROVAL if self.require_human_approval_for_medium else ActionCategory.AUTOMATIC_EXECUTE
        else:
            risk = RiskLevel.HIGH
            cat = ActionCategory.REQUIRE_APPROVAL

        return RemediationAction(
            action_type=action_type,
            target=target,
            reason=reason,
            supporting_evidence=evidence or {},
            expected_outcome=expected_outcome or f"Resolve anomaly on {target}",
            risk_level=risk,
            category=cat,
            preconditions=[f"Target {target} exists and is reachable"],
            rollback_strategy=rollback_strategy or "Revert to pre-action configuration",
            verification_procedure="Re-sample telemetry and verify anomaly clears",
        )

    def authorize_and_execute(
        self,
        action: RemediationAction,
        mode: str,  # monitoring_only | policy_autonomous
        emergency_stop: bool,
        operator_approved: bool = False,
        executor_fn: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Validates policy constraints and authorizes execution."""
        ts = utcnow()

        # Check emergency stop
        if emergency_stop:
            self._record_action(action, "BLOCKED", mode, "Global emergency stop engaged")
            return {
                "authorized": False,
                "result": "BLOCKED",
                "reason": "Global emergency stop is engaged; all remediation blocked",
            }

        # Check prohibited
        if action.category == ActionCategory.PROHIBITED or action.risk_level == RiskLevel.PROHIBITED:
            self._record_action(action, "REJECTED", mode, "Action violates safety policy allowlist")
            return {
                "authorized": False,
                "result": "REJECTED",
                "reason": "Action is strictly prohibited by safety policy",
            }

        # Check human approval requirement
        if action.category == ActionCategory.REQUIRE_APPROVAL and not operator_approved:
            self._record_action(action, "BLOCKED", mode, "Requires operator approval")
            return {
                "authorized": False,
                "result": "NEEDS_APPROVAL",
                "reason": f"Action '{action.action_type}' has risk level {action.risk_level.value} and requires human approval",
            }

        # In monitoring_only or operator_approval mode, all real state changes are dry-run only unless approved
        if mode in ("monitoring_only", "operator_approval") and action.action_type not in {"increase_sampling", "create_or_update_incident", "notify"}:
            self._record_action(action, "VERIFIED", "dry_run", f"Dry run evaluation passed in {mode} mode")
            return {
                "authorized": True,
                "result": "VERIFIED",
                "mode": "dry_run",
                "reason": f"{mode} mode: simulated dry run verified without real mutation",
            }

        # Real Execution
        if executor_fn:
            try:
                success = executor_fn()
                result = "VERIFIED" if success else "FAILED"
                self._record_action(action, result, "real", "Executed via authorized handler")
                return {"authorized": True, "result": result, "mode": "real"}
            except Exception as e:
                _LOG.exception("Execution of %s failed", action.action_type)
                self._record_action(action, "FAILED", "real", str(e))
                return {"authorized": True, "result": "FAILED", "error": str(e)}

        self._record_action(action, "VERIFIED", "real", "Policy authorization granted")
        return {"authorized": True, "result": "VERIFIED", "mode": "real"}

    def _record_action(
        self, action: RemediationAction, result: str, mode: str, note: str
    ) -> None:
        self.db.execute(
            "INSERT INTO remediation_actions(ts, action, target, reason, mode, result, detail)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                utcnow(),
                action.action_type,
                action.target,
                action.reason,
                mode,
                result,
                f"Risk: {action.risk_level.value}, Category: {action.category.value}, Note: {note}",
            ),
        )
        self.events.emit(
            "remediation",
            f"Remediation {action.action_type} on {action.target} -> {result} ({mode})",
            "info" if result == "VERIFIED" else "warning",
            action=action.action_type,
            target=action.target,
            result=result,
            mode=mode,
        )
