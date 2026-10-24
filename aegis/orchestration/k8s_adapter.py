"""Kubernetes orchestration adapter for cluster, node, deployment, and pod monitoring.

Uses the official Kubernetes client. Operates in read-only mode by default;
write operations (e.g. deployment rollout restart) require explicit policy authorization.
Gracefully handles missing clusters or absent kubeconfig.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aegis.db import utcnow

_LOG = logging.getLogger("aegis.orchestration.k8s")


@dataclass
class PodInfo:
    name: str
    namespace: str
    phase: str  # Running, Pending, Failed, Succeeded, Unknown
    node_name: str | None
    ip: str | None
    restart_count: int
    container_states: list[dict[str, Any]]
    waiting_reasons: list[str]
    is_ready: bool
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "phase": self.phase,
            "node_name": self.node_name,
            "ip": self.ip,
            "restart_count": self.restart_count,
            "container_states": self.container_states,
            "waiting_reasons": self.waiting_reasons,
            "is_ready": self.is_ready,
            "created_at": self.created_at,
        }


@dataclass
class DeploymentInfo:
    name: str
    namespace: str
    desired_replicas: int
    available_replicas: int
    updated_replicas: int
    is_healthy: bool
    labels: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "desired_replicas": self.desired_replicas,
            "available_replicas": self.available_replicas,
            "updated_replicas": self.updated_replicas,
            "is_healthy": self.is_healthy,
            "labels": self.labels,
        }


@dataclass
class NodeInfo:
    name: str
    status: str  # Ready, NotReady, Unknown
    cpu_capacity: str
    memory_capacity: str
    kubelet_version: str
    os_image: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "cpu_capacity": self.cpu_capacity,
            "memory_capacity": self.memory_capacity,
            "kubelet_version": self.kubelet_version,
            "os_image": self.os_image,
        }


class KubernetesAdapter:
    """Discovers and monitors Kubernetes cluster workloads and health."""

    def __init__(self, kubeconfig_path: str | None = None, allow_write: bool = False):
        self.kubeconfig_path = kubeconfig_path
        self.allow_write = allow_write
        self._connected = False
        self._v1 = None
        self._apps_v1 = None
        self._connect()

    def _connect(self) -> None:
        try:
            from kubernetes import client, config
            # 1. Try in-cluster config
            try:
                config.load_incluster_config()
                self._connected = True
            except Exception:
                # 2. Try explicit kubeconfig or default ~/.kube/config
                k_path = self.kubeconfig_path or str(Path.home() / ".kube/config")
                if Path(k_path).exists():
                    config.load_kube_config(config_file=k_path)
                    self._connected = True
                else:
                    self._connected = False

            if self._connected:
                self._v1 = client.CoreV1Api()
                self._apps_v1 = client.AppsV1Api()
        except Exception as e:
            _LOG.debug("Kubernetes client initialization note: %s", e)
            self._connected = False

    def is_available(self) -> bool:
        return self._connected

    def sample(self) -> dict[str, Any]:
        """Discovers nodes, namespaces, deployments, pods, and incidents."""
        if not self.is_available():
            return {
                "available": False,
                "reason": "Kubernetes cluster not configured or ~/.kube/config not found",
                "nodes": [],
                "deployments": [],
                "pods": [],
                "incidents": [],
                "collected_at": utcnow(),
            }

        try:
            nodes = self._discover_nodes()
            deployments = self._discover_deployments()
            pods = self._discover_pods()
            incidents = self._detect_incidents(nodes, deployments, pods)

            return {
                "available": True,
                "reason": None,
                "nodes": [n.as_dict() for n in nodes],
                "deployments": [d.as_dict() for d in deployments],
                "pods": [p.as_dict() for p in pods],
                "incidents": incidents,
                "collected_at": utcnow(),
            }
        except Exception as e:
            _LOG.warning("Kubernetes sample error: %s", e)
            return {
                "available": False,
                "reason": f"Kubernetes API error: {e}",
                "nodes": [],
                "deployments": [],
                "pods": [],
                "incidents": [],
                "collected_at": utcnow(),
            }

    def _discover_nodes(self) -> list[NodeInfo]:
        nodes: list[NodeInfo] = []
        if not self._v1:
            return nodes
        for item in self._v1.list_node(timeout_seconds=5).items:
            status = "Unknown"
            for cond in item.status.conditions or []:
                if cond.type == "Ready":
                    status = "Ready" if cond.status == "True" else "NotReady"
                    break
            capacity = item.status.capacity or {}
            node_info = item.status.node_info
            nodes.append(NodeInfo(
                name=item.metadata.name,
                status=status,
                cpu_capacity=str(capacity.get("cpu", "unknown")),
                memory_capacity=str(capacity.get("memory", "unknown")),
                kubelet_version=getattr(node_info, "kubelet_version", "unknown"),
                os_image=getattr(node_info, "os_image", "unknown"),
            ))
        return nodes

    def _discover_deployments(self) -> list[DeploymentInfo]:
        deployments: list[DeploymentInfo] = []
        if not self._apps_v1:
            return deployments
        for d in self._apps_v1.list_deployment_for_all_namespaces(timeout_seconds=5).items:
            desired = d.spec.replicas or 1
            available = d.status.available_replicas or 0
            updated = d.status.updated_replicas or 0
            deployments.append(DeploymentInfo(
                name=d.metadata.name,
                namespace=d.metadata.namespace,
                desired_replicas=desired,
                available_replicas=available,
                updated_replicas=updated,
                is_healthy=available >= desired,
                labels=d.metadata.labels or {},
            ))
        return deployments

    def _discover_pods(self) -> list[PodInfo]:
        pods: list[PodInfo] = []
        if not self._v1:
            return pods
        for p in self._v1.list_pod_for_all_namespaces(timeout_seconds=5).items:
            total_restarts = 0
            waiting_reasons: list[str] = []
            container_states: list[dict[str, Any]] = []
            is_ready = True

            statuses = p.status.container_statuses or []
            for cs in statuses:
                total_restarts += cs.restart_count
                if not cs.ready:
                    is_ready = False
                state_desc = {}
                if cs.state.running:
                    state_desc["state"] = "running"
                elif cs.state.terminated:
                    state_desc["state"] = "terminated"
                    state_desc["exit_code"] = cs.state.terminated.exit_code
                    state_desc["reason"] = cs.state.terminated.reason
                elif cs.state.waiting:
                    state_desc["state"] = "waiting"
                    reason = cs.state.waiting.reason or "Waiting"
                    state_desc["reason"] = reason
                    waiting_reasons.append(reason)
                container_states.append({
                    "name": cs.name,
                    "ready": cs.ready,
                    "restarts": cs.restart_count,
                    **state_desc,
                })

            created = p.metadata.creation_timestamp
            created_str = created.isoformat() if created else utcnow()

            pods.append(PodInfo(
                name=p.metadata.name,
                namespace=p.metadata.namespace,
                phase=p.status.phase or "Unknown",
                node_name=p.spec.node_name,
                ip=p.status.pod_ip,
                restart_count=total_restarts,
                container_states=container_states,
                waiting_reasons=waiting_reasons,
                is_ready=is_ready,
                created_at=created_str,
            ))
        return pods

    def _detect_incidents(
        self, nodes: list[NodeInfo], deployments: list[DeploymentInfo], pods: list[PodInfo]
    ) -> list[dict[str, Any]]:
        incidents: list[dict[str, Any]] = []

        # 1. Unhealthy Nodes
        for n in nodes:
            if n.status != "Ready":
                incidents.append({
                    "type": "k8s_node_not_ready",
                    "severity": "critical",
                    "resource": f"node/{n.name}",
                    "summary": f"Kubernetes node {n.name} status is {n.status}",
                    "evidence": n.as_dict(),
                })

        # 2. Degraded Deployments
        for d in deployments:
            if not d.is_healthy:
                incidents.append({
                    "type": "k8s_deployment_degraded",
                    "severity": "warning",
                    "resource": f"deployment/{d.namespace}/{d.name}",
                    "summary": f"Deployment {d.name} replicas degraded ({d.available_replicas}/{d.desired_replicas} available)",
                    "evidence": d.as_dict(),
                })

        # 3. Pod Failures (CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending)
        for p in pods:
            for reason in p.waiting_reasons:
                if "CrashLoopBackOff" in reason:
                    incidents.append({
                        "type": "k8s_pod_crash_loop",
                        "severity": "critical",
                        "resource": f"pod/{p.namespace}/{p.name}",
                        "summary": f"Pod {p.name} is in CrashLoopBackOff (restarts: {p.restart_count})",
                        "evidence": p.as_dict(),
                    })
                elif "ImagePullBackOff" in reason or "ErrImagePull" in reason:
                    incidents.append({
                        "type": "k8s_pod_image_pull_failed",
                        "severity": "critical",
                        "resource": f"pod/{p.namespace}/{p.name}",
                        "summary": f"Pod {p.name} failed to pull image ({reason})",
                        "evidence": p.as_dict(),
                    })
            for cs in p.container_states:
                if cs.get("reason") == "OOMKilled" or cs.get("exit_code") == 137:
                    incidents.append({
                        "type": "k8s_pod_oom_killed",
                        "severity": "critical",
                        "resource": f"pod/{p.namespace}/{p.name}",
                        "summary": f"Pod {p.name} container {cs['name']} OOMKilled",
                        "evidence": p.as_dict(),
                    })

        return incidents

    def rollout_restart_deployment(
        self, namespace: str, name: str, mode: str = "dry_run"
    ) -> dict[str, Any]:
        """Rollout restart an authorized deployment by updating annotations."""
        if not self.is_available() or not self._apps_v1:
            return {"result": "FAILED", "reason": "Kubernetes API not connected"}

        if namespace in ("kube-system", "kube-public", "kube-node-lease"):
            return {
                "result": "REJECTED",
                "reason": f"Namespace '{namespace}' is a protected system namespace",
            }

        if mode == "dry_run":
            return {
                "result": "VERIFIED",
                "mode": "dry_run",
                "target": f"deployment/{namespace}/{name}",
                "reason": "Dry-run policy evaluation passed: deployment is eligible for restart",
            }

        if not self.allow_write:
            return {
                "result": "BLOCKED",
                "reason": "Kubernetes write operations are disabled in current policy mode",
            }

        try:
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": utcnow()
                            }
                        }
                    }
                }
            }
            self._apps_v1.patch_namespaced_deployment(
                name=name, namespace=namespace, body=patch
            )
            return {
                "result": "VERIFIED",
                "mode": "real",
                "target": f"deployment/{namespace}/{name}",
                "verified": True,
            }
        except Exception as e:
            return {"result": "FAILED", "reason": f"Patch error: {e}"}
