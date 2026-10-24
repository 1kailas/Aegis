"""Workload dependency graph, blast-radius estimation, and resource contention intelligence.

Represents topological relationships between hosts, Docker containers, compose services,
Kubernetes pods/deployments, and AI inference workloads to compute failure impact
and resource contention.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.orchestration.dependency_graph")


@dataclass
class GraphNode:
    id: str
    kind: str  # host, container, compose_service, k8s_pod, k8s_deployment, ai_service
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    relationship: str  # runs_on, depends_on, connects_to, shares_network


class DependencyGraph:
    """Directed graph capturing infrastructure topologies and workload links."""

    def __init__(self):
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []
        self._adjacency: dict[str, list[str]] = defaultdict(list)
        self._reverse_adjacency: dict[str, list[str]] = defaultdict(list)

    def clear(self) -> None:
        self._nodes.clear()
        self._edges.clear()
        self._adjacency.clear()
        self._reverse_adjacency.clear()

    def add_node(self, id: str, kind: str, name: str, metadata: dict[str, Any] | None = None) -> None:
        self._nodes[id] = GraphNode(id=id, kind=kind, name=name, metadata=metadata or {})

    def add_edge(self, source_id: str, target_id: str, relationship: str) -> None:
        edge = GraphEdge(source_id=source_id, target_id=target_id, relationship=relationship)
        self._edges.append(edge)
        self._adjacency[source_id].append(target_id)
        self._reverse_adjacency[target_id].append(source_id)

    def build_from_telemetry(
        self,
        host_id: str,
        docker_data: dict[str, Any] | None = None,
        k8s_data: dict[str, Any] | None = None,
        ai_data: dict[str, Any] | None = None,
    ) -> None:
        """Constructs graph from current discovery payloads."""
        self.clear()
        self.add_node(id=host_id, kind="host", name=host_id)

        # 1. Docker containers & networks
        containers = (docker_data or {}).get("containers", [])
        network_to_containers: dict[str, list[str]] = defaultdict(list)

        for c in containers:
            cid = f"docker:{c['name']}"
            self.add_node(id=cid, kind="container", name=c["name"], metadata=c)
            self.add_edge(cid, host_id, "runs_on")

            if c.get("service_name"):
                svc_id = f"compose_service:{c['service_name']}"
                self.add_node(id=svc_id, kind="compose_service", name=c["service_name"])
                self.add_edge(cid, svc_id, "belongs_to")

            for net in c.get("networks", []):
                if net != "none":
                    network_to_containers[net].append(cid)

        # Connect containers on the same docker network
        for net_name, c_ids in network_to_containers.items():
            for i in range(len(c_ids)):
                for j in range(i + 1, len(c_ids)):
                    self.add_edge(c_ids[i], c_ids[j], "shares_network")
                    self.add_edge(c_ids[j], c_ids[i], "shares_network")

        # 2. Kubernetes workloads
        k_pods = (k8s_data or {}).get("pods", [])
        k_deps = (k8s_data or {}).get("deployments", [])

        for d in k_deps:
            did = f"k8s_deploy:{d['namespace']}/{d['name']}"
            self.add_node(did, "k8s_deployment", d["name"], metadata=d)

        for p in k_pods:
            pid = f"k8s_pod:{p['namespace']}/{p['name']}"
            self.add_node(pid, "k8s_pod", p["name"], metadata=p)
            self.add_edge(pid, host_id, "runs_on")

        # 3. AI Workloads
        ollama = (ai_data or {}).get("ollama", {})
        if ollama.get("available"):
            ai_id = "ai_service:ollama"
            self.add_node(ai_id, "ai_service", "Ollama", metadata=ollama)
            self.add_edge(ai_id, host_id, "runs_on")
            for m in ollama.get("active_models", []):
                mid = f"ai_model:{m['name']}"
                self.add_node(mid, "ai_model", m["name"], metadata=m)
                self.add_edge(mid, ai_id, "served_by")

    def get_blast_radius(self, target_id: str) -> dict[str, Any]:
        """Calculates direct and transitive blast radius of an incident on target_id."""
        if target_id not in self._nodes:
            # Check by short name or partial match
            match = next((nid for nid in self._nodes if target_id in nid), None)
            if match:
                target_id = match
            else:
                return {
                    "target_id": target_id,
                    "target_found": False,
                    "affected_nodes_count": 0,
                    "affected_nodes": [],
                    "blast_radius_score": "LOW",
                }

        visited: set[str] = set()
        queue = deque([target_id])

        while queue:
            curr = queue.popleft()
            if curr in visited:
                continue
            visited.add(curr)
            # Traverse outwards (nodes connected to curr)
            for neighbor in self._adjacency.get(curr, []):
                if neighbor not in visited and neighbor != "host":
                    queue.append(neighbor)
            for neighbor in self._reverse_adjacency.get(curr, []):
                if neighbor not in visited and neighbor != "host":
                    queue.append(neighbor)

        affected = [self._nodes[nid].name for nid in visited if nid != target_id]
        count = len(affected)

        score = "LOW"
        if count >= 5:
            score = "HIGH"
        elif count >= 2:
            score = "MEDIUM"

        return {
            "target_id": target_id,
            "target_found": True,
            "affected_nodes_count": count,
            "affected_nodes": affected,
            "blast_radius_score": score,
        }

    def detect_competing_resources(
        self, docker_data: dict[str, Any], ai_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Identifies workloads competing for finite host resources (e.g. RAM or GPU)."""
        contentions: list[dict[str, Any]] = []
        containers = docker_data.get("containers", [])
        ai_procs = ai_data.get("ai_processes", [])

        # Check memory competition
        high_mem_containers = [
            c for c in containers
            if (c.get("metrics") or {}).get("memory_usage_mb", 0) > 1024
        ]
        high_mem_ai = [
            p for p in ai_procs if p.get("memory_mb", 0) > 1024
        ]

        if high_mem_containers and high_mem_ai:
            contentions.append({
                "type": "competing_memory_demand",
                "severity": "warning",
                "description": f"{len(high_mem_containers)} container(s) and {len(high_mem_ai)} AI process(es) concurrently consuming >1GB RAM",
                "containers": [c["name"] for c in high_mem_containers],
                "ai_processes": [p["name"] for p in high_mem_ai],
            })

        return contentions

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"id": n.id, "kind": n.kind, "name": n.name, "metadata": n.metadata}
                for n in self._nodes.values()
            ],
            "edges": [
                {"source": e.source_id, "target": e.target_id, "relationship": e.relationship}
                for e in self._edges
            ],
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "updated_at": utcnow(),
        }
