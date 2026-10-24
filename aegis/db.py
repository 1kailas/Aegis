"""SQLite persistence layer.

Single connection in WAL mode shared across threads, guarded by a lock.
Aegis is single-process by design; this keeps transactional incident/state
transitions and job claiming correct without external services.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self.migrate()

    # -- core ------------------------------------------------------------
    @contextmanager
    def tx(self, write: bool = True):
        """Transaction context; commits on success, rolls back on error."""
        with self._lock:
            try:
                yield self._conn
                if write:
                    self._conn.commit()
            except Exception:
                if write:
                    self._conn.rollback()
                raise

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.tx() as conn:
            return conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.tx(write=False) as conn:
            return conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.tx(write=False) as conn:
            return conn.execute(sql, tuple(params)).fetchone()

    # -- migrations ------------------------------------------------------
    def migrate(self) -> list[int]:
        applied: list[int] = []
        with self.tx() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
            ).fetchone()[0]
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            m = re.match(r"(\d+)_", path.name)
            if not m:
                continue
            version = int(m.group(1))
            if version <= current:
                continue
            sql_text = path.read_text(encoding="utf-8")
            with self.tx() as conn:
                conn.executescript(sql_text)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(?,?)",
                    (version, utcnow()),
                )
            applied.append(version)
        return applied

    # -- app state helpers -------------------------------------------------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM app_state WHERE key=?", (key,))
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO app_state(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " updated_at=excluded.updated_at",
            (key, value, utcnow()),
        )

    # -- telemetry ---------------------------------------------------------
    def record_telemetry(
        self,
        samples: Iterable[tuple[str, float | int | None, str | None]],
        host_id: str,
        source: str = "psutil",
    ) -> int:
        rows = [
            (utcnow(), host_id, name, None if v is None else float(v), unit, source)
            for name, v, unit in samples
        ]
        if not rows:
            return 0
        with self.tx() as conn:
            conn.executemany(
                "INSERT INTO telemetry(ts, host_id, metric, value, unit, source)"
                " VALUES(?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def record_snapshot(self, host_id: str, payload: dict[str, Any], validity: str) -> None:
        self.execute(
            "INSERT INTO metric_snapshots(ts, host_id, payload, validity)"
            " VALUES(?,?,?,?)",
            (utcnow(), host_id, json.dumps(payload, default=str), validity),
        )

    def prune_telemetry(self, retention_hours: int) -> int:
        """Delete metric history older than the retention horizon."""
        cutoff_epoch = datetime.now(timezone.utc).timestamp() - (retention_hours * 3600)
        cutoff = datetime.fromtimestamp(cutoff_epoch, timezone.utc).isoformat(
            timespec="milliseconds")
        with self.tx() as conn:
            cur1 = conn.execute("DELETE FROM telemetry WHERE ts <= ?", (cutoff,))
            c1 = cur1.rowcount
            cur2 = conn.execute("DELETE FROM metric_snapshots WHERE ts <= ?", (cutoff,))
            return c1 + cur2.rowcount

    def db_integrity(self) -> str:
        row = self.query_one("PRAGMA integrity_check")
        return row[0] if row else "unknown"

    def list_metrics(self) -> list[str]:
        rows = self.query("SELECT DISTINCT metric FROM telemetry ORDER BY metric ASC")
        return [r["metric"] for r in rows]

    def query_telemetry(
        self,
        metric: str | None = None,
        start_ts: str | None = None,
        end_ts: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = []
        params = []
        if metric:
            clauses.append("metric = ?")
            params.append(metric)
        if start_ts:
            clauses.append("ts >= ?")
            params.append(start_ts)
        if end_ts:
            clauses.append("ts <= ?")
            params.append(end_ts)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT ts, host_id, metric, value, unit, source FROM telemetry {where} ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self.query(sql, params)
        return [dict(r) for r in reversed(rows)]

    def query_telemetry_aggregated(
        self,
        metric: str,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        """Aggregates telemetry points into recent buckets with min, max, avg."""
        sql = """
            SELECT
                SUBSTR(ts, 1, 16) AS time_bucket,
                metric,
                COUNT(*) as count,
                ROUND(AVG(value), 2) as avg_value,
                ROUND(MIN(value), 2) as min_value,
                ROUND(MAX(value), 2) as max_value,
                MAX(unit) as unit
            FROM telemetry
            WHERE metric = ? AND value IS NOT NULL
            GROUP BY time_bucket, metric
            ORDER BY time_bucket DESC
            LIMIT ?
        """
        rows = self.query(sql, (metric, limit))
        return [dict(r) for r in reversed(rows)]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()
