"""Persistent incident lifecycle.

Phase 1 supports detection through containment and closure. The state
map already covers the full product lifecycle so later phases reuse the
same validated state machine without a schema change.
"""

from __future__ import annotations

import json
from typing import Any

from aegis.db import Database, utcnow

STATES = [
    "DETECTED",
    "INVESTIGATING",
    "CONTAINMENT_REQUIRED",
    "CONTAINED",
    "REPAIRING",
    "VALIDATING",
    "PR_READY",
    "PR_OPENED",
    "RECOVERY_VERIFIED",
    "NEEDS_HUMAN_APPROVAL",
    "INSUFFICIENT_EVIDENCE",
    "REPAIR_REJECTED",
    "RECOVERY_FAILED",
    "EXTERNAL_DEPENDENCY_FAILURE",
    "CLOSED_WITHOUT_REPAIR",
]

TERMINAL = {
    "RECOVERY_VERIFIED",
    "REPAIR_REJECTED",
    "INSUFFICIENT_EVIDENCE",
    "EXTERNAL_DEPENDENCY_FAILURE",
    "CLOSED_WITHOUT_REPAIR",
    "RECOVERY_FAILED",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "DETECTED": {
        "INVESTIGATING", "CONTAINMENT_REQUIRED", "CONTAINED",
        "REPAIRING", "VALIDATING", "PR_OPENED", "RECOVERY_VERIFIED",
        "INSUFFICIENT_EVIDENCE", "CLOSED_WITHOUT_REPAIR",
    },
    "INVESTIGATING": {
        "CONTAINMENT_REQUIRED", "CONTAINED", "REPAIRING", "VALIDATING",
        "PR_OPENED", "NEEDS_HUMAN_APPROVAL", "INSUFFICIENT_EVIDENCE",
        "RECOVERY_VERIFIED", "CLOSED_WITHOUT_REPAIR",
    },
    "CONTAINMENT_REQUIRED": {"CONTAINED", "RECOVERY_FAILED"},
    "CONTAINED": {"REPAIRING", "VALIDATING", "PR_OPENED", "RECOVERY_VERIFIED", "RECOVERY_FAILED",
                  "CLOSED_WITHOUT_REPAIR"},
    "REPAIRING": {"VALIDATING", "PR_READY", "PR_OPENED", "REPAIR_REJECTED", "RECOVERY_FAILED"},
    "VALIDATING": {"PR_READY", "PR_OPENED", "REPAIR_REJECTED", "RECOVERY_FAILED"},
    "PR_READY": {"PR_OPENED", "REPAIR_REJECTED"},
    "PR_OPENED": {"RECOVERY_VERIFIED", "RECOVERY_FAILED"},
    "NEEDS_HUMAN_APPROVAL": {"REPAIRING", "CLOSED_WITHOUT_REPAIR"},
    "INSUFFICIENT_EVIDENCE": {"INVESTIGATING", "CLOSED_WITHOUT_REPAIR"},
    "REPAIR_REJECTED": {"REPAIRING", "CLOSED_WITHOUT_REPAIR"},
    "RECOVERY_FAILED": {"INVESTIGATING", "CONTAINMENT_REQUIRED",
                        "CLOSED_WITHOUT_REPAIR"},
    "EXTERNAL_DEPENDENCY_FAILURE": {"INVESTIGATING", "CLOSED_WITHOUT_REPAIR"},
    "RECOVERY_VERIFIED": set(),
    "CLOSED_WITHOUT_REPAIR": set(),
}


def new_incident_id(ts: str | None = None) -> str:
    stamp = (ts or utcnow())[:23].replace(":", "").replace("-", "")
    return f"INC-{stamp}"


class IncidentStore:
    def __init__(self, db: Database):
        self.db = db

    # -- creation ---------------------------------------------------------
    def create(
        self,
        category: str,
        severity: str,
        title: str,
        summary: str | None = None,
        host_id: str | None = None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        iid = new_incident_id()
        now = utcnow()
        self.db.execute(
            "INSERT INTO incidents(id, host_id, created_at, updated_at, state,"
            " severity, category, title, summary, evidence, lifecycle)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (iid, host_id, now, now, "DETECTED", severity, category, title,
             summary, json.dumps(evidence or []),
             json.dumps([{"ts": now, "state": "DETECTED", "actor": "detector",
                          "note": summary or title}])),
        )
        return self.get(iid)  # type: ignore[return-value]

    # -- mutations --------------------------------------------------------
    def transition(self, incident_id: str, new_state: str, actor: str,
                   note: str | None = None) -> dict[str, Any]:
        if new_state not in STATES:
            raise ValueError(f"Unknown incident state '{new_state}'")
        row = self.get(incident_id)
        if row is None:
            raise KeyError(f"Incident '{incident_id}' not found")
        current = row["state"]
        if new_state == current:
            return row
        if new_state not in ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"Illegal incident transition {current} -> {new_state}"
            )
        now = utcnow()
        lifecycle = row["lifecycle"] if isinstance(row["lifecycle"], list) else json.loads(row["lifecycle"] or "[]")
        lifecycle.append(
            {"ts": now, "from": current, "state": new_state,
             "actor": actor, "note": note})
        closed_at = now if new_state in TERMINAL else None
        self.db.execute(
            "UPDATE incidents SET state=?, updated_at=?, lifecycle=?,"
            " closed_at=COALESCE(?, closed_at) WHERE id=?",
            (new_state, now, json.dumps(lifecycle), closed_at, incident_id),
        )
        return self.get(incident_id)  # type: ignore[return-value]

    def append_evidence(self, incident_id: str, item: dict[str, Any]) -> None:
        row = self.get(incident_id)
        if row is None:
            raise KeyError(f"Incident '{incident_id}' not found")
        evidence = row["evidence"] if isinstance(row["evidence"], list) else json.loads(row["evidence"] or "[]")
        evidence.append(item)
        self.db.execute(
            "UPDATE incidents SET evidence=?, updated_at=? WHERE id=?",
            (json.dumps(evidence), utcnow(), incident_id),
        )

    # -- queries ----------------------------------------------------------
    def get(self, incident_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM incidents WHERE id=?", (incident_id,))
        return _hydrate(row) if row else None

    def active_by_category(self, category: str) -> dict[str, Any] | None:
        rows = self.db.query(
            "SELECT * FROM incidents WHERE category=? AND closed_at IS NULL"
            " ORDER BY created_at DESC", (category,))
        return _hydrate(rows[0]) if rows else None

    def list(self, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if state:
            rows = self.db.query(
                "SELECT * FROM incidents WHERE state=? ORDER BY created_at"
                " DESC LIMIT ?", (state, limit))
        else:
            rows = self.db.query(
                "SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?",
                (limit,))
        return [_hydrate(r) for r in rows]


def _hydrate(row) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    inc = dict(row)
    for col in ("evidence", "lifecycle"):
        try:
            inc[col] = json.loads(inc[col] or "[]")
        except json.JSONDecodeError:
            inc[col] = []
    return inc
