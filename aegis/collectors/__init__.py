"""Collector package: hardware discovery, system metrics, sensor overrides.

Public helper: overall_validity classifies a merged temperature reading
set into the sample validity reported on the dashboard and snapshots.
"""

from aegis.collectors.sensor_registry import (
    Reading,
    Sensor,
    SensorRegistry,
    upscale_temperature,
)
from aegis.collectors.system_metrics import MetricsCollector, overall_validity

__all__ = [
    "Reading",
    "Sensor",
    "SensorRegistry",
    "MetricsCollector",
    "overall_validity",
    "upscale_temperature",
]
