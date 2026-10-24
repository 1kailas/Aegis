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
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
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
        if v not in ("monitoring_only", "policy_autonomous"):
            raise ValueError("mode must be monitoring_only or policy_autonomous")
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
    token = ctx.config.server.api_token
    if not token:
        return  # local development binding only
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Missing or invalid bearer token")


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Aegis AI", version=__version__,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

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
                        "warning", kind=req.kind)
        return {"ok": True, "active_overrides": list(ctx.overrides.active())}

    @app.post("/api/admin/shutdown")
    def shutdown(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _guard_state_changer(ctx, authorization)
        stop_file = Path(ctx.config.database.path).parent / "STOP"
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text("requested")
        return {"requested": True}

    # -------------------------------------------------------------- frontend
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
