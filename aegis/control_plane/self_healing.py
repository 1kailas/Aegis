"""Autonomous self-healing engine for closed-loop incident resolution.

Connects anomaly detection, AI/rule diagnosis, policy authorization, Devin repair
delegation, workspace patch validation, and recovery verification into an
autonomous self-healing lifecycle with retry bounds and dead-letter escalation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.control_plane.self_healing")


@dataclass
class SelfHealingCycleReport:
    cycle_id: str
    incidents_evaluated: int
    diagnosed_count: int
    actions_attempted: int
    actions_succeeded: int
    devin_tasks_created: int
    escalated_count: int
    timestamp: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "incidents_evaluated": self.incidents_evaluated,
            "diagnosed_count": self.diagnosed_count,
            "actions_attempted": self.actions_attempted,
            "actions_succeeded": self.actions_succeeded,
            "devin_tasks_created": self.devin_tasks_created,
            "escalated_count": self.escalated_count,
            "timestamp": self.timestamp,
        }


class SelfHealingEngine:
    """Orchestrates autonomous triage, safe remediation, and repair delegation."""

    def __init__(self, ctx: Any, max_attempts_per_incident: int = 3, cycle_cooldown_s: float = 10.0):
        self.ctx = ctx
        self.max_attempts_per_incident = max_attempts_per_incident
        self.cycle_cooldown_s = cycle_cooldown_s
        self._incident_attempts: dict[str, int] = {}
        self._last_cycle_ts: float = 0.0

    def run_reconciliation_cycle(self) -> SelfHealingCycleReport:
        """Executes a single self-healing evaluation pass over active incidents."""
        ctx = self.ctx
        now = time.time()
        if now - self._last_cycle_ts < self.cycle_cooldown_s:
            return SelfHealingCycleReport(
                cycle_id=f"CYCLE-SKIP-{int(now)}",
                incidents_evaluated=0,
                diagnosed_count=0,
                actions_attempted=0,
                actions_succeeded=0,
                devin_tasks_created=0,
                escalated_count=0,
            )
        self._last_cycle_ts = now
        cid = f"CYCLE-{int(now)}"

        active_incidents = ctx.incidents.active()
        diagnosed = 0
        attempted = 0
        succeeded = 0
        devin_created = 0
        escalated = 0

        for inc in active_incidents:
            iid = inc["id"]
            state = inc.get("state", "DETECTED")
            attempts = self._incident_attempts.get(iid, 0)

            # Check dead-letter escalation
            if attempts >= self.max_attempts_per_incident:
                if state != "NEEDS_HUMAN_APPROVAL":
                    ctx.incidents.transition(
                        iid, "NEEDS_HUMAN_APPROVAL", "self_healing",
                        note=f"Max auto-remediation attempts ({attempts}) exceeded; manual SRE intervention required"
                    )
                    ctx.events.emit(
                        "self_healing",
                        f"Incident {iid} escalated to NEEDS_HUMAN_APPROVAL",
                        "critical",
                        incident_id=iid,
                    )
                    escalated += 1
                continue

            # 1. Triage: If DETECTED, trigger diagnosis
            if state == "DETECTED":
                try:
                    diag = ctx.diagnoser.diagnose(inc)
                    diagnosed += 1
                    ctx.incidents.append_evidence(iid, {"kind": "ai_diagnosis", "result": diag.as_dict()})
                    ctx.incidents.transition(
                        iid, "INVESTIGATING", "self_healing",
                        note=f"Diagnosed by {diag.provider} ({diag.model_name}): {diag.root_cause_hypothesis}"
                    )
                    ctx.events.emit(
                        "self_healing",
                        f"Autonomous diagnosis for {iid}: {diag.root_cause_hypothesis}",
                        "info",
                        incident_id=iid,
                        diagnosis=diag.as_dict(),
                    )

                    # 2. Automated Remediation for Allowlisted Operations
                    if ctx.flags.mode == "policy_autonomous" and not ctx.flags.emergency_stop:
                        for act_proposal in diag.recommended_actions:
                            action_name = act_proposal.get("action", "")
                            target = act_proposal.get("target", "system")
                            reason = act_proposal.get("reason", diag.root_cause_hypothesis)

                            attempted += 1
                            self._incident_attempts[iid] = attempts + 1

                            # Authorize action
                            auth_res = ctx.remediation.authorize_and_execute(
                                action=ctx.remediation.evaluate_proposal(action_name, target, reason),
                                mode="policy_autonomous",
                                emergency_stop=ctx.flags.emergency_stop,
                            )

                            if auth_res.get("authorized") and auth_res.get("result") in ("VERIFIED", "SUCCESS"):
                                succeeded += 1
                                ctx.incidents.transition(
                                    iid, "CONTAINED", "self_healing",
                                    note=f"Remediation '{action_name}' executed autonomously on {target}"
                                )
                                break

                    # 3. Code/Config Repair Delegation (Devin)
                    if diag.is_code_or_config_issue:
                        task = ctx.devin.create_repair_task(
                            incident=inc,
                            sanitized_logs=inc.get("summary", ""),
                        )
                        devin_created += 1
                        ctx.incidents.transition(
                            iid, "REPAIRING", "self_healing",
                            note=f"Delegated to Devin engineering task {task.task_id}"
                        )
                        ctx.events.emit(
                            "self_healing",
                            f"Created Devin repair task {task.task_id} for incident {iid}",
                            "info",
                            task_id=task.task_id,
                            incident_id=iid,
                        )

                except Exception as e:
                    _LOG.warning("Self-healing cycle exception on %s: %s", iid, e)

        return SelfHealingCycleReport(
            cycle_id=cid,
            incidents_evaluated=len(active_incidents),
            diagnosed_count=diagnosed,
            actions_attempted=attempted,
            actions_succeeded=succeeded,
            devin_tasks_created=devin_created,
            escalated_count=escalated,
        )
