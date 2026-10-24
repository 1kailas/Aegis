"""Aegis Agent daemon - lightweight host telemetry agent for distributed cluster mesh.

Runs on individual compute nodes or worker hosts. Periodically samples system
telemetry (CPU, memory, disk, network, PSI, GPU, Docker) and reports back to
the Aegis Server control plane via authenticated HTTP heartbeats.
"""

from __future__ import annotations

import argparse
import logging
import os
import platform
import signal
import socket
import sys
import time
from typing import Any

import httpx
import psutil

_LOG = logging.getLogger("aegis.agent")


def get_primary_ip() -> str:
    """Discovers primary local IP address routed outward."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def sample_host_capacity() -> dict[str, Any]:
    """Inspects physical capacity of this host."""
    vm = psutil.virtual_memory()
    cap = {
        "cpu_cores": os.cpu_count() or 1,
        "total_ram_gb": round(vm.total / (1024 ** 3), 2),
        "total_ram_bytes": vm.total,
        "arch": platform.machine(),
        "kernel": platform.release(),
        "os": platform.system(),
    }
    # Check for NVIDIA GPU count if available
    try:
        from aegis.collectors.gpu import GpuCollector
        gpu_col = GpuCollector()
        gpus = gpu_col.sample()
        cap["gpu_count"] = len(gpus.get("devices", []))
    except Exception:
        cap["gpu_count"] = 0
    return cap


def sample_quick_telemetry() -> dict[str, Any]:
    """Samples instantaneous host vitals for the heartbeat payload."""
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    cpu_pct = psutil.cpu_percent(interval=None)

    # Primary disk usage
    disk_pct = 0.0
    try:
        disk_pct = psutil.disk_usage("/").percent
    except Exception:
        pass

    # Load averages on Linux
    load_1m = 0.0
    try:
        load_1m, _, _ = os.getloadavg()
    except (AttributeError, OSError):
        pass

    # Quick container count if Docker socket is available
    docker_containers_running = 0
    docker_sock = "/var/run/docker.sock"
    if os.path.exists(docker_sock):
        try:
            import docker
            client = docker.from_env(timeout=2)
            docker_containers_running = len(client.containers.list())
        except Exception:
            pass

    return {
        "cpu_percent": float(cpu_pct),
        "ram_percent": float(vm.percent),
        "ram_used_gb": round((vm.total - vm.available) / (1024 ** 3), 2),
        "swap_percent": float(swap.percent),
        "disk_percent": float(disk_pct),
        "load_1m": float(load_1m),
        "docker_running": docker_containers_running,
        "timestamp": time.time(),
    }


class AegisAgent:
    """Lightweight autonomous agent reporting to the Aegis Server."""

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8000",
        token: str | None = None,
        node_id: str | None = None,
        hostname: str | None = None,
        ip_address: str | None = None,
        labels: dict[str, str] | None = None,
        interval_s: float = 5.0,
        timeout_s: float = 8.0,
        drain_on_stop: bool = True,
    ):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.hostname = hostname or socket.gethostname()
        self.node_id = node_id or self.hostname
        self.ip_address = ip_address or get_primary_ip()
        self.labels = labels or {"role": "worker"}
        self.interval_s = max(1.0, float(interval_s))
        self.timeout_s = max(2.0, float(timeout_s))
        self.drain_on_stop = drain_on_stop

        self._running = False
        self._registered = False
        self.client = httpx.Client(timeout=self.timeout_s)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def register(self) -> bool:
        """Registers this node with the Aegis cluster mesh."""
        cap = sample_host_capacity()
        labels = dict(self.labels)
        labels["arch"] = cap.get("arch", "unknown")
        labels["cores"] = str(cap.get("cpu_cores", 1))

        payload = {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "labels": labels,
        }

        url = f"{self.server_url}/api/cluster/nodes/register"
        try:
            r = self.client.post(url, json=payload, headers=self._headers())
            if r.status_code in (200, 201):
                self._registered = True
                _LOG.info(
                    "Registered node '%s' (%s) with Aegis Server at %s",
                    self.node_id, self.ip_address, self.server_url
                )
                return True
            else:
                _LOG.warning("Registration failed (HTTP %d): %s", r.status_code, r.text)
                return False
        except Exception as e:
            _LOG.error("Failed to connect to Aegis Server at %s: %s", url, e)
            return False

    def heartbeat(self) -> bool:
        """Sends an authenticated telemetry heartbeat."""
        if not self._registered:
            ok = self.register()
            if not ok:
                return False

        telemetry = sample_quick_telemetry()
        url = f"{self.server_url}/api/cluster/nodes/{self.node_id}/heartbeat"
        try:
            r = self.client.post(
                url,
                json={"telemetry_summary": telemetry},
                headers=self._headers(),
            )
            if r.status_code == 200:
                _LOG.debug("Heartbeat OK for node %s (CPU: %.1f%%, RAM: %.1f%%)",
                          self.node_id, telemetry["cpu_percent"], telemetry["ram_percent"])
                return True
            elif r.status_code == 404:
                # Server might have restarted; re-register
                _LOG.info("Node '%s' not recognized by server; re-registering...", self.node_id)
                self._registered = False
                return self.register()
            else:
                _LOG.warning("Heartbeat error (HTTP %d): %s", r.status_code, r.text)
                return False
        except Exception as e:
            _LOG.warning("Heartbeat failed to %s: %s", url, e)
            return False

    def drain(self, reason: str = "Agent shutting down") -> bool:
        """Notifies the server to drain this node before shutdown."""
        url = f"{self.server_url}/api/cluster/nodes/{self.node_id}/drain"
        try:
            r = self.client.post(url, json={"reason": reason}, headers=self._headers())
            return r.status_code == 200
        except Exception as e:
            _LOG.warning("Failed to notify drain to server: %s", e)
            return False

    def run_once(self) -> bool:
        """Performs a single registration and heartbeat check."""
        return self.heartbeat()

    def run(self) -> None:
        """Runs the background telemetry agent loop until interrupted."""
        self._running = True

        def _handle_exit(sig: int, _frame: Any) -> None:
            _LOG.info("Caught signal %d; shutting down agent...", sig)
            self._running = False

        try:
            signal.signal(signal.SIGINT, _handle_exit)
            signal.signal(signal.SIGTERM, _handle_exit)
        except (ValueError, AttributeError):
            pass

        _LOG.info(
            "Starting Aegis Agent [node_id: %s] pointing to %s (interval: %.1fs)",
            self.node_id, self.server_url, self.interval_s
        )

        # Initial registration attempt
        self.register()

        backoff = 1.0
        while self._running:
            success = self.heartbeat()
            if success:
                backoff = 1.0
                time.sleep(self.interval_s)
            else:
                # Exponential backoff up to 30s
                sleep_t = min(30.0, self.interval_s * backoff)
                _LOG.debug("Retrying heartbeat in %.1fs...", sleep_t)
                time.sleep(sleep_t)
                backoff = min(backoff * 1.5, 6.0)

        # Graceful shutdown
        if self.drain_on_stop:
            _LOG.info("Cordoning node '%s' on server...", self.node_id)
            self.drain("Agent stopped")

        self.client.close()
        _LOG.info("Aegis Agent stopped cleanly.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="aegis-agent",
        description="Aegis AI Node Agent — distributed host telemetry collector"
    )
    p.add_argument(
        "--server", "-s",
        default=os.getenv("AEGIS_SERVER_URL", "http://127.0.0.1:8000"),
        help="Aegis Server URL (default: http://127.0.0.1:8000)"
    )
    p.add_argument(
        "--token", "-t",
        default=os.getenv("AEGIS_AGENT_TOKEN", os.getenv("AEGIS_API_TOKEN")),
        help="RBAC Bearer token for server authentication"
    )
    p.add_argument(
        "--node-id",
        default=os.getenv("AEGIS_NODE_ID"),
        help="Unique cluster node ID (default: hostname)"
    )
    p.add_argument(
        "--interval", "-i",
        type=float,
        default=float(os.getenv("AEGIS_AGENT_INTERVAL", "5.0")),
        help="Heartbeat interval in seconds (default: 5.0)"
    )
    p.add_argument(
        "--labels", "-l",
        default=os.getenv("AEGIS_NODE_LABELS", "role=worker"),
        help="Comma-separated key=value labels (e.g. role=worker,env=prod)"
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Run single registration/heartbeat and exit"
    )
    p.add_argument(
        "--no-drain",
        action="store_true",
        help="Do not drain node on exit"
    )
    p.add_argument(
        "--log-level",
        default=os.getenv("AEGIS_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )

    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Parse labels
    labels: dict[str, str] = {}
    if args.labels:
        for part in args.labels.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                labels[k.strip()] = v.strip()

    agent = AegisAgent(
        server_url=args.server,
        token=args.token,
        node_id=args.node_id,
        labels=labels,
        interval_s=args.interval,
        drain_on_stop=not args.no_drain,
    )

    if args.once:
        ok = agent.run_once()
        sys.exit(0 if ok else 1)
    else:
        agent.run()


if __name__ == "__main__":
    main()
