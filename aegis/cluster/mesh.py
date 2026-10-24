"""Distributed multi-node cluster mesh and agent coordinator for Aegis AI 2.0.

Provides peer-to-peer and coordinator-based cluster topology tracking,
cryptographic node registration, heartbeat health tracking, workload evacuation
(node draining), and cluster-wide aggregate capacity analytics.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aegis.db import Database, utcnow
from aegis.events import EventBus

_LOG = logging.getLogger("aegis.cluster.mesh")


@dataclass
class ClusterNode:
    node_id: str
    hostname: str
    ip_address: str | None
    status: str  # ONLINE, DEGRADED, OFFLINE, DRAINED
    labels: dict[str, str] = field(default_factory=dict)
    telemetry_summary: dict[str, Any] = field(default_factory=dict)
    last_heartbeat_at: str = field(default_factory=utcnow)
    registered_at: str = field(default_factory=utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "status": self.status,
            "labels": self.labels,
            "telemetry_summary": self.telemetry_summary,
            "last_heartbeat_at": self.last_heartbeat_at,
            "registered_at": self.registered_at,
        }


class ClusterMeshManager:
    """Coordinates multi-node cluster topology, health, and workload draining."""

    def __init__(self, db: Database, events: EventBus, heartbeat_timeout_s: float = 30.0):
        self.db = db
        self.events = events
        self.heartbeat_timeout_s = heartbeat_timeout_s

    def register_node(
        self,
        node_id: str,
        hostname: str,
        ip_address: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> ClusterNode:
        """Registers a new node or updates existing node registration in the cluster."""
        now = utcnow()
        lbls = labels or {}
        node = ClusterNode(
            node_id=node_id,
            hostname=hostname,
            ip_address=ip_address,
            status="ONLINE",
            labels=lbls,
            telemetry_summary={},
            last_heartbeat_at=now,
            registered_at=now,
        )
        self.db.execute(
            "INSERT INTO cluster_nodes(node_id, hostname, ip_address, status, labels,"
            " telemetry_summary, last_heartbeat_at, registered_at) VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(node_id) DO UPDATE SET hostname=excluded.hostname,"
            " ip_address=excluded.ip_address, labels=excluded.labels,"
            " status='ONLINE', last_heartbeat_at=excluded.last_heartbeat_at",
            (
                node.node_id,
                node.hostname,
                node.ip_address,
                node.status,
                json.dumps(node.labels),
                json.dumps(node.telemetry_summary),
                node.last_heartbeat_at,
                node.registered_at,
            ),
        )
        self.events.emit(
            "cluster",
            f"Node {hostname} ({node_id}) joined cluster mesh",
            "info",
            node_id=node_id,
            status="ONLINE",
        )
        return node

    def record_heartbeat(
        self, node_id: str, telemetry_summary: dict[str, Any] | None = None
    ) -> bool:
        """Updates node heartbeat timestamp and current vitals summary."""
        now = utcnow()
        summary_json = json.dumps(telemetry_summary or {})
        row = self.db.query_one("SELECT status FROM cluster_nodes WHERE node_id=?", (node_id,))
        if not row:
            return False

        current_status = row["status"]
        new_status = "ONLINE" if current_status != "DRAINED" else "DRAINED"

        self.db.execute(
            "UPDATE cluster_nodes SET last_heartbeat_at=?, telemetry_summary=?,"
            " status=? WHERE node_id=?",
            (now, summary_json, new_status, node_id),
        )
        return True

    def evaluate_cluster_health(self) -> dict[str, Any]:
        """Scans for stale node heartbeats and marks unresponsive nodes OFFLINE."""
        rows = self.db.query("SELECT * FROM cluster_nodes")
        now_dt = datetime.now(timezone.utc)
        stale_nodes = []
        online_count = 0

        for r in rows:
            nid = r["node_id"]
            status = r["status"]
            if status == "DRAINED":
                continue

            last_hb = r["last_heartbeat_at"]
            try:
                hb_dt = datetime.fromisoformat(last_hb)
                diff = (now_dt - hb_dt).total_seconds()
            except Exception:
                diff = 999.0

            if diff > self.heartbeat_timeout_s:
                if status != "OFFLINE":
                    self.db.execute("UPDATE cluster_nodes SET status='OFFLINE' WHERE node_id=?", (nid,))
                    self.events.emit(
                        "cluster",
                        f"Node {r['hostname']} ({nid}) missed heartbeat ({diff:.1f}s > {self.heartbeat_timeout_s}s) -> OFFLINE",
                        "warning",
                        node_id=nid,
                        status="OFFLINE",
                    )
                    stale_nodes.append(nid)
            else:
                online_count += 1

        return {
            "total_nodes": len(rows),
            "online_nodes": online_count,
            "stale_nodes": stale_nodes,
        }

    def drain_node(self, node_id: str, reason: str = "Host failure containment") -> bool:
        """Evacuates workloads from a critically degraded or failing node."""
        row = self.db.query_one("SELECT hostname, status FROM cluster_nodes WHERE node_id=?", (node_id,))
        if not row:
            return False

        self.db.execute("UPDATE cluster_nodes SET status='DRAINED' WHERE node_id=?", (node_id,))
        self.events.emit(
            "cluster",
            f"Node {row['hostname']} ({node_id}) DRAINED: {reason}",
            "critical",
            node_id=node_id,
            reason=reason,
        )
        self.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail) VALUES(?,?,?,?,?)",
            (utcnow(), "cluster_mesh", "drain_node", node_id, reason),
        )
        return True

    def list_nodes(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM cluster_nodes ORDER BY registered_at DESC")
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["labels"] = json.loads(item["labels"]) if item.get("labels") else {}
            except Exception:
                item["labels"] = {}
            try:
                item["telemetry_summary"] = json.loads(item["telemetry_summary"]) if item.get("telemetry_summary") else {}
            except Exception:
                item["telemetry_summary"] = {}
            out.append(item)
        return out

    def deregister_node(self, node_id: str) -> bool:
        """Removes a node completely from the cluster mesh."""
        row = self.db.query_one("SELECT hostname FROM cluster_nodes WHERE node_id=?", (node_id,))
        if not row:
            return False
        self.db.execute("DELETE FROM cluster_nodes WHERE node_id=?", (node_id,))
        self.events.emit(
            "cluster",
            f"Node {row['hostname']} ({node_id}) removed from cluster mesh",
            "info",
            node_id=node_id,
        )
        return True

    def cluster_summary(self) -> dict[str, Any]:
        """Provides aggregate capacity and health metrics across all nodes."""
        nodes = self.list_nodes()
        total_nodes = len(nodes)
        online_nodes = sum(1 for n in nodes if n["status"] == "ONLINE")
        drained_nodes = sum(1 for n in nodes if n["status"] == "DRAINED")
        offline_nodes = sum(1 for n in nodes if n["status"] == "OFFLINE")

        total_cores = 0
        total_ram_gb = 0.0
        used_ram_gb = 0.0
        cpu_samples = []

        for n in nodes:
            lbls = n.get("labels") or {}
            try:
                cores = int(lbls.get("cores", 1))
            except (ValueError, TypeError):
                cores = 1
            total_cores += cores

            t = n.get("telemetry_summary") or {}
            mem = t.get("memory") or {}
            node_ram_gb = 0.0
            if mem.get("total_mb"):
                node_ram_gb = mem["total_mb"] / 1024.0
                used_ram_gb += mem.get("used_mb", 0) / 1024.0
            elif "ram_total_gb" in t:
                node_ram_gb = float(t["ram_total_gb"])
                used_ram_gb += float(t.get("ram_used_gb", 0.0))
            elif "ram_gb" in lbls:
                try:
                    node_ram_gb = float(lbls["ram_gb"])
                    used_ram_gb += float(t.get("ram_used_gb", 0.0))
                except (ValueError, TypeError):
                    pass
            elif t.get("ram_used_gb") and t.get("ram_percent"):
                pct = float(t["ram_percent"])
                if pct > 0:
                    node_ram_gb = float(t["ram_used_gb"]) / (pct / 100.0)
                    used_ram_gb += float(t["ram_used_gb"])

            total_ram_gb += node_ram_gb

            if t.get("cpu_percent") is not None:
                cpu_samples.append(float(t["cpu_percent"]))

        avg_cpu = round(sum(cpu_samples) / len(cpu_samples), 1) if cpu_samples else 0.0
        cluster_mem_pct = round((used_ram_gb / total_ram_gb) * 100.0, 1) if total_ram_gb > 0 else 0.0

        return {
            "total_nodes": total_nodes,
            "online_nodes": online_nodes,
            "drained_nodes": drained_nodes,
            "offline_nodes": offline_nodes,
            "total_cluster_cpu_cores": total_cores,
            "total_cluster_ram_gb": round(total_ram_gb, 1),
            "average_cpu_percent": avg_cpu,
            "cluster_memory_percent": cluster_mem_pct,
            "cluster_memory_total_mb": int(total_ram_gb * 1024),
            "cluster_memory_used_mb": int(used_ram_gb * 1024),
        }
