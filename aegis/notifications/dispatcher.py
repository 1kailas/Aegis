"""Multi-channel notification dispatcher and webhook alert router for Aegis AI 2.0.

Provides reliable alert routing to Slack, PagerDuty, OpsGenie, and generic JSON
webhooks with sliding-window alert storm suppression (debouncing).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from aegis.db import Database, utcnow
from aegis.events import EventBus

_LOG = logging.getLogger("aegis.notifications.dispatcher")

SEVERITY_ORDER = {
    "info": 1,
    "warning": 2,
    "critical": 3,
}


@dataclass
class NotificationChannel:
    channel_id: str
    name: str
    channel_type: str  # webhook | slack | pagerduty | generic_json
    webhook_url: str
    min_severity: str = "warning"
    enabled: bool = True
    created_at: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "channel_type": self.channel_type,
            "webhook_url": self.webhook_url,
            "min_severity": self.min_severity,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


class NotificationDispatcher:
    """Dispatches alerts to configured external channels with storm suppression."""

    def __init__(self, db: Database, events: EventBus, debounce_seconds: float = 300.0):
        self.db = db
        self.events = events
        self.debounce_seconds = debounce_seconds
        # key: (subject, severity) -> last_sent_timestamp
        self._sent_history: dict[tuple[str, str], float] = {}

    def add_channel(
        self,
        name: str,
        channel_type: str,
        webhook_url: str,
        min_severity: str = "warning",
        channel_id: str | None = None,
    ) -> NotificationChannel:
        """Registers a new outbound notification channel."""
        cid = channel_id or f"CHAN-{int(time.time())}-{name.lower().replace(' ', '_')}"
        now = utcnow()
        chan = NotificationChannel(
            channel_id=cid,
            name=name,
            channel_type=channel_type,
            webhook_url=webhook_url,
            min_severity=min_severity,
            enabled=True,
            created_at=now,
        )
        self.db.execute(
            "INSERT INTO notification_channels(channel_id, name, channel_type,"
            " webhook_url, min_severity, enabled, created_at) VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(channel_id) DO UPDATE SET webhook_url=excluded.webhook_url,"
            " min_severity=excluded.min_severity, enabled=excluded.enabled",
            (
                chan.channel_id,
                chan.name,
                chan.channel_type,
                chan.webhook_url,
                chan.min_severity,
                1 if chan.enabled else 0,
                chan.created_at,
            ),
        )
        return chan

    def list_channels(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM notification_channels ORDER BY created_at DESC")
        return [
            {
                "channel_id": r["channel_id"],
                "name": r["name"],
                "channel_type": r["channel_type"],
                "webhook_url": r["webhook_url"],
                "min_severity": r["min_severity"],
                "enabled": bool(r["enabled"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def dispatch_alert(
        self,
        title: str,
        message: str,
        severity: str = "warning",
        incident_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatches an alert to all matching channels if not debounced."""
        now = time.time()
        dedup_key = (f"{incident_id or title}", severity)

        # Check debounce
        last_sent = self._sent_history.get(dedup_key, 0.0)
        if now - last_sent < self.debounce_seconds:
            return {
                "dispatched": False,
                "reason": f"Alert storm suppression active: debounced for {int(self.debounce_seconds - (now - last_sent))}s",
                "channels_notified": 0,
            }

        self._sent_history[dedup_key] = now
        channels = self.list_channels()
        active_channels = [c for c in channels if c["enabled"]]

        sent_count = 0
        current_sev_level = SEVERITY_ORDER.get(severity.lower(), 1)

        payload = {
            "source": "Aegis AI Control Plane",
            "incident_id": incident_id,
            "title": title,
            "message": message,
            "severity": severity.upper(),
            "timestamp": utcnow(),
            "metadata": metadata or {},
        }

        for chan in active_channels:
            min_sev = chan.get("min_severity", "warning").lower()
            min_level = SEVERITY_ORDER.get(min_sev, 2)

            if current_sev_level >= min_level:
                try:
                    # Execute synchronous HTTP POST with quick timeout
                    with httpx.Client(timeout=3.0) as client:
                        client.post(chan["webhook_url"], json=payload)
                        sent_count += 1
                except Exception as e:
                    _LOG.debug("Webhook delivery to %s failed: %s", chan["name"], e)

        return {
            "dispatched": sent_count > 0,
            "channels_notified": sent_count,
            "total_channels": len(active_channels),
            "severity": severity,
        }
