"""Audit trail and remediation-action recording.

Every meaningful Aegis decision or side effect lands here:
guardian transitions, remediation attempts (real or dry-run),
policy rejections and manual approvals. The remediation_actions
table stores the outcome; audit_log captures actor/action trails.
"""

from __future__ import annotations

import json
from typing import Any

from aegis.db import Database, utcnow

RESULT_VERIFIED = "VERIFIED"
RESULT_REJECTED = "REJECTED"
RESULT_BLOCKED = "BLOCKED"
RESULT_FAILED = "FAILED"


class Audit:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        actor: str,
        action: str,
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail)"
            " VALUES(?,?,?,?,?)",
            (utcnow(), actor, action, subject,
             json.dumps(detail or {}, default=str)),
        )

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            item = dict(row)
            item["detail"] = _safe_json(item.get("detail"))
            out.append(item)
        return out


class ActionRecorder:
    """Persists remediation attempts with their outcomes."""

    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        action: str,
        target: str | None,
        reason: str | None,
        mode: str,
        incident_id: str | None = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO remediation_actions(ts, incident_id, action, target,"
            " reason, mode, result, detail) VALUES(?,?,?,?,?,?,?,?)",
            (utcnow(), incident_id, action, target, reason, mode,
             "PENDING", None),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def record(
        self,
        action_id: int,
        result: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE remediation_actions SET result=?,"
            " detail=COALESCE(?, detail) WHERE id=?",
            (result, json.dumps(detail or None, default=str)
             if detail else None, action_id),
        )

    def list(
        self,
        limit: int = 100,
        incident_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM remediation_actions"
        params: list[Any] = []
        if incident_id:
            sql += " WHERE incident_id=?"
            params.append(incident_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, tuple(params))
        return [_hydrate_action(r) for r in rows]


def _hydrate_action(row) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    act = dict(row)
    act["detail"] = _safe_json(act.get("detail"))
    return act


def _safe_json(raw: Any) -> Any:  # type: ignore[no-untyped-def]
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
