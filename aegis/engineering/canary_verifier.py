"""Automated canary deployment verification and closed-loop rollback engine.

Observes progressive rollouts over staged observation windows. If latency SLAs
degrade or error rates exceed acceptable thresholds, automatically initiates a
safe rollback to the last-known-good state.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from aegis.db import Database, utcnow
from aegis.events import EventBus

_LOG = logging.getLogger("aegis.engineering.canary_verifier")


class CanaryState(str, Enum):
    CANARY_INIT = "CANARY_INIT"
    CANARY_OBSERVING = "CANARY_OBSERVING"
    PROMOTED = "PROMOTED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class CanaryRolloutRecord:
    rollout_id: str
    target: str
    state: CanaryState
    current_step: int = 1
    total_steps: int = 3
    error_rate: float = 0.0
    latency_ms: float = 0.0
    revert_trigger: str | None = None
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rollout_id": self.rollout_id,
            "target": self.target,
            "state": self.state.value,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "error_rate": round(self.error_rate, 3),
            "latency_ms": round(self.latency_ms, 1),
            "revert_trigger": self.revert_trigger,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CanaryVerificationEngine:
    """Manages progressive canary evaluation and automated rollbacks."""

    def __init__(
        self,
        db: Database,
        events: EventBus,
        max_error_rate: float = 0.05,        # 5% max error rate
        max_latency_sla_ms: float = 500.0,   # 500ms SLA
    ):
        self.db = db
        self.events = events
        self.max_error_rate = max_error_rate
        self.max_latency_sla_ms = max_latency_sla_ms
        self._active_rollouts: dict[str, CanaryRolloutRecord] = {}

    def init_rollout(
        self, target: str, total_steps: int = 3, rollout_id: str | None = None
    ) -> CanaryRolloutRecord:
        """Initializes a new canary rollout record."""
        rid = rollout_id or f"CANARY-{int(time.time())}-{target.replace('/', '_')}"
        now = utcnow()
        record = CanaryRolloutRecord(
            rollout_id=rid,
            target=target,
            state=CanaryState.CANARY_OBSERVING,
            current_step=1,
            total_steps=total_steps,
            created_at=now,
            updated_at=now,
        )
        self._active_rollouts[rid] = record
        self.db.execute(
            "INSERT INTO canary_rollouts(rollout_id, target, state, current_step,"
            " total_steps, error_rate, latency_ms, revert_trigger, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                record.rollout_id,
                record.target,
                record.state.value,
                record.current_step,
                record.total_steps,
                record.error_rate,
                record.latency_ms,
                record.revert_trigger,
                record.created_at,
                record.updated_at,
            ),
        )
        self.events.emit(
            "canary",
            f"Canary rollout initiated for {target} (step 1/{total_steps})",
            "info",
            rollout_id=rid,
            target=target,
        )
        return record

    def evaluate_step(
        self,
        rollout_id: str,
        live_error_rate: float,
        live_latency_ms: float,
        rollback_fn: Callable[[], bool] | None = None,
    ) -> CanaryRolloutRecord:
        """Evaluates live metrics against SLA budgets, triggering rollback on breach."""
        record = self._active_rollouts.get(rollout_id)
        if not record:
            row = self.db.query_one("SELECT * FROM canary_rollouts WHERE rollout_id=?", (rollout_id,))
            if not row:
                raise ValueError(f"Rollout '{rollout_id}' not found")
            record = CanaryRolloutRecord(
                rollout_id=row["rollout_id"],
                target=row["target"],
                state=CanaryState(row["state"]),
                current_step=row["current_step"],
                total_steps=row["total_steps"],
                error_rate=row["error_rate"],
                latency_ms=row["latency_ms"],
                revert_trigger=row["revert_trigger"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            self._active_rollouts[rollout_id] = record

        record.error_rate = live_error_rate
        record.latency_ms = live_latency_ms
        now = utcnow()
        record.updated_at = now

        # 1. Check for Breach -> Automated Rollback Trigger
        breach_reason = None
        if live_error_rate > self.max_error_rate:
            breach_reason = f"Error rate {live_error_rate:.1%} exceeded budget {self.max_error_rate:.1%}"
        elif live_latency_ms > self.max_latency_sla_ms:
            breach_reason = f"Latency {live_latency_ms:.1f}ms breached SLA {self.max_latency_sla_ms:.1f}ms"

        if breach_reason:
            record.state = CanaryState.ROLLING_BACK
            record.revert_trigger = breach_reason
            self.events.emit(
                "canary",
                f"Canary breach on {record.target}: {breach_reason} -> Triggering automated rollback",
                "critical",
                rollout_id=rollout_id,
                reason=breach_reason,
            )

            rollback_success = True
            if rollback_fn:
                try:
                    rollback_success = rollback_fn()
                except Exception as e:
                    _LOG.error("Rollback execution failure: %s", e)
                    rollback_success = False

            record.state = CanaryState.ROLLED_BACK if rollback_success else CanaryState.ROLLING_BACK
            self._persist_record(record)
            return record

        # 2. Advance Step or Promote
        if record.current_step < record.total_steps:
            record.current_step += 1
            self.events.emit(
                "canary",
                f"Canary step {record.current_step}/{record.total_steps} passed for {record.target} (errors: {live_error_rate:.1%}, latency: {live_latency_ms:.1f}ms)",
                "info",
                rollout_id=rollout_id,
            )
        else:
            record.state = CanaryState.PROMOTED
            self.events.emit(
                "canary",
                f"Canary rollout {rollout_id} for {record.target} verified & fully PROMOTED",
                "info",
                rollout_id=rollout_id,
            )

        self._persist_record(record)
        return record

    def _persist_record(self, record: CanaryRolloutRecord) -> None:
        self.db.execute(
            "UPDATE canary_rollouts SET state=?, current_step=?, error_rate=?,"
            " latency_ms=?, revert_trigger=?, updated_at=? WHERE rollout_id=?",
            (
                record.state.value,
                record.current_step,
                record.error_rate,
                record.latency_ms,
                record.revert_trigger,
                record.updated_at,
                record.rollout_id,
            ),
        )

    def list_rollouts(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM canary_rollouts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
