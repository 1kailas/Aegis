"""Prometheus metrics exporter for Aegis AI infrastructure telemetry.

Generates Prometheus text-format exposition for integration into standard
Prometheus, VictoriaMetrics, and Grafana observability pipelines.
"""

from __future__ import annotations

from typing import Any

STATE_MAP = {
    "NORMAL": 0,
    "WARNING": 1,
    "CRITICAL": 2,
    "RECOVERY": 3,
}


def generate_prometheus_metrics(ctx: Any) -> str:
    """Formats current Aegis telemetry into standard Prometheus text lines."""
    lines: list[str] = []
    host_id = ctx.identity.host_id
    snap = ctx.metrics.latest() or {}

    def add_gauge(name: str, help_text: str, value: float | int | None, labels: dict[str, str] | None = None) -> None:
        if value is None:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        all_labels = {"host_id": host_id, **(labels or {})}
        lbl_str = ",".join(f'{k}="{v}"' for k, v in all_labels.items())
        lines.append(f"{name}{{{lbl_str}}} {value}")

    # Host Vitals
    add_gauge("aegis_cpu_utilization_percent", "Host CPU utilization percentage", snap.get("cpu_percent"))
    mem = snap.get("memory") or {}
    add_gauge("aegis_memory_utilization_percent", "Host RAM utilization percentage", mem.get("percent"))
    add_gauge("aegis_memory_used_bytes", "Host RAM used bytes", (mem.get("used_mb") or 0) * 1024 * 1024)
    add_gauge("aegis_memory_total_bytes", "Host RAM total capacity bytes", (mem.get("total_mb") or 0) * 1024 * 1024)

    # Guardians
    t_state = ctx.guardian.state.state
    add_gauge(
        "aegis_thermal_guardian_state",
        "Thermal Guardian state (0=NORMAL, 1=WARNING, 2=CRITICAL, 3=RECOVERY)",
        STATE_MAP.get(t_state, -1),
    )
    r_state = ctx.resource_guardian.state.state
    add_gauge(
        "aegis_resource_guardian_state",
        "Resource Guardian state (0=NORMAL, 1=WARNING, 2=CRITICAL, 3=RECOVERY)",
        STATE_MAP.get(r_state, -1),
    )

    # System Flags
    add_gauge("aegis_emergency_stop_engaged", "Global emergency stop engaged flag (1=yes, 0=no)", 1 if ctx.flags.emergency_stop else 0)
    add_gauge("aegis_autonomous_mode", "Autonomous execution mode enabled (1=yes, 0=no)", 1 if ctx.flags.mode == "policy_autonomous" else 0)

    # Incidents
    active_incidents = ctx.incidents.active()
    add_gauge("aegis_incidents_active_total", "Number of currently active unresolved incidents", len(active_incidents))
    crit_count = sum(1 for i in active_incidents if i.get("severity") == "critical")
    add_gauge("aegis_incidents_critical_total", "Number of active critical incidents", crit_count)

    # Workloads
    if ctx.docker.is_available():
        docker_sample = ctx.docker.sample()
        add_gauge("aegis_docker_containers_total", "Total registered Docker containers", docker_sample.get("total_containers"))
        add_gauge("aegis_docker_containers_running", "Running Docker containers count", docker_sample.get("running_count"))

    # Temperatures
    for t in snap.get("temperatures", []):
        sid = t.get("sensor_id", "unknown")
        val = t.get("value")
        if val is not None:
            add_gauge("aegis_sensor_temperature_celsius", "Hardware temperature reading in Celsius", val, labels={"sensor_id": sid})

    lines.append("")  # trailing newline required by prometheus
    return "\n".join(lines)
