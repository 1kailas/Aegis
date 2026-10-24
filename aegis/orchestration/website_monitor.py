"""Website & Production App Target Monitor for Aegis AI.

Probes registered websites, web applications, and plain HTTP/S services.
Tracks SLA latency, consecutive failures, and HTTP 5xx errors.
Automatically triggers incident detection and end-to-end autonomous triage.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

import httpx

from aegis.db import Database, utcnow
from aegis.events import EventBus

_LOG = logging.getLogger("aegis.orchestration.website_monitor")


class WebsiteMonitor:
    """Monitors any plain website or web application over HTTP/S."""

    def __init__(self, db: Database, incidents: Any, events: EventBus):
        self.db = db
        self.incidents = incidents
        self.events = events

    def add_target(
        self,
        name: str,
        url: str,
        health_path: str = "/health",
        github_repo_url: str = "",
        log_source: str = "",
        interval_s: float = 5.0,
    ) -> dict[str, Any]:
        """Registers a new website target and performs an immediate probe."""
        target_id = f"tgt-{secrets.token_hex(4)}"
        clean_url = url.strip().rstrip("/")
        if not clean_url.startswith(("http://", "https://")):
            clean_url = f"http://{clean_url}"

        clean_path = health_path.strip()
        if not clean_path.startswith("/"):
            clean_path = f"/{clean_path}"

        now = utcnow()
        self.db.execute(
            "INSERT INTO monitored_targets(id, name, url, health_path, github_repo_url,"
            " log_source, interval_s, consecutive_failures, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,0,'ACTIVE',?)",
            (
                target_id,
                name.strip(),
                clean_url,
                clean_path,
                github_repo_url.strip(),
                log_source.strip(),
                float(interval_s),
                now,
            ),
        )
        self.events.emit(
            "target_monitor",
            f"Added monitored target '{name}' ({clean_url}{clean_path})",
            "info",
            target_id=target_id,
            url=clean_url,
        )

        # Run immediate probe
        res = self.probe_target(target_id)
        target = self.get_target(target_id)
        return target or res

    def get_target(self, target_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM monitored_targets WHERE id=?", (target_id,))
        return dict(row) if row else None

    def list_targets(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM monitored_targets ORDER BY created_at DESC")
        return [dict(r) for r in rows]

    def delete_target(self, target_id: str) -> bool:
        row = self.get_target(target_id)
        if not row:
            return False
        self.db.execute("DELETE FROM monitored_targets WHERE id=?", (target_id,))
        self.events.emit(
            "target_monitor",
            f"Removed monitored target '{row['name']}' ({target_id})",
            "info",
            target_id=target_id,
        )
        return True

    def probe_target(self, target_id: str) -> dict[str, Any]:
        """Executes a live probe against a registered target."""
        target = self.get_target(target_id)
        if not target:
            return {"error": "Target not found", "target_id": target_id}

        probe_url = f"{target['url']}{target['health_path']}"
        start_t = time.perf_counter()
        status_code: int | None = None
        error_msg: str | None = None
        is_healthy = False

        try:
            with httpx.Client(timeout=5.0, follow_redirects=True) as client:
                resp = client.get(probe_url)
                duration_ms = (time.perf_counter() - start_t) * 1000.0
                status_code = resp.status_code
                if 200 <= status_code < 300:
                    is_healthy = True
                else:
                    error_msg = f"HTTP {status_code}: {resp.text[:300].strip()}"
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            error_msg = str(exc)

        now = utcnow()
        failures = 0 if is_healthy else (target.get("consecutive_failures", 0) + 1)

        self.db.execute(
            "UPDATE monitored_targets SET consecutive_failures=?, last_status_code=?,"
            " last_latency_ms=?, last_error=?, last_checked_at=? WHERE id=?",
            (
                failures,
                status_code,
                round(duration_ms, 2),
                error_msg,
                now,
                target_id,
            ),
        )

        # Incident correlation & dispatch
        category = f"target_outage_{target_id}"
        if not is_healthy and failures >= 2:
            active = self.incidents.active_by_category(category)
            evidence_item = {
                "kind": "website_health_probe",
                "target_id": target_id,
                "target_name": target["name"],
                "probe_url": probe_url,
                "status_code": status_code,
                "latency_ms": round(duration_ms, 2),
                "error": error_msg,
                "github_repo_url": target.get("github_repo_url", ""),
                "log_source": target.get("log_source", ""),
                "consecutive_failures": failures,
                "ts": now,
            }
            if not active:
                new_inc = self.incidents.create(
                    category=category,
                    severity="critical",
                    title=f"Target Outage: {target['name']}",
                    summary=(
                        f"Target '{target['name']}' failing health checks on {probe_url} "
                        f"(Status: {status_code or 'ERR'}, Latency: {duration_ms:.1f}ms). {error_msg or ''}"
                    ),
                    evidence=[evidence_item],
                )
                self.events.emit(
                    "target_outage",
                    f"Outage detected on {target['name']} ({probe_url}): {error_msg or status_code}",
                    "critical",
                    incident_id=new_inc["id"],
                    target_id=target_id,
                )
            else:
                self.incidents.append_evidence(active["id"], evidence_item)
        elif is_healthy and target.get("consecutive_failures", 0) > 0:
            active = self.incidents.active_by_category(category)
            if active and active.get("state") in ("DETECTED", "INVESTIGATING", "CONTAINED", "PR_OPENED"):
                self.incidents.append_evidence(
                    active["id"],
                    {
                        "kind": "recovery_confirmation",
                        "target_id": target_id,
                        "probe_url": probe_url,
                        "status_code": status_code,
                        "latency_ms": round(duration_ms, 2),
                        "note": "Target health probe returned 200 OK",
                        "ts": now,
                    },
                )

        return {
            "target_id": target_id,
            "target_name": target["name"],
            "probe_url": probe_url,
            "healthy": is_healthy,
            "status_code": status_code,
            "latency_ms": round(duration_ms, 2),
            "consecutive_failures": failures,
            "error": error_msg,
            "checked_at": now,
        }

    def probe_all(self) -> list[dict[str, Any]]:
        """Probes all active registered targets."""
        targets = self.list_targets()
        results = []
        for t in targets:
            if t.get("status") == "ACTIVE":
                try:
                    res = self.probe_target(t["id"])
                    results.append(res)
                except Exception as e:
                    _LOG.debug("Error probing target %s: %s", t.get("id"), e)
        return results
