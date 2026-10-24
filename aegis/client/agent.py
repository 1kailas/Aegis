"""Aegis Agent daemon - lightweight host telemetry agent for distributed cluster mesh.

Runs on individual compute nodes or worker hosts. Periodically samples system
telemetry (CPU, memory, disk, network, PSI, GPU, Docker) and reports back to
the Aegis Server control plane via authenticated HTTP heartbeats.

Can run zero-dependency using standard library Python 3 on any client machine:
  curl -sSL http://<server-ip>:8000/agent.py | python3 - <server-ip>
Or installed via CLI:
  aegis-agent 192.168.1.100
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import signal
import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse

# Optional rich libraries with graceful standard library fallback
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_LOG = logging.getLogger("aegis.agent")


def normalize_server_url(raw: str | None, default_port: int = 8000) -> str:
    """Normalizes any IP or URL string into a fully qualified HTTP(S) endpoint.

    Examples:
      '192.168.1.50'       -> 'http://192.168.1.50:8000'
      '192.168.1.50:8480'  -> 'http://192.168.1.50:8480'
      'localhost'          -> 'http://localhost:8000'
      'http://10.0.0.1'    -> 'http://10.0.0.1:8000'
      'http://10.0.0.1:90' -> 'http://10.0.0.1:90'
      'https://aegis.io'   -> 'https://aegis.io'
    """
    if not raw:
        return f"http://127.0.0.1:{default_port}"
    s = raw.strip()
    if not (s.startswith("http://") or s.startswith("https://")):
        if ":" in s and not s.endswith(":"):
            s = f"http://{s}"
        else:
            s = f"http://{s}:{default_port}"
    else:
        p = urlparse(s)
        if not p.port and p.scheme == "http":
            s = f"{s.rstrip('/')}:{default_port}"
    return s.rstrip("/")


def get_primary_ip() -> str:
    """Discovers primary local IP address routed outward."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def sample_host_capacity() -> dict[str, Any]:
    """Inspects physical capacity of this host (works with or without psutil)."""
    cpu_cores = os.cpu_count() or 1
    total_ram_bytes = 0

    if _HAS_PSUTIL:
        try:
            total_ram_bytes = psutil.virtual_memory().total
        except Exception:
            pass

    if total_ram_bytes == 0 and os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_ram_bytes = int(line.split()[1]) * 1024
                        break
        except Exception:
            pass

    if total_ram_bytes == 0:
        total_ram_bytes = 4 * 1024 * 1024 * 1024  # Default 4GB fallback

    cap: dict[str, Any] = {
        "cpu_cores": cpu_cores,
        "total_ram_gb": round(total_ram_bytes / (1024 ** 3), 2),
        "total_ram_bytes": total_ram_bytes,
        "arch": platform.machine(),
        "kernel": platform.release(),
        "os": platform.system(),
        "gpu_count": 0,
    }

    # GPU discovery if nvidia-smi is available
    if shutil.which("nvidia-smi"):
        try:
            import subprocess
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode()
            cap["gpu_count"] = len([l for l in out.strip().split("\n") if l.strip()])
        except Exception:
            pass

    return cap


def sample_quick_telemetry() -> dict[str, Any]:
    """Samples instantaneous host vitals for the heartbeat payload."""
    cpu_pct = 0.0
    ram_pct = 0.0
    ram_used_gb = 0.0
    ram_total_gb = 0.0
    swap_pct = 0.0
    disk_pct = 0.0
    load_1m = 0.0

    # 1. Memory
    if _HAS_PSUTIL:
        try:
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()
            cpu_pct = float(psutil.cpu_percent(interval=None))
            ram_pct = float(vm.percent)
            ram_used_gb = round((vm.total - vm.available) / (1024 ** 3), 2)
            ram_total_gb = round(vm.total / (1024 ** 3), 2)
            swap_pct = float(swap.percent)
        except Exception:
            pass

    # Fallback to /proc/meminfo if psutil unavailable or failed
    if ram_pct == 0.0 and os.path.exists("/proc/meminfo"):
        try:
            total_kb, avail_kb = 0, 0
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_kb = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        avail_kb = int(line.split()[1])
            if total_kb > 0:
                used_kb = total_kb - avail_kb
                ram_pct = round((used_kb / total_kb) * 100, 1)
                ram_used_gb = round(used_kb / (1024 * 1024), 2)
                ram_total_gb = round(total_kb / (1024 * 1024), 2)
        except Exception:
            pass

    # 2. Disk Usage via standard library shutil
    try:
        du = shutil.disk_usage("/")
        disk_pct = round((du.used / du.total) * 100, 1)
    except Exception:
        pass

    # 3. Load average on Linux
    try:
        load_1m, _, _ = os.getloadavg()
    except (AttributeError, OSError):
        pass

    # 4. CPU percentage fallback if psutil absent
    if cpu_pct == 0.0 and load_1m > 0:
        cores = os.cpu_count() or 1
        cpu_pct = min(100.0, round((load_1m / cores) * 100, 1))

    # 5. Quick container count if Docker socket is accessible
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
        "ram_percent": float(ram_pct),
        "ram_used_gb": float(ram_used_gb),
        "ram_total_gb": float(ram_total_gb),
        "swap_percent": float(swap_pct),
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
        self.server_url = normalize_server_url(server_url)
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

        if _HAS_HTTPX:
            self.client = httpx.Client(timeout=self.timeout_s)
        else:
            self.client = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        url = f"{self.server_url}{path}"
        headers = self._headers()

        if self.client is not None:
            r = self.client.post(url, json=payload, headers=headers)
            try:
                data = r.json()
            except Exception:
                data = {}
            return r.status_code, data
        else:
            # Fallback to Python standard library urllib.request
            import urllib.request
            import urllib.error
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw) if raw else {}
                    return resp.status, data
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8")
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {"detail": raw}
                return e.code, data

    def register(self) -> bool:
        """Registers this node with the Aegis cluster mesh."""
        cap = sample_host_capacity()
        labels = dict(self.labels)
        labels["arch"] = cap.get("arch", "unknown")
        labels["cores"] = str(cap.get("cpu_cores", 1))
        labels["ram_gb"] = str(cap.get("total_ram_gb", 4.0))

        payload = {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "labels": labels,
        }

        try:
            status_code, data = self._post_json("/api/cluster/nodes/register", payload)
            if status_code in (200, 201):
                self._registered = True
                _LOG.info(
                    "Registered node '%s' (%s) with Aegis Server at %s",
                    self.node_id, self.ip_address, self.server_url
                )
                return True
            else:
                _LOG.warning("Registration failed (HTTP %d): %s", status_code, data)
                return False
        except Exception as e:
            _LOG.error("Failed to connect to Aegis Server at %s: %s", self.server_url, e)
            return False

    def heartbeat(self) -> bool:
        """Sends an authenticated telemetry heartbeat."""
        if not self._registered:
            ok = self.register()
            if not ok:
                return False

        telemetry = sample_quick_telemetry()
        try:
            status_code, data = self._post_json(
                f"/api/cluster/nodes/{self.node_id}/heartbeat",
                {"telemetry_summary": telemetry}
            )
            if status_code == 200:
                _LOG.debug("Heartbeat OK for node %s (CPU: %.1f%%, RAM: %.1f%%)",
                           self.node_id, telemetry["cpu_percent"], telemetry["ram_percent"])
                return True
            elif status_code == 404:
                _LOG.info("Node '%s' not recognized by server; re-registering...", self.node_id)
                self._registered = False
                return self.register()
            else:
                _LOG.warning("Heartbeat error (HTTP %d): %s", status_code, data)
                return False
        except Exception as e:
            _LOG.warning("Heartbeat failed to %s: %s", self.server_url, e)
            return False

    def drain(self, reason: str = "Agent shutting down") -> bool:
        """Notifies the server to drain this node before shutdown."""
        try:
            status_code, _ = self._post_json(
                f"/api/cluster/nodes/{self.node_id}/drain",
                {"reason": reason}
            )
            return status_code == 200
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
                sleep_t = min(30.0, self.interval_s * backoff)
                _LOG.debug("Retrying heartbeat in %.1fs...", sleep_t)
                time.sleep(sleep_t)
                backoff = min(backoff * 1.5, 6.0)

        # Graceful shutdown
        if self.drain_on_stop:
            _LOG.info("Cordoning node '%s' on server...", self.node_id)
            self.drain("Agent stopped")

        if self.client is not None and hasattr(self.client, "close"):
            self.client.close()
        _LOG.info("Aegis Agent stopped cleanly.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="aegis-agent",
        description="Aegis AI Node Agent — distributed host telemetry collector"
    )
    p.add_argument(
        "server_ip",
        nargs="?",
        default=None,
        help="Aegis Server IP or URL (e.g. 192.168.1.100 or http://192.168.1.100:8000)"
    )
    p.add_argument(
        "--server", "-s",
        default=None,
        help="Aegis Server URL (overridden by positional server_ip)"
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

    # Determine server URL from positional IP or flag or env
    target_server = args.server_ip or args.server or os.getenv("AEGIS_SERVER_URL", "http://127.0.0.1:8000")
    target_server = normalize_server_url(target_server)

    # Parse labels
    labels: dict[str, str] = {}
    if args.labels:
        for part in args.labels.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                labels[k.strip()] = v.strip()

    agent = AegisAgent(
        server_url=target_server,
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
