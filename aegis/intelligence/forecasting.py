"""Predictive telemetry forecasting and Time-To-Exhaustion (TTE) engine.

Uses online linear regression and rate-of-change estimation over rolling
telemetry windows to forecast storage, memory, and resource exhaustion before
physical capacity thresholds are breached.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.intelligence.forecasting")


@dataclass
class ForecastResult:
    metric_name: str
    current_value: float
    capacity: float
    unit: str
    rate_per_sec: float
    tte_seconds: float | None
    tte_human: str
    severity: str  # NOMINAL, WARNING, CRITICAL
    is_exhaustion_imminent: bool
    confidence: float
    sample_count: int
    calculated_at: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "capacity": self.capacity,
            "unit": self.unit,
            "rate_per_sec": round(self.rate_per_sec, 4),
            "tte_seconds": round(self.tte_seconds, 1) if self.tte_seconds is not None else None,
            "tte_human": self.tte_human,
            "severity": self.severity,
            "is_exhaustion_imminent": self.is_exhaustion_imminent,
            "confidence": round(self.confidence, 2),
            "sample_count": self.sample_count,
            "calculated_at": self.calculated_at,
        }


class TelemetryForecaster:
    """Calculates online trend slopes and Time-To-Exhaustion for resources."""

    def __init__(
        self,
        warning_tte_seconds: float = 3600.0,    # 1 hour
        critical_tte_seconds: float = 900.0,   # 15 minutes
        min_samples_for_prediction: int = 5,
        history_window_seconds: float = 600.0,  # 10 minutes
    ):
        self.warning_tte_seconds = warning_tte_seconds
        self.critical_tte_seconds = critical_tte_seconds
        self.min_samples_for_prediction = min_samples_for_prediction
        self.history_window_seconds = history_window_seconds
        # series: dict[metric_name, list[tuple[timestamp, value]]]
        self._history: dict[str, list[tuple[float, float]]] = {}

    def feed(self, metric_name: str, value: float, ts: float | None = None) -> None:
        """Appends a new reading to the rolling series."""
        ts = time.time() if ts is None else ts
        series = self._history.setdefault(metric_name, [])
        series.append((ts, float(value)))

        # Prune old samples
        cutoff = ts - self.history_window_seconds
        self._history[metric_name] = [pt for pt in series if pt[0] >= cutoff]

    def forecast_exhaustion(
        self,
        metric_name: str,
        capacity: float,
        unit: str = "%",
        current_value: float | None = None,
    ) -> ForecastResult:
        """Projects Time-To-Exhaustion (TTE) to reach capacity."""
        series = self._history.get(metric_name, [])
        count = len(series)

        curr = current_value if current_value is not None else (series[-1][1] if series else 0.0)

        if count < self.min_samples_for_prediction:
            return ForecastResult(
                metric_name=metric_name,
                current_value=curr,
                capacity=capacity,
                unit=unit,
                rate_per_sec=0.0,
                tte_seconds=None,
                tte_human="Collecting baseline data...",
                severity="NOMINAL",
                is_exhaustion_imminent=False,
                confidence=0.1,
                sample_count=count,
            )

        # Compute OLS linear regression slope: rate = cov(t, y) / var(t)
        t_vals = [pt[0] for pt in series]
        y_vals = [pt[1] for pt in series]

        t_mean = sum(t_vals) / count
        y_mean = sum(y_vals) / count

        numerator = sum((t - t_mean) * (y - y_mean) for t, y in zip(t_vals, y_vals))
        denominator = sum((t - t_mean) ** 2 for t in t_vals)

        slope = numerator / denominator if denominator > 0 else 0.0
        time_span = max(1.0, t_vals[-1] - t_vals[0])

        # Confidence scales with sample count
        confidence = min(1.0, count / (self.min_samples_for_prediction * 1.5))

        # If slope is positive and current < capacity, calculate TTE
        remaining = capacity - curr
        if slope > 0.0001 and remaining > 0:
            tte_s = remaining / slope
            tte_human = self._format_tte(tte_s)

            if tte_s <= self.critical_tte_seconds and confidence >= 0.5:
                severity = "CRITICAL"
                imminent = True
            elif tte_s <= self.warning_tte_seconds and confidence >= 0.4:
                severity = "WARNING"
                imminent = True
            else:
                severity = "NOMINAL"
                imminent = False
        else:
            tte_s = None
            tte_human = "Stable / Nominal" if slope <= 0.0001 else "Exhausted"
            severity = "CRITICAL" if curr >= capacity else "NOMINAL"
            imminent = curr >= capacity

        return ForecastResult(
            metric_name=metric_name,
            current_value=curr,
            capacity=capacity,
            unit=unit,
            rate_per_sec=slope,
            tte_seconds=tte_s,
            tte_human=tte_human,
            severity=severity,
            is_exhaustion_imminent=imminent,
            confidence=confidence,
            sample_count=count,
        )

    @staticmethod
    def _format_tte(seconds: float) -> str:
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins} min"
        elif seconds < 86400:
            hrs = round(seconds / 3600, 1)
            return f"{hrs} hrs"
        else:
            days = round(seconds / 86400, 1)
            return f"{days} days"
