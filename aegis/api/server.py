"""FastAPI application: health, telemetry, thermal, incidents, events (SSE),
policy controls, emergency stop and the safe demo override endpoints.

State-changing endpoints accept an optional bearer token
(config.server.api_token). By default the server binds 127.0.0.1 only;
never expose the control plane to a public network without adding real
authentication.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from aegis import __version__
from aegis.appstate import AppContext
from aegis.collectors.overrides import VIRTUAL_CPU_SENSOR_ID

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

DEMO_KINDS = {
    "warning": "Simulated WARNING: holds ~90degC to cross the warning threshold.",
    "critical": "Simulated CRITICAL: holds ~95degC to cross the critical threshold.",
    "rapid_rise": "Simulated rapid rise at ~80degC triggers rate-of-rise escalation.",
    "unavailable": "Removes overrides to show honest no-sensor handling.",
    "recover": "Simulated recovery: holds ~40degC so cooling can be verified.",
    "clear": "Removes every override.",
}


class ModeRequest(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in ("monitoring_only", "operator_approval", "policy_autonomous"):
            raise ValueError("mode must be monitoring_only, operator_approval, or policy_autonomous")
        return v


class EmergencyStopRequest(BaseModel):
    enabled: bool


class OverrideRequest(BaseModel):
    kind: str
    value_c: float | None = Field(default=None, gt=0, lt=200)

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        if v not in DEMO_KINDS:
            raise ValueError(f"kind must be one of {sorted(DEMO_KINDS)}")
        return v


def _guard_state_changer(ctx: AppContext,
                         authorization: str | None) -> None:
    # 1. RBAC Token Verification
    if authorization and "aegis_sec_" in authorization:
        key_rec = ctx.rbac.authenticate_token(authorization)
        if not key_rec:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked RBAC token")
        if key_rec.role == "READONLY":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="READONLY token cannot execute state changes")
        return

    # 2. Config static bearer token verification
    token = ctx.config.server.api_token
    if not token:
        return  # local development binding only
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing or invalid bearer token")


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Aegis AI", version=__version__,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------- client distribution
    @app.get("/agent.py", response_class=PlainTextResponse)
    def serve_agent_py() -> str:
        agent_file = Path(__file__).resolve().parent.parent / "client" / "agent.py"
        if agent_file.exists():
            return agent_file.read_text(encoding="utf-8")
        raise HTTPException(status_code=404, detail="agent.py not found")

    @app.get("/install.sh", response_class=PlainTextResponse)
    def serve_install_sh(request: Request) -> str:
        install_file = Path(__file__).resolve().parent.parent.parent / "scripts" / "install.sh"
        if install_file.exists():
            content = install_file.read_text(encoding="utf-8")
            host = request.headers.get("host", "127.0.0.1:8000")
            return content.replace("http://127.0.0.1:8000", f"http://{host}")
        raise HTTPException(status_code=404, detail="install.sh not found")

    @app.get("/api/cluster/join-info")
    def cluster_join_info(request: Request) -> dict[str, Any]:
        host = request.headers.get("host", "127.0.0.1:8000")
        url = f"http://{host}"
        return {
            "server_host": host,
            "server_url": url,
            "agent_url": f"{url}/agent.py",
            "install_url": f"{url}/install.sh",
            "curl_agent_run": f"curl -sSL {url}/agent.py | python3 - {host}",
            "curl_install_run": f"curl -sSL {url}/install.sh | bash -s {host}",
            "cli_join": f"aegis join {host}",
        }

    # ------------------------------------------------------------------ health
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        engine_running = bool(ctx.engine and ctx.engine.alive)
        return {
            "status": "ok" if engine_running else "degraded",
            "version": __version__,
            "uptime_s": round(time.time() - ctx.started_at, 1),
            "engine_running": engine_running,
            "mode": ctx.flags.mode,
            "emergency_stop": ctx.flags.emergency_stop,
            "db_integrity": ctx.db.db_integrity(),
            "host_id": ctx.identity.host_id,
        }

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        return ctx.snapshot()

    # ----------------------------------------------------------------- sensors
    @app.get("/api/sensors")
    def sensors() -> list[dict[str, Any]]:
        readings = ctx.registry.read_all()
        if ctx.config.monitoring.simulated_overrides_enabled:
            readings = ctx.overrides.apply(readings)
        by_id: dict[str, dict[str, Any]] = {
            s.sensor_id: {"sensor_id": s.sensor_id, "kind": s.kind,
                          "label": s.label, "source": s.source,
                          "unit": s.unit}
            for s in ctx.registry.all_sensors()
        }
        for r in readings:
            entry = by_id.setdefault(r.sensor.sensor_id, {
                "sensor_id": r.sensor.sensor_id, "kind": r.sensor.kind,
                "label": r.sensor.label, "source": r.sensor.source,
                "unit": r.sensor.unit})
            entry["latest"] = r.as_dict()
        out = list(by_id.values())
        for entry in out:
            entry.setdefault("latest", {
                "value": None, "validity": "UNAVAILABLE",
                "error": "no reading yet", "ts": None,
                "sensor_id": entry["sensor_id"],
            })
        return out

    # ----------------------------------------------------------------- metrics
    @app.get("/api/metrics/history")
    def history(points: int = 120) -> dict[str, Any]:
        pts = ctx.metrics.history(points)
        series: dict[str, Any] = {"cpu_percent": [], "mem_percent": [],
                                  "temps": {}}
        temp_series: dict[str, list[float]] = {}
        for _, snap, _v in pts:
            series["cpu_percent"].append(snap.get("cpu_percent"))
            series["mem_percent"].append(snap["memory"]["percent"])
            for t in snap.get("temperatures", []):
                if t["value"] is not None:
                    temp_series.setdefault(t["sensor_id"], []).append(t["value"])
        series["temps"] = temp_series
        return {"ts": [p[0] for p in pts], **series,
                "interval_s": ctx.flags.active_interval_s}

    @app.get("/api/telemetry/metrics")
    def telemetry_metrics() -> list[str]:
        return ctx.db.list_metrics()

    @app.get("/api/telemetry/query")
    def telemetry_query(
        metric: str | None = None,
        start_ts: str | None = None,
        end_ts: str | None = None,
        aggregate: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if aggregate and metric:
            return ctx.db.query_telemetry_aggregated(metric=metric, limit=min(limit, 500))
        return ctx.db.query_telemetry(metric=metric, start_ts=start_ts, end_ts=end_ts, limit=min(limit, 1000))

    # ------------------------------------------------------------------ system
    @app.get("/api/system/gpu")
    def system_gpu() -> dict[str, Any]:
        return ctx.gpu.sample()

    @app.get("/api/system/processes")
    def system_processes() -> dict[str, Any]:
        return ctx.processes.sample()

    @app.get("/api/system/storage")
    def system_storage() -> dict[str, Any]:
        return ctx.storage.sample()

    @app.get("/api/system/inventory")
    def system_inventory() -> dict[str, Any]:
        return ctx.inventory.sample()

    @app.get("/api/system/resources")
    def system_resources() -> dict[str, Any]:
        return {
            "state": ctx.resource_guardian.state.as_dict(),
            "policy": ctx.config.resource_policy.model_dump(),
        }

    # -------------------------------------------------------- workloads & orchestration
    @app.get("/api/workloads/docker")
    def workloads_docker() -> dict[str, Any]:
        return ctx.docker.sample()

    @app.get("/api/workloads/docker/{container_id}/logs")
    def workloads_docker_logs(container_id: str, tail: int = 100) -> dict[str, str]:
        return {"container_id": container_id, "logs": ctx.docker.get_container_logs(container_id, tail=min(tail, 500))}

    @app.post("/api/workloads/docker/{container_id}/restart", dependencies=[Depends(_auth_dp(ctx))])
    def workloads_docker_restart(container_id: str) -> dict[str, Any]:
        mode = "dry_run" if ctx.flags.mode == "monitoring_only" else "real"
        if ctx.flags.emergency_stop:
            raise HTTPException(status_code=403, detail="Emergency stop engaged; container restart blocked")
        result = ctx.docker.restart_container(container_id, mode=mode)
        ctx.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail) VALUES(?,?,?,?,?)",
            (utcnow(), "operator", f"docker_restart_{mode}", container_id, str(result)),
        )
        return result

    @app.get("/api/workloads/k8s")
    def workloads_k8s() -> dict[str, Any]:
        return ctx.k8s.sample()

    @app.post("/api/workloads/k8s/{namespace}/{name}/restart", dependencies=[Depends(_auth_dp(ctx))])
    def workloads_k8s_restart(namespace: str, name: str) -> dict[str, Any]:
        mode = "dry_run" if ctx.flags.mode == "monitoring_only" else "real"
        if ctx.flags.emergency_stop:
            raise HTTPException(status_code=403, detail="Emergency stop engaged; k8s restart blocked")
        result = ctx.k8s.rollout_restart_deployment(namespace, name, mode=mode)
        ctx.db.execute(
            "INSERT INTO audit_log(ts, actor, action, subject, detail) VALUES(?,?,?,?,?)",
            (utcnow(), "operator", f"k8s_restart_{mode}", f"{namespace}/{name}", str(result)),
        )
        return result

    @app.get("/api/workloads/ai")
    def workloads_ai() -> dict[str, Any]:
        return ctx.ai_workloads.sample()

    @app.get("/api/workloads/graph")
    def workloads_graph() -> dict[str, Any]:
        return ctx.graph.as_dict()

    @app.get("/api/workloads/blast_radius")
    def workloads_blast_radius(target: str) -> dict[str, Any]:
        return ctx.graph.get_blast_radius(target)

    @app.get("/api/thermal")
    def thermal() -> dict[str, Any]:
        rows = ctx.db.query(
            "SELECT ts, sensor_id, from_state, to_state, trigger_value_c, note"
            " FROM thermal_transitions ORDER BY id DESC LIMIT 50")
        return {
            "state": ctx.guardian.state.as_dict(),
            "mode": ctx.flags.mode,
            "emergency_stop": ctx.flags.emergency_stop,
            "transitions": [dict(r) for r in rows],
            "policy": ctx.config.thermal_policy.model_dump(),
        }

    @app.get("/api/incidents")
    def incidents(state: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        return ctx.incidents.list(state=state, limit=min(limit, 200))

    @app.post("/api/incidents/{incident_id}/diagnose")
    def incident_diagnose(incident_id: str) -> dict[str, Any]:
        inc = ctx.incidents.get(incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="Incident not found")
        history = ctx.metrics.history(10)
        latest_telemetry = history[-1][1] if history else None
        diag = ctx.diagnoser.diagnose(inc, latest_telemetry)
        ctx.incidents.append_evidence(incident_id, {"kind": "ai_diagnosis", "detail": diag.as_dict()})
        ctx.events.emit(
            "diagnosis",
            f"Diagnosis complete for {incident_id} via {diag.provider}: {diag.root_cause_hypothesis}",
            "info",
            incident_id=incident_id,
            provider=diag.provider,
        )
        return diag.as_dict()

    # ------------------------------------------------------------- Devin Engineering
    @app.get("/api/devin/status")
    def devin_status() -> dict[str, Any]:
        return ctx.devin.status()

    @app.get("/api/devin/tasks")
    def devin_tasks(limit: int = 50) -> list[dict[str, Any]]:
        return ctx.devin.list_tasks(limit=limit)

    @app.post("/api/devin/tasks", dependencies=[Depends(_auth_dp(ctx))])
    def devin_create_task(payload: dict[str, Any]) -> dict[str, Any]:
        incident_id = payload.get("incident_id")
        if not incident_id:
            raise HTTPException(status_code=422, detail="incident_id required")
        inc = ctx.incidents.get(incident_id)
        if not inc:
            raise HTTPException(status_code=404, detail="Incident not found")
        task = ctx.devin.create_repair_task(
            incident=inc,
            affected_repo=payload.get("affected_repo", "https://github.com/1kailas/Aegis.git"),
            affected_files=payload.get("affected_files"),
            reproduction_steps=payload.get("reproduction_steps"),
            acceptance_criteria=payload.get("acceptance_criteria"),
            sanitized_logs=payload.get("sanitized_logs", ""),
        )
        return task.as_dict()

    @app.post("/api/devin/tasks/{task_id}/dispatch", dependencies=[Depends(_auth_dp(ctx))])
    def devin_dispatch(task_id: str) -> dict[str, Any]:
        job = ctx.db.query_one("SELECT * FROM jobs WHERE id=?", (task_id,))
        if not job:
            raise HTTPException(status_code=404, detail="Task not found")
        task_data = json.loads(job["payload"]) if job["payload"] else {}
        from aegis.engineering.devin_adapter import RepairTask
        task = RepairTask(
            task_id=task_id,
            incident_id=task_data.get("incident_id", ""),
            title=task_data.get("title", ""),
            summary=task_data.get("summary", ""),
            root_cause_hypotheses=task_data.get("root_cause_hypotheses", []),
            affected_repo=task_data.get("affected_repo", ""),
            affected_revision=task_data.get("affected_revision"),
            affected_files=task_data.get("affected_files", []),
            reproduction_steps=task_data.get("reproduction_steps", []),
            acceptance_criteria=task_data.get("acceptance_criteria", []),
            allowed_scope=task_data.get("allowed_scope", []),
            prohibited_operations=task_data.get("prohibited_operations", []),
            sanitized_logs=task_data.get("sanitized_logs", ""),
        )
        return ctx.devin.dispatch_task(task)

    # -------------------------------------------------------- Remediation Policy Engine
    @app.post("/api/remediation/evaluate")
    def remediation_evaluate(payload: dict[str, Any]) -> dict[str, Any]:
        action = ctx.remediation.evaluate_proposal(
            action_type=payload.get("action_type", ""),
            target=payload.get("target", ""),
            reason=payload.get("reason", ""),
            evidence=payload.get("evidence"),
            expected_outcome=payload.get("expected_outcome", ""),
        )
        return {
            "action_type": action.action_type,
            "target": action.target,
            "risk_level": action.risk_level.value,
            "category": action.category.value,
            "preconditions": action.preconditions,
            "rollback_strategy": action.rollback_strategy,
            "verification_procedure": action.verification_procedure,
        }

    @app.post("/api/remediation/authorize", dependencies=[Depends(_auth_dp(ctx))])
    def remediation_authorize(payload: dict[str, Any]) -> dict[str, Any]:
        action = ctx.remediation.evaluate_proposal(
            action_type=payload.get("action_type", ""),
            target=payload.get("target", ""),
            reason=payload.get("reason", ""),
            evidence=payload.get("evidence"),
        )
        approved = bool(payload.get("operator_approved", False))
        result = ctx.remediation.authorize_and_execute(
            action=action,
            mode=ctx.flags.mode,
            emergency_stop=ctx.flags.emergency_stop,
            operator_approved=approved,
        )
        return result

    # ------------------------------------------------------------- Phase 4: Repair & GitHub
    @app.get("/api/github/status")
    def github_status() -> dict[str, Any]:
        return ctx.github.status()

    @app.post("/api/repair/validate")
    def repair_validate(payload: dict[str, Any]) -> dict[str, Any]:
        task_id = payload.get("task_id", f"task-{int(time.time())}")
        patch_text = payload.get("patch", "")
        test_commands = payload.get("test_commands", ["pytest -q tests/test_phase1.py"])
        if not patch_text:
            raise HTTPException(status_code=422, detail="Patch text required")

        report = ctx.workspace_mgr.apply_and_validate(
            task_id=task_id,
            patch_text=patch_text,
            test_commands=test_commands,
        )
        ctx.events.emit(
            "repair_validation",
            f"Validation {'PASSED' if report.passed else 'FAILED'} for {task_id}",
            "info" if report.passed else "warning",
            report=report.as_dict(),
        )
        return report.as_dict()

    @app.post("/api/repair/submit_pr", dependencies=[Depends(_auth_dp(ctx))])
    def repair_submit_pr(payload: dict[str, Any]) -> dict[str, Any]:
        incident_id = payload.get("incident_id")
        task_id = payload.get("task_id", f"task-{int(time.time())}")
        branch_name = payload.get("branch_name", f"aegis/repair-{task_id}")
        commit_message = payload.get("commit_message", f"fix(auto-repair): resolve {incident_id}")
        mode = payload.get("mode", "dry_run" if ctx.flags.mode == "monitoring_only" else "real")

        inc = ctx.incidents.get(incident_id) if incident_id else {"id": incident_id, "title": "Auto Repair"}
        validation_data = payload.get("validation_report")
        if not validation_data:
            raise HTTPException(status_code=422, detail="validation_report required")

        from aegis.engineering.workspace import ValidationReport, ValidationStepResult
        steps = [
            ValidationStepResult(
                command=s.get("command", ""),
                exit_code=s.get("exit_code", 0),
                duration_s=s.get("duration_s", 0.0),
                stdout=s.get("stdout", ""),
                stderr=s.get("stderr", ""),
                passed=s.get("passed", True),
            )
            for s in validation_data.get("steps", [])
        ]
        val_report = ValidationReport(
            task_id=task_id,
            passed=validation_data.get("passed", False),
            scope_valid=validation_data.get("scope_valid", False),
            changed_files=validation_data.get("changed_files", []),
            steps=steps,
            rejection_reason=validation_data.get("rejection_reason"),
        )

        if not val_report.passed:
            raise HTTPException(status_code=400, detail="Cannot open PR for unverified or failing repair")

        pr_res = ctx.github.create_repair_pr(
            incident=inc or {},
            validation_report=val_report,
            branch_name=branch_name,
            commit_message=commit_message,
            mode=mode,
        )

        if pr_res.success and incident_id:
            ctx.incidents.append_evidence(
                incident_id,
                {"kind": "github_pr", "pr_url": pr_res.pr_url, "pr_number": pr_res.pr_number},
            )
            ctx.incidents.transition(incident_id, "PR_OPENED", "github_adapter", note=f"Opened PR {pr_res.pr_url}")

        return pr_res.as_dict()

    @app.get("/docs", include_in_schema=False)
    def docs_redirect():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/api/docs")

    @app.post("/api/repair/verify_recovery")
    def repair_verify_recovery(payload: dict[str, Any]) -> dict[str, Any]:
        incident_id = payload.get("incident_id")
        target = payload.get("target", "host")

        # Live probe function
        def probe():
            snap, val, _ = ctx.metrics.collect()
            cpu_pct = snap.get("cpu_percent", 0.0) or 0.0
            mem_pct = snap.get("memory", {}).get("percent", 0.0) or 0.0

            if target == "thermal_guardian":
                t_state = ctx.guardian.state.state
                healthy = t_state in ("NORMAL", "RECOVERY")
                return healthy, f"Thermal guardian state: {t_state}", {"state": t_state}

            if ctx.docker.is_available() and target not in ("host", "system"):
                try:
                    c = ctx.docker._client.containers.get(target)
                    healthy = c.status == "running"
                    return healthy, f"Container '{target}' status: {c.status}", {"status": c.status}
                except Exception:
                    pass

            healthy = (cpu_pct < 95.0) and (mem_pct < 95.0)
            return healthy, f"Host vitals: CPU={cpu_pct}%, Mem={mem_pct}%", snap

        rec = ctx.recovery_verifier.verify_workload_recovery(
            incident_id=incident_id or f"INC-{int(time.time())}",
            target=target,
            check_fn=probe,
        )

        if rec.status.value == "CONFIRMED" and incident_id:
            try:
                ctx.incidents.transition(
                    incident_id, "RECOVERY_VERIFIED", "recovery_verifier",
                    note=f"Workload {target} confirmed stabilized"
                )
            except Exception:
                pass

        return rec.as_dict()

    @app.get("/api/actions")
    def actions(limit: int = 25) -> list[dict[str, Any]]:
        rows = ctx.db.query(
            "SELECT ts, incident_id, action, target, reason, mode, result,"
            " detail FROM remediation_actions ORDER BY id DESC LIMIT ?",
            (min(limit, 200),))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ events
    @app.get("/api/events")
    def events(after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        evs = ctx.events.since(after) if after else ctx.events.latest(limit)
        return [e.__dict__ for e in evs[:limit]]

    @app.get("/api/events/stream")
    async def event_stream(request_after: int = 0) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            last = int(request_after)
            yield "retry: 3000\n\n"
            while True:
                pending = ctx.events.since(last)
                for ev in pending:
                    last = ev.seq
                    payload = json.dumps(ev.__dict__, default=str)
                    yield f"id: {ev.seq}\nevent: {ev.kind}\ndata: {payload}\n\n"
                if not pending:
                    await asyncio.sleep(1.0)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # ------------------------------------------------------------- Prometheus Exporter
    @app.get("/metrics")
    def prometheus_metrics() -> Response:
        from aegis.api.prometheus import generate_prometheus_metrics
        text = generate_prometheus_metrics(ctx)
        return Response(content=text, media_type="text/plain; version=0.0.4; charset=utf-8")

    # ------------------------------------------------------------- Predictive Forecasting
    @app.get("/api/forecasting/forecasts")
    def get_forecasts() -> dict[str, Any]:
        mem = ctx.forecaster.forecast_exhaustion("memory_percent", capacity=100.0)
        snap = ctx.metrics.latest() or {}
        disks = snap.get("disks", [])
        disk_forecasts = {}
        for d in disks:
            mount = d.get("mount", "/")
            f = ctx.forecaster.forecast_exhaustion(f"disk_{mount}", capacity=100.0)
            disk_forecasts[mount] = f.as_dict()
        return {
            "memory": mem.as_dict(),
            "disks": disk_forecasts,
        }

    # ------------------------------------------------------------- Autonomous Self-Healing
    @app.post("/api/self_healing/reconcile", dependencies=[Depends(_auth_dp(ctx))])
    def trigger_self_healing() -> dict[str, Any]:
        report = ctx.self_healing.run_reconciliation_cycle()
        return report.as_dict()

    # ------------------------------------------------------------- Synthetic Probers
    @app.get("/api/probers/summary")
    def get_prober_summary() -> dict[str, Any]:
        return ctx.prober.get_summary()

    @app.post("/api/probers/targets", dependencies=[Depends(_auth_dp(ctx))])
    def add_probe_target(payload: dict[str, Any]) -> dict[str, Any]:
        from aegis.orchestration.probers import ProbeTarget
        target = ProbeTarget(
            name=payload.get("name", f"target-{int(time.time())}"),
            target_type=payload.get("target_type", "http"),
            endpoint=payload.get("endpoint", ""),
            sla_latency_ms=float(payload.get("sla_latency_ms", 500.0)),
            timeout_s=float(payload.get("timeout_s", 5.0)),
            expected_status=int(payload.get("expected_status", 200)),
            expected_substring=payload.get("expected_substring", ""),
        )
        ctx.prober.add_target(target)
        return {"added": target.name, "target": target.__dict__}

    # ------------------------------------------------------------- Cluster Mesh (v2.0)
    @app.post("/api/cluster/nodes/register", dependencies=[Depends(_auth_dp(ctx))])
    def cluster_register_node(payload: dict[str, Any]) -> dict[str, Any]:
        node_id = payload.get("node_id") or f"node-{secrets.token_hex(4)}"
        hostname = payload.get("hostname", "unknown-host")
        ip = payload.get("ip_address")
        labels = payload.get("labels", {})
        node = ctx.cluster.register_node(node_id, hostname, ip, labels)
        return node.as_dict()

    @app.post("/api/cluster/nodes/{node_id}/heartbeat", dependencies=[Depends(_auth_dp(ctx))])
    def cluster_node_heartbeat(node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        telemetry = payload.get("telemetry_summary", {})
        ok = ctx.cluster.record_heartbeat(node_id, telemetry)
        if not ok:
            raise HTTPException(status_code=404, detail="Node not registered in cluster mesh")
        return {"heartbeat_recorded": True, "node_id": node_id}

    @app.get("/api/cluster/nodes")
    def cluster_list_nodes() -> list[dict[str, Any]]:
        ctx.cluster.evaluate_cluster_health()
        return ctx.cluster.list_nodes()

    @app.get("/api/cluster/summary")
    def cluster_summary() -> dict[str, Any]:
        ctx.cluster.evaluate_cluster_health()
        return ctx.cluster.cluster_summary()

    @app.post("/api/cluster/nodes/{node_id}/drain", dependencies=[Depends(_auth_dp(ctx))])
    def cluster_drain_node(node_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reason = (payload or {}).get("reason", "Operator requested drain")
        ok = ctx.cluster.drain_node(node_id, reason)
        if not ok:
            raise HTTPException(status_code=404, detail="Node not found in cluster mesh")
        return {"drained": True, "node_id": node_id, "reason": reason}

    # ------------------------------------------------------------- Canary & Rollbacks (v2.0)
    @app.post("/api/canary/rollouts", dependencies=[Depends(_auth_dp(ctx))])
    def canary_init(payload: dict[str, Any]) -> dict[str, Any]:
        target = payload.get("target")
        if not target:
            raise HTTPException(status_code=422, detail="target required")
        steps = int(payload.get("total_steps", 3))
        record = ctx.canary.init_rollout(target, total_steps=steps)
        return record.as_dict()

    @app.post("/api/canary/rollouts/{rollout_id}/evaluate", dependencies=[Depends(_auth_dp(ctx))])
    def canary_evaluate(rollout_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        err_rate = float(payload.get("error_rate", 0.0))
        lat_ms = float(payload.get("latency_ms", 50.0))

        # Optional simulated rollback callback
        def rollback_action():
            ctx.events.emit("canary", f"Rollback executed for {rollout_id}", "warning")
            return True

        record = ctx.canary.evaluate_step(
            rollout_id=rollout_id,
            live_error_rate=err_rate,
            live_latency_ms=lat_ms,
            rollback_fn=rollback_action,
        )
        return record.as_dict()

    @app.get("/api/canary/rollouts")
    def canary_list() -> list[dict[str, Any]]:
        return ctx.canary.list_rollouts()

    @app.post("/api/canary/rollouts/{rollout_id}/rollback", dependencies=[Depends(_auth_dp(ctx))])
    def canary_rollback(rollout_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reason = (payload or {}).get("reason", "Operator manual rollback")
        try:
            record = ctx.canary.trigger_rollback(rollout_id, reason=reason)
            return record.as_dict()
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ------------------------------------------------------------- Notifications & Webhooks (v2.0)
    @app.get("/api/notifications/channels")
    def list_notification_channels() -> list[dict[str, Any]]:
        return ctx.dispatcher.list_channels()

    @app.post("/api/notifications/channels", dependencies=[Depends(_auth_dp(ctx))])
    def add_notification_channel(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        url = payload.get("webhook_url")
        ctype = payload.get("channel_type", "webhook")
        min_sev = payload.get("min_severity", "warning")
        if not name or not url:
            raise HTTPException(status_code=422, detail="name and webhook_url required")
        chan = ctx.dispatcher.add_channel(name, ctype, url, min_sev)
        return chan.as_dict()

    @app.post("/api/notifications/test", dependencies=[Depends(_auth_dp(ctx))])
    def test_notification_alert(payload: dict[str, Any]) -> dict[str, Any]:
        title = payload.get("title", "Aegis AI Test Alert")
        msg = payload.get("message", "Test alert notification from Aegis 2.0")
        sev = payload.get("severity", "warning")
        return ctx.dispatcher.dispatch_alert(title, msg, sev)

    # ------------------------------------------------------------- RBAC & Security (v2.0)
    @app.get("/api/security/keys", dependencies=[Depends(_auth_dp(ctx))])
    def list_api_keys() -> list[dict[str, Any]]:
        return ctx.rbac.list_keys()

    @app.post("/api/security/keys", dependencies=[Depends(_auth_dp(ctx))])
    def create_api_key(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        role = payload.get("role", "OPERATOR")
        scopes = payload.get("scopes")
        if not name:
            raise HTTPException(status_code=422, detail="Key name required")
        try:
            raw_token, record = ctx.rbac.create_api_key(name, role=role, custom_scopes=scopes)
            return {
                "token": raw_token,
                "key_id": record.key_id,
                "name": record.name,
                "role": record.role,
                "scopes": record.scopes,
                "note": "Save this token now; it is cryptographically hashed and cannot be retrieved again."
            }
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # ------------------------------------------------------------- AI Models & Deployer (v3.0)
    @app.get("/api/models/recommendations")
    def model_recommendations(task: str | None = None) -> dict[str, Any]:
        snap = ctx.snapshot()
        gpu_data = ctx.gpu.sample()
        profile = ctx.models.extract_profile(snap, gpu_data)
        return ctx.models.get_recommendations(profile, task=task)

    @app.get("/api/models/catalog")
    def model_catalog() -> list[dict[str, Any]]:
        return [m.as_dict() for m in ctx.models.catalog]

    @app.post("/api/models/deploy", dependencies=[Depends(_auth_dp(ctx))])
    def model_deploy(payload: dict[str, Any]) -> dict[str, Any]:
        model_name = payload.get("model_name")
        if not model_name:
            raise HTTPException(status_code=422, detail="model_name required")
        runtime = payload.get("runtime", "docker").lower()
        port = int(payload.get("port", 11434))
        gpu = bool(payload.get("gpu", True))

        if runtime == "docker":
            return ctx.deployer.deploy_docker(model_name=model_name, port=port, gpu=gpu)
        elif runtime in ("k8s", "kubernetes"):
            namespace = payload.get("namespace", "default")
            replicas = int(payload.get("replicas", 1))
            return ctx.deployer.deploy_k8s(
                model_name=model_name,
                namespace=namespace,
                port=port,
                gpu=gpu,
                replicas=replicas,
            )
        else:
            raise HTTPException(status_code=422, detail=f"Unsupported runtime '{runtime}'; must be 'docker' or 'k8s'")

    @app.get("/api/models/deployments")
    def list_model_deployments(limit: int = 50) -> list[dict[str, Any]]:
        return ctx.deployer.list_deployments(limit=limit)

    @app.post("/api/models/deployments/{deployment_id}/stop", dependencies=[Depends(_auth_dp(ctx))])
    def stop_model_deployment(deployment_id: str) -> dict[str, Any]:
        ok = ctx.deployer.stop_deployment(deployment_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Deployment not found")
        return {"stopped": True, "status": "stopped", "deployment_id": deployment_id}

    @app.post("/api/orchestration/deploy/docker", dependencies=[Depends(_auth_dp(ctx))])
    def orchestration_deploy_docker(payload: dict[str, Any]) -> dict[str, Any]:
        model_name = payload.get("model_name", "qwen2.5-coder:7b")
        port = int(payload.get("port", 11434))
        gpu = bool(payload.get("gpu", True))
        return ctx.deployer.deploy_docker(model_name=model_name, port=port, gpu=gpu)

    @app.post("/api/orchestration/deploy/k8s", dependencies=[Depends(_auth_dp(ctx))])
    def orchestration_deploy_k8s(payload: dict[str, Any]) -> dict[str, Any]:
        model_name = payload.get("model_name", "qwen2.5-coder:7b")
        namespace = payload.get("namespace", "default")
        port = int(payload.get("port", 11434))
        gpu = bool(payload.get("gpu", True))
        replicas = int(payload.get("replicas", 1))
        return ctx.deployer.deploy_k8s(
            model_name=model_name,
            namespace=namespace,
            port=port,
            gpu=gpu,
            replicas=replicas,
        )

    @app.get("/api/orchestration/manifests/k8s")
    def get_k8s_manifest(model_name: str = "qwen2.5-coder:7b", namespace: str = "default", port: int = 11434, gpu: bool = True) -> dict[str, Any]:
        yaml_content = ctx.deployer.generate_k8s_manifest(model_name=model_name, namespace=namespace, port=port, gpu=gpu)
        return {"model_name": model_name, "namespace": namespace, "manifest_yaml": yaml_content, "yaml": yaml_content}

    @app.post("/api/orchestration/manifests/k8s")
    def post_k8s_manifest(payload: dict[str, Any]) -> dict[str, Any]:
        model_name = payload.get("model_name", "qwen2.5-coder:7b")
        namespace = payload.get("namespace", "default")
        port = int(payload.get("port", 11434))
        gpu = bool(payload.get("gpu", True))
        replicas = int(payload.get("replicas", 1))
        yaml_content = ctx.deployer.generate_k8s_manifest(
            model_name=model_name,
            namespace=namespace,
            port=port,
            gpu=gpu,
            replicas=replicas,
        )
        return {"model_name": model_name, "namespace": namespace, "manifest_yaml": yaml_content, "yaml": yaml_content}

    # ------------------------------------------------------------ policy & demo
    @app.post("/api/policy/mode", dependencies=[Depends(_auth_dp(ctx))])
    def set_mode(req: ModeRequest) -> dict[str, str]:
        ctx.set_mode(req.mode)
        return {"mode": ctx.flags.mode}

    @app.post("/api/policy/emergency_stop", dependencies=[Depends(_auth_dp(ctx))])
    def set_estop(req: EmergencyStopRequest) -> dict[str, Any]:
        ctx.set_emergency_stop(req.enabled)
        return {"emergency_stop": ctx.flags.emergency_stop}

    @app.get("/api/demo")
    def demo_status() -> dict[str, Any]:
        return {
            "enabled": ctx.config.monitoring.simulated_overrides_enabled,
            "kinds": DEMO_KINDS,
            "active_overrides": {
                s: {"value_c": e.value_c, "note": e.note,
                    "expires_in_s": None if e.expires_at is None
                    else round(e.expires_at - time.time(), 1)}
                for s, e in ctx.overrides.active().items()
            },
        }

    @app.post("/api/demo/override")
    def demo_override(req: OverrideRequest,
                      authorization: str | None = Header(default=None)
                      ) -> dict[str, Any]:
        _guard_state_changer(ctx, authorization)
        if not ctx.config.monitoring.simulated_overrides_enabled:
            raise HTTPException(
                status_code=403,
                detail="simulated_overrides_enabled=false in config.yaml")
        try:
            _apply_demo_kind(ctx, req.kind, req.value_c)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ctx.events.emit("demo", f"Simulated override applied: {req.kind}",
                        "warning", override_kind=req.kind)
        return {"ok": True, "active_overrides": list(ctx.overrides.active())}

    @app.post("/api/admin/shutdown")
    def shutdown(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _guard_state_changer(ctx, authorization)
        stop_file = Path(ctx.config.database.path).parent / "STOP"
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text("requested")
        return {"requested": True}

    # -------------------------------------------------------------- frontend
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static_assets")
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True),
              name="static")
    return app


def _auth_dp(ctx: AppContext):
    def check(authorization: str | None = Header(default=None)) -> None:
        _guard_state_changer(ctx, authorization)
    return check


def _apply_demo_kind(ctx: AppContext, kind: str, value_c: float | None) -> None:
    sid = VIRTUAL_CPU_SENSOR_ID
    if kind == "clear":
        ctx.overrides.clear()
    elif kind == "unavailable":
        ctx.overrides.clear(sid)
    elif kind == "warning":
        ctx.overrides.inject(sid, value_c or 90.0,
                             note="demo: warning threshold", ttl_s=600)
    elif kind == "critical":
        ctx.overrides.inject(sid, value_c or 95.0,
                             note="demo: critical threshold", ttl_s=600)
    elif kind == "rapid_rise":
        ctx.overrides.inject(sid, value_c or 80.0,
                             note="demo: rapid rise", ttl_s=240)
    elif kind == "recover":
        ctx.overrides.inject(sid, value_c or 40.0,
                             note="demo: recovery stabilization", ttl_s=900)
