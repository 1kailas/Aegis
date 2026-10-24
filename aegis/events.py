"""Structured event log shared between the engine thread and the API/SSE.

Events are appended to a bounded ring buffer with a monotonically
increasing seq number; SSE consumers follow by seq. Thread-safe.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Event:
    seq: int
    ts: str
    kind: str          # metrics | thermal | incident | action | policy | system ...
    severity: str      # info | warning | critical
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Bounded in-memory event log with SSE-friendly follow-by-seq reads."""

    def __init__(self, capacity: int = 500):
        self._deque: deque[Event] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._next_seq = 1

    def emit(
        self,
        kind: str,
        message: str,
        severity: str = "info",
        **detail: Any,
    ) -> Event:
        with self._lock:
            ev = Event(
                seq=self._next_seq,
                ts=utcnow(),
                kind=kind,
                severity=severity,
                message=message,
                detail=detail,
            )
            self._next_seq += 1
            self._deque.append(ev)
        return ev

    def since(self, seq: int) -> list[Event]:
        with self._lock:
            return [e for e in self._deque if e.seq > seq]

    def latest(self, count: int = 50) -> list[Event]:
        with self._lock:
            items = list(self._deque)
        return items[-count:][::-1]

    def __iter__(self) -> Iterator[Event]:
        with self._lock:
            return iter(list(self._deque))
