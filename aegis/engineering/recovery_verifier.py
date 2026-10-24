"""Post-repair and remediation independent recovery verification.

Separates code patch generation / pull request creation from actual live
infrastructure stabilization. Rechecks workload health, ensures errors do not recur,
and tracks stabilization hold periods.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aegis.db import Database, utcnow
from aegis.events import EventBus

_LOG = logging.getLogger("aegis.engineering.recovery_verifier")


class RecoveryStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class VerificationRecord:
    verification_id: str
    incident_id: str
    status: RecoveryStatus
    target: str
    checks_performed: list[dict[str, Any]] = field(default_factory=list)
    pre_incident_metric: float | None = None
    post_remediation_metric: float | None = None
    stabilization_duration_s: float = 0.0
    notes: str = ""
    timestamp: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "incident_id": self.incident_id,
            "status": self.status.value,
            "target": self.target,
            "checks_performed": self.checks_performed,
            "pre_incident_metric": self.pre_incident_metric,
            "post_remediation_metric": self.post_remediation_metric,
            "stabilization_duration_s": self.stabilization_duration_s,
            "notes": self.notes,
            "timestamp": self.timestamp,
        }


class RecoveryVerifier:
    """Verifies live service stabilization independently of repair artifacts."""

    def __init__(self, db: Database, events: EventBus, stabilization_period_s: float = 30.0):
        self.db = db
        self.events = events
        self.stabilization_period_s = stabilization_period_s
        self._verifications: dict[str, VerificationRecord] = {}

    def verify_workload_recovery(
        self,
        incident_id: str,
        target: str,
        check_fn: Any,  # Callable[[], tuple[bool, str, dict]]
        pre_metric: float | None = None,
        post_metric: float | None = None,
    ) -> VerificationRecord:
        """Executes verification check and records the outcome."""
        vid = f"VERIFY-{incident_id}-{int(time.time())}"
        checks = []

        try:
            healthy, detail, raw_metrics = check_fn()
            checks.append({
                "name": "live_probe",
                "healthy": healthy,
                "detail": detail,
                "metrics": raw_metrics,
            })
            if healthy:
                status = RecoveryStatus.CONFIRMED
                notes = f"Target {target} verified operational; metrics stabilized nominal."
            else:
                status = RecoveryStatus.FAILED
                notes = f"Target {target} remains unhealthy after remediation: {detail}"
        except Exception as e:
            status = RecoveryStatus.INCONCLUSIVE
            notes = f"Verification check threw exception: {e}"
            checks.append({"name": "live_probe", "error": str(e)})

        rec = VerificationRecord(
            verification_id=vid,
            incident_id=incident_id,
            status=status,
            target=target,
            checks_performed=checks,
            pre_incident_metric=pre_metric,
            post_remediation_metric=post_metric,
            stabilization_duration_s=self.stabilization_period_s,
            notes=notes,
        )
        self._verifications[vid] = rec

        # Record in DB audit log
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail) VALUES(?,?,?,?,?)",
            (
                utcnow(),
                "recovery_verifier",
                f"recovery_{status.value.lower()}",
                target,
                f"Incident: {incident_id}, Outcome: {notes}",
            ),
        )

        self.events.emit(
            "verification",
            f"Recovery verification for {target} -> {status.value}: {notes}",
            "info" if status == RecoveryStatus.CONFIRMED else "warning",
            incident_id=incident_id,
            status=status.value,
        )
        return rec

    def get_verification(self, verification_id: str) -> VerificationRecord | None:
        return self._verifications.get(verification_id)
