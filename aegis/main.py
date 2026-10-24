"""Aegis CLI entrypoint.

Usage: aegis [--host H] [--port P] [--config PATH] [--policies PATH]
             [--db PATH] [--log-level LEVEL]

The engine runs in a daemon thread; uvicorn serves the API in another.
Shutdown (Ctrl+C / SIGTERM / the STOP file written via the control
plane) stops the engine first, then closes the database.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

import uvicorn

from aegis import __version__
from aegis.appstate import AppContext
from aegis.config import load_config

_LOG = logging.getLogger("aegis.main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="aegis", description="Aegis AI operations backend")
    p.add_argument("--host", default=None, help="bind address (default from config)")
    p.add_argument("--port", type=int, default=None, help="port (default from config)")
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("--policies", default=None, help="path to policies.yaml")
    p.add_argument("--db", default=None, help="SQLite database path")
    p.add_argument("--log-level", default=None)
    return p.parse_args(argv)


def build_context(args: argparse.Namespace) -> AppContext:
    config = load_config(config_path=args.config, policies_path=args.policies)
    if args.db:
        config.database.path = Path(args.db)
    if args.log_level:
        config.logging.level = args.log_level.upper()
    return AppContext(config)


def run(ctx: AppContext, host: str | None, port: int | None) -> None:
    """Server loop that also honours the control-plane STOP file."""
    from aegis.api.server import create_app
    from aegis.engine import MonitoringEngine

    app = create_app(ctx)
    engine = MonitoringEngine(ctx)
    ctx.engine = engine
    engine.start()

    config = uvicorn.Config(
        app,
        host=host or ctx.config.server.host,
        port=port or ctx.config.server.port,
        log_level=ctx.config.logging.level.lower(),
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True,
                                     name="aegis-server")
    server_thread.start()

    stop_file = ctx.config.database.path.parent / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        while not server.should_exit:
            if stop_file.exists():
                _LOG.info("STOP file detected: shutting down gracefully")
                stop_file.unlink()
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        _LOG.info("Interrupt received: shutting down gracefully")
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        try:
            engine.stop(timeout=10)
        except Exception:
            _LOG.exception("Engine stop failed")
        ctx.close()
        _LOG.info("Aegis shut down")


def main() -> int:
    args = parse_args()
    try:
        ctx = build_context(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 2
    ctx.log.info("Aegis %s starting (host=%s mode=%s)", __version__,
                 ctx.config.server.host, ctx.flags.mode)
    has_gemini = bool(ctx.config.gemini_api_key and len(ctx.config.gemini_api_key.strip()) > 8)
    has_devin = bool(ctx.devin.api_key or ctx.devin._desktop_cli)
    ctx.log.info(
        "AI Diagnostic & Repair Startup: Gemini configured=%s (model=%s, budget=%s/hr), Devin configured=%s",
        has_gemini,
        ctx.config.models.gemini_model,
        ctx.config.models.gemini_max_requests_per_hour,
        has_devin,
    )
    run(ctx, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
