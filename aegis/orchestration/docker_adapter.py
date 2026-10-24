"""Docker orchestration adapter for container discovery, telemetry, and recovery.

Discovers running and stopped containers, resource utilization, OOM kills,
crash loops, and health check states. Policy-controlled restarts are executed
only for authorized, non-protected workloads with cooldown tracking.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.orchestration.docker")

SECRET_KEY_PATTERN = re.compile(
    r"(token|key|secret|password|auth|credential|api_key|priv)", re.IGNORECASE
)


@dataclass
class ContainerMetrics:
    cpu_percent: float = 0.0
    memory_usage_mb: float = 0.0
    memory_limit_mb: float = 0.0
    memory_percent: float = 0.0
    net_rx_bytes: int = 0
    net_tx_bytes: int = 0
    block_read_bytes: int = 0
    block_write_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "memory_usage_mb": self.memory_usage_mb,
            "memory_limit_mb": self.memory_limit_mb,
            "memory_percent": self.memory_percent,
            "net_rx_bytes": self.net_rx_bytes,
            "net_tx_bytes": self.net_tx_bytes,
            "block_read_bytes": self.block_read_bytes,
            "block_write_bytes": self.block_write_bytes,
        }


@dataclass
class ContainerInfo:
    id: str
    short_id: str
    name: str
    image: str
    status: str  # running, exited, paused, restarting, dead
    health: str  # healthy, unhealthy, starting, none
    exit_code: int | None
    oom_killed: bool
    restart_count: int
    created_at: str
    started_at: str | None
    networks: list[str] = field(default_factory=list)
    ip_addresses: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    service_name: str | None = None
    project_name: str | None = None
    metrics: ContainerMetrics | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "short_id": self.short_id,
            "name": self.name,
            "image": self.image,
            "status": self.status,
            "health": self.health,
            "exit_code": self.exit_code,
            "oom_killed": self.oom_killed,
            "restart_count": self.restart_count,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "networks": self.networks,
            "ip_addresses": self.ip_addresses,
            "labels": self.labels,
            "service_name": self.service_name,
            "project_name": self.project_name,
            "metrics": self.metrics.as_dict() if self.metrics else None,
        }


class DockerAdapter:
    """Manages Docker discovery, inspection, and safe operations."""

    def __init__(self, protected_containers: set[str] | None = None):
        self.protected_containers = protected_containers or {
            "aegis", "aegis-engine", "kube-system", "docker-desktop"
        }
        self._client = None
        self._available = False
        self._restart_history: dict[str, list[float]] = {}
        self._connect()

    def _connect(self) -> None:
        try:
            import docker
            self._client = docker.from_env(timeout=3)
            self._client.ping()
            self._available = True
        except Exception as e:
            _LOG.debug("Docker daemon not reachable: %s", e)
            self._client = None
            self._available = False

    def is_available(self) -> bool:
        if not self._available or not self._client:
            self._connect()
        return self._available

    def sample(self) -> dict[str, Any]:
        """Discovers containers and inspects their health and metrics."""
        if not self.is_available():
            return {
                "available": False,
                "reason": "Docker daemon unavailable or socket access not permitted",
                "containers": [],
                "incidents": [],
                "collected_at": utcnow(),
            }

        containers: list[ContainerInfo] = []
        incidents: list[dict[str, Any]] = []

        try:
            raw_containers = self._client.containers.list(all=True)
            for c in raw_containers:
                info = self._inspect_container(c)
                containers.append(info)

                # Anomaly & Incident Detection
                detected = self._detect_incidents(info)
                incidents.extend(detected)

            return {
                "available": True,
                "reason": None,
                "total_containers": len(containers),
                "running_count": sum(1 for c in containers if c.status == "running"),
                "containers": [c.as_dict() for c in containers],
                "incidents": incidents,
                "collected_at": utcnow(),
            }
        except Exception as e:
            _LOG.warning("Docker sample failed: %s", e)
            self._available = False
            return {
                "available": False,
                "reason": f"Docker error: {e}",
                "containers": [],
                "incidents": [],
                "collected_at": utcnow(),
            }

    def _inspect_container(self, c: Any) -> ContainerInfo:
        attrs = c.attrs or {}
        state = attrs.get("State") or {}
        config = attrs.get("Config") or {}
        labels = config.get("Labels") or {}

        # Health
        health_status = "none"
        if "Health" in state:
            health_status = state["Health"].get("Status", "none")

        # Networks
        networks = []
        ips = []
        net_settings = attrs.get("NetworkSettings") or {}
        for net_name, net_info in (net_settings.get("Networks") or {}).items():
            networks.append(net_name)
            ip = net_info.get("IPAddress")
            if ip:
                ips.append(ip)

        metrics = None
        if c.status == "running":
            metrics = self._sample_container_stats(c)

        return ContainerInfo(
            id=c.id,
            short_id=c.short_id,
            name=c.name.lstrip("/"),
            image=attrs.get("Config", {}).get("Image", str(c.image)),
            status=c.status,
            health=health_status,
            exit_code=state.get("ExitCode"),
            oom_killed=bool(state.get("OOMKilled")),
            restart_count=attrs.get("RestartCount", 0),
            created_at=attrs.get("Created", utcnow()),
            started_at=state.get("StartedAt"),
            networks=networks,
            ip_addresses=ips,
            labels=labels,
            service_name=labels.get("com.docker.compose.service"),
            project_name=labels.get("com.docker.compose.project"),
            metrics=metrics,
        )

    def _sample_container_stats(self, c: Any) -> ContainerMetrics | None:
        try:
            stats = c.stats(stream=False)
            if not stats:
                return None

            # Calculate CPU %
            cpu_pct = 0.0
            cpu_stats = stats.get("cpu_stats", {})
            precpu_stats = stats.get("precpu_stats", {})
            cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
            system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
            online_cpus = cpu_stats.get("online_cpus", 1)
            if system_delta > 0 and cpu_delta > 0:
                cpu_pct = round((cpu_delta / system_delta) * online_cpus * 100.0, 1)

            # Memory
            mem_stats = stats.get("memory_stats", {})
            mem_usage = mem_stats.get("usage", 0)
            mem_limit = mem_stats.get("limit", 1)
            mem_pct = round((mem_usage / mem_limit) * 100.0, 1) if mem_limit > 0 else 0.0

            # Network
            rx_bytes = 0
            tx_bytes = 0
            for _, net in (stats.get("networks") or {}).items():
                rx_bytes += net.get("rx_bytes", 0)
                tx_bytes += net.get("tx_bytes", 0)

            # Block IO
            blk_read = 0
            blk_write = 0
            for entry in (stats.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []:
                op = entry.get("op", "").lower()
                val = entry.get("value", 0)
                if op == "read":
                    blk_read += val
                elif op == "write":
                    blk_write += val

            return ContainerMetrics(
                cpu_percent=cpu_pct,
                memory_usage_mb=round(mem_usage / (1024 * 1024), 1),
                memory_limit_mb=round(mem_limit / (1024 * 1024), 1),
                memory_percent=mem_pct,
                net_rx_bytes=rx_bytes,
                net_tx_bytes=tx_bytes,
                block_read_bytes=blk_read,
                block_write_bytes=blk_write,
            )
        except Exception:
            return None

    def _detect_incidents(self, info: ContainerInfo) -> list[dict[str, Any]]:
        incidents: list[dict[str, Any]] = []

        # 1. OOM Killed
        if info.oom_killed or info.exit_code == 137:
            incidents.append({
                "type": "container_oom_killed",
                "severity": "critical",
                "container_id": info.id,
                "container_name": info.name,
                "summary": f"Container {info.name} was OOMKilled (exit code {info.exit_code})",
                "evidence": {"oom_killed": True, "exit_code": info.exit_code, "metrics": info.metrics.as_dict() if info.metrics else None},
            })

        # 2. CrashLoop or repeated crash
        if info.restart_count >= 3 and info.status != "running":
            incidents.append({
                "type": "container_crash_loop",
                "severity": "critical",
                "container_id": info.id,
                "container_name": info.name,
                "summary": f"Container {info.name} is in a crash loop (restart count: {info.restart_count}, exit code: {info.exit_code})",
                "evidence": {"restart_count": info.restart_count, "exit_code": info.exit_code, "status": info.status},
            })

        # 3. Unhealthy container
        if info.health == "unhealthy":
            incidents.append({
                "type": "container_unhealthy",
                "severity": "warning",
                "container_id": info.id,
                "container_name": info.name,
                "summary": f"Container {info.name} health probe failed (status: unhealthy)",
                "evidence": {"health": info.health, "status": info.status},
            })

        # 4. Resource exhaustion
        if info.metrics:
            if info.metrics.memory_percent >= 90.0:
                incidents.append({
                    "type": "container_memory_saturation",
                    "severity": "warning",
                    "container_id": info.id,
                    "container_name": info.name,
                    "summary": f"Container {info.name} memory saturation: {info.metrics.memory_percent}% ({info.metrics.memory_usage_mb}MB / {info.metrics.memory_limit_mb}MB)",
                    "evidence": info.metrics.as_dict(),
                })
            if info.metrics.cpu_percent >= 95.0:
                incidents.append({
                    "type": "container_cpu_saturation",
                    "severity": "warning",
                    "container_id": info.id,
                    "container_name": info.name,
                    "summary": f"Container {info.name} CPU saturation: {info.metrics.cpu_percent}%",
                    "evidence": info.metrics.as_dict(),
                })

        return incidents

    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """Retrieves and sanitizes container logs."""
        if not self.is_available():
            return "Docker unavailable"
        try:
            c = self._client.containers.get(container_id)
            raw = c.logs(tail=tail, stdout=True, stderr=True).decode("utf-8", errors="replace")
            return self._sanitize_logs(raw)
        except Exception as e:
            return f"Failed to retrieve logs: {e}"

    def _sanitize_logs(self, text: str) -> str:
        """Redacts tokens, credentials, and sensitive environment keys."""
        # Redact common token and credential patterns
        sanitized = re.sub(r"(AIzaSy[A-Za-z0-9_-]{33})", "[REDACTED_GEMINI_KEY]", text)
        sanitized = re.sub(r"(gsk_[A-Za-z0-9_-]{30,})", "[REDACTED_GROQ_KEY]", sanitized)
        sanitized = re.sub(r"(gh[pousr]_[A-Za-z0-9_]{30,})", "[REDACTED_GITHUB_TOKEN]", sanitized)
        sanitized = re.sub(r"(bearer\s+)[A-Za-z0-9\._\-]+", r"\1[REDACTED_BEARER]", sanitized, flags=re.I)
        return sanitized

    def restart_container(self, container_id: str, mode: str = "dry_run") -> dict[str, Any]:
        """Safely restarts an authorized container under policy control."""
        if not self.is_available():
            return {"result": "FAILED", "reason": "Docker daemon unavailable"}

        try:
            c = self._client.containers.get(container_id)
            name = c.name.lstrip("/")

            # Check protected containers
            if name in self.protected_containers:
                return {
                    "result": "REJECTED",
                    "reason": f"Container '{name}' is in protected allowlist; autonomous restart prohibited",
                }

            # Check cooldown / restart limits (max 3 restarts in 10 minutes)
            now = time.time()
            history = self._restart_history.setdefault(container_id, [])
            history = [t for t in history if now - t <= 600]
            self._restart_history[container_id] = history

            if len(history) >= 3:
                return {
                    "result": "BLOCKED",
                    "reason": f"Rate limit reached: container '{name}' restarted {len(history)} times in 10 minutes",
                }

            if mode == "dry_run":
                return {
                    "result": "VERIFIED",
                    "mode": "dry_run",
                    "container_name": name,
                    "reason": "Dry-run policy evaluation passed: container is eligible for restart",
                }

            # Real execution
            history.append(now)
            c.restart(timeout=10)
            time.sleep(1.0)
            c.reload()

            post_status = c.status
            verified = post_status == "running"
            return {
                "result": "VERIFIED" if verified else "FAILED",
                "mode": "real",
                "container_name": name,
                "post_status": post_status,
                "verified": verified,
            }
        except Exception as e:
            return {"result": "FAILED", "reason": f"Restart exception: {e}"}
