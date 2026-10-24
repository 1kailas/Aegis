"""Collector package: hardware discovery, system metrics, GPU, processes, storage, inventory.

Public helper: overall_validity classifies a merged temperature reading
set into the sample validity reported on the dashboard and snapshots.
"""

from aegis.collectors.gpu import GpuCollector
from aegis.collectors.hardware import HardwareCollector, HostIdentity, host_identity
from aegis.collectors.inventory import InventoryCollector
from aegis.collectors.processes import ProcessCollector
from aegis.collectors.sensor_registry import (
    Reading,
    Sensor,
    SensorRegistry,
    upscale_temperature,
)
from aegis.collectors.storage import StorageCollector
from aegis.collectors.system_metrics import MetricsCollector, overall_validity

__all__ = [
    "Reading",
    "Sensor",
    "SensorRegistry",
    "MetricsCollector",
    "HardwareCollector",
    "HostIdentity",
    "host_identity",
    "GpuCollector",
    "ProcessCollector",
    "StorageCollector",
    "InventoryCollector",
    "overall_validity",
    "upscale_temperature",
]
