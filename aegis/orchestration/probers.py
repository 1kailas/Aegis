"""Synthetic service probers and canary latency SLA monitors.

Runs continuous HTTP and TCP health probes to detect latency degradation, SLA breaches,
and endpoint failures before downstream dependent systems encounter errors.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.orchestration.probers")


@dataclass
class ProbeTarget:
    name: str
    target_type: str  # http | tcp
    endpoint: str     # url or "host:port"
    sla_latency_ms: float = 500.0
    timeout_s: float = 5.0
    expected_status: int = 200
    expected_substring: str = ""
    enabled: bool = True


@dataclass
class ProbeResult:
    target_name: str
    success: bool
    latency_ms: float
    sla_passed: bool
    status_code: int | None
    error: str | None = None
    timestamp: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_name": self.target_name,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
            "sla_passed": self.sla_passed,
            "status_code": self.status_code,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class SyntheticProber:
    """Executes synthetic health and latency SLA probes across targets."""

    def __init__(self, targets: list[ProbeTarget] | None = None):
        self.targets: dict[str, ProbeTarget] = {t.name: t for t in (targets or [])}
        self._history: dict[str, list[ProbeResult]] = {}
        self._consecutive_failures: dict[str, int] = {}

    def add_target(self, target: ProbeTarget) -> None:
        self.targets[target.name] = target

    def probe_all(self) -> list[ProbeResult]:
        """Executes all registered probes and updates history."""
        results: list[ProbeResult] = []
        for name, t in self.targets.items():
            if not t.enabled:
                continue
            res = self._probe_single(t)
            results.append(res)
            # Store in history
            hist = self._history.setdefault(name, [])
            hist.append(res)
            if len(hist) > 100:
                self._history[name] = hist[-100:]

            # Consecutive failures tracking
            if not res.success:
                self._consecutive_failures[name] = self._consecutive_failures.get(name, 0) + 1
            else:
                self._consecutive_failures[name] = 0

        return results

    def _probe_single(self, target: ProbeTarget) -> ProbeResult:
        start_t = time.perf_counter()
        if target.target_type == "http":
            try:
                with httpx.Client(timeout=target.timeout_s, follow_redirects=True) as client:
                    resp = client.get(target.endpoint)
                    dur_ms = (time.perf_counter() - start_t) * 1000.0
                    status_ok = resp.status_code == target.expected_status
                    body_ok = (not target.expected_substring) or (target.expected_substring in resp.text)
                    success = status_ok and body_ok
                    sla_ok = dur_ms <= target.sla_latency_ms
                    err = None if success else f"Status: {resp.status_code} (expected {target.expected_status})"
                    return ProbeResult(
                        target_name=target.name,
                        success=success,
                        latency_ms=dur_ms,
                        sla_passed=sla_ok,
                        status_code=resp.status_code,
                        error=err,
                    )
            except Exception as e:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return ProbeResult(
                    target_name=target.name,
                    success=False,
                    latency_ms=dur_ms,
                    sla_passed=False,
                    status_code=None,
                    error=str(e),
                )

        elif target.target_type == "tcp":
            try:
                host, port_str = target.endpoint.split(":")
                port = int(port_str)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(target.timeout_s)
                s.connect((host, port))
                s.close()
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                sla_ok = dur_ms <= target.sla_latency_ms
                return ProbeResult(
                    target_name=target.name,
                    success=True,
                    latency_ms=dur_ms,
                    sla_passed=sla_ok,
                    status_code=None,
                )
            except Exception as e:
                dur_ms = (time.perf_counter() - start_t) * 1000.0
                return ProbeResult(
                    target_name=target.name,
                    success=False,
                    latency_ms=dur_ms,
                    sla_passed=False,
                    status_code=None,
                    error=str(e),
                )

        return ProbeResult(
            target_name=target.name,
            success=False,
            latency_ms=0.0,
            sla_passed=False,
            status_code=None,
            error=f"Unsupported target type: {target.target_type}",
        )

    def get_summary(self) -> dict[str, Any]:
        """Summarizes probe health and latency across targets."""
        summary = {}
        for name, hist in self._history.items():
            if not hist:
                continue
            last = hist[-1]
            success_count = sum(1 for r in hist if r.success)
            uptime_pct = round((success_count / len(hist)) * 100.0, 1)
            avg_latency = round(sum(r.latency_ms for r in hist) / len(hist), 1)
            summary[name] = {
                "healthy": last.success,
                "uptime_pct": uptime_pct,
                "current_latency_ms": round(last.latency_ms, 1),
                "avg_latency_ms": avg_latency,
                "consecutive_failures": self._consecutive_failures.get(name, 0),
                "last_checked": last.timestamp,
            }
        return summary
