"""Logging configuration for Aegis.

Emits human-readable lines to the console and structured JSON lines to
logs/aegis.log so incidents and engine activity can be audited later.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone


def _json_record(record: logging.LogRecord) -> str:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    if record.exc_info:
        payload["exception"] = logging.formatException(record.exc_info)
    extra = getattr(record, "extra_fields", None)
    if isinstance(extra, dict):
        payload.update(extra)
    return json.dumps(payload, default=str)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _json_record(record)


def setup_logging(log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    """Configure root logging. Idempotent: repeated calls update the level only."""
    logger = logging.getLogger("aegis")
    if getattr(logger, "_aegis_configured", False):
        logger.setLevel(level)
        return logger

    os.makedirs(log_dir, exist_ok=True)
    logger.setLevel(level)
    logger.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "aegis.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    logger._aegis_configured = True  # type: ignore[attr-defined]
    return logger
