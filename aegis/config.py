"""Configuration loading and validation for Aegis.

Sources (in increasing priority):
1. config.yaml      - application settings
2. policies.yaml    - thermal thresholds and action policies
3. .env / process environment - secrets (GEMINI_API_KEY, GITHUB_TOKEN)

Invalid configuration must fail at startup with a clear error, never fall
back to unpredictable defaults. Secrets never come from yaml.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent


class DatabaseConfig(BaseModel):
    path: Path = Path("data/aegis.db")
    telemetry_retention_hours: int = Field(default=24, ge=1, le=24 * 30)


class MonitoringConfig(BaseModel):
    collect_interval_s: float = Field(default=5.0, ge=1.0, le=120.0)
    warning_interval_s: float = Field(default=1.5, ge=0.5, le=60.0)
    critical_interval_s: float = Field(default=1.0, ge=0.5, le=60.0)
    sensor_snapshot_seconds: float = Field(default=30.0, ge=1.0)
    history_points_max: int = Field(default=900, ge=100, le=20000)
    mode: Literal["monitoring_only", "policy_autonomous"] = "monitoring_only"
    simulated_overrides_enabled: bool = True


class ModelsConfig(BaseModel):
    investigator: str = "qwen2.5:1.5b"
    repairer: str = "qwen2.5-coder:1.5b"
    ollama_url: str = "http://127.0.0.1:11434"
    inference_timeout_s: float = 120.0
    gemini_model: str = "gemini-2.5-flash"
    gemini_max_requests_per_incident: int = 1
    gemini_max_requests_per_hour: int = 6
    gemini_max_input_chars: int = 20000
    gemini_timeout_s: float = 60.0


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8480, ge=1, le=65535)
    api_token: str = ""  # optional bearer token for state-changing ops


class LogConfig(BaseModel):
    dir: str = "logs"
    level: str = "INFO"


class ThermalPolicyConfig(BaseModel):
    """Conservative placeholder thresholds; must be calibrated per device."""

    sensor: str = "auto"
    warning_c: float = 75.0
    critical_c: float = 85.0
    recovery_below_c: float = 68.0
    hysteresis_c: float = 3.0
    rate_per_s_warning: float = 0.5
    window_points: int = 12
    stale_after_s: float = 30.0
    recovery_hold_s: float = 60.0
    slope_min_for_estimate: float = 0.05
    min_points_for_slope: int = 5

    @field_validator("window_points")
    @classmethod
    def window_min(cls, v: int) -> int:
        return max(3, v)


class ThermalActionsConfig(BaseModel):
    warning_actions: list[str] = Field(
        default_factory=lambda: ["increase_sampling", "create_or_update_incident"]
    )
    critical_actions: list[str] = Field(
        default_factory=lambda: [
            "increase_sampling",
            "create_or_update_incident",
            "set_backpressure",
        ]
    )
    require_auth_for_destructive: bool = True


class AppConfig(BaseModel):
    database: DatabaseConfig
    monitoring: MonitoringConfig
    models: ModelsConfig
    server: ServerConfig
    logging: LogConfig
    thermal_policy: ThermalPolicyConfig
    action_policy: ThermalActionsConfig
    emergency_stop: bool = False

    # Secrets (from environment only; never from yaml/git)
    gemini_api_key: str | None = None
    github_token: str | None = None

    @property
    def db_path(self) -> Path:
        return self.database.path


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Required configuration file not found: {path}. "
            "Aegis will not start with unpredictable defaults; copy the "
            "example or run scripts/verify_environment.sh."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Expected a mapping at top level of {path}")
    return raw


def _secret_from_env(name: str) -> str | None:
    val = os.environ.get(name)
    if val:
        return val.strip() or None
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or None
    return None


@lru_cache(maxsize=4)
def load_config(
    config_path: str | None = None, policies_path: str | None = None
) -> AppConfig:
    cpath = Path(config_path or os.environ.get("AEGIS_CONFIG", REPO_ROOT / "config.yaml"))
    ppath = Path(policies_path or os.environ.get("AEGIS_POLICIES", REPO_ROOT / "policies.yaml"))

    cfg_raw = _load_yaml(cpath)
    pol_raw = _load_yaml(ppath)

    try:
        app = AppConfig(
            database=DatabaseConfig(**(cfg_raw.get("database") or {})),
            monitoring=MonitoringConfig(**(cfg_raw.get("monitoring") or {})),
            models=ModelsConfig(**(cfg_raw.get("models") or {})),
            server=ServerConfig(**(cfg_raw.get("server") or {})),
            logging=LogConfig(**(cfg_raw.get("logging") or {})),
            thermal_policy=ThermalPolicyConfig(**(pol_raw.get("thermal") or {})),
            action_policy=ThermalActionsConfig(**(pol_raw.get("actions") or {})),
            emergency_stop=bool(pol_raw.get("emergency_stop", False)),
        )
    except ValidationError as exc:
        raise ValueError(f"Configuration validation failed: {exc}") from exc

    # Secrets only via env (or local gitignored .env at repo root).
    app.gemini_api_key = _secret_from_env("GEMINI_API_KEY")
    app.github_token = _secret_from_env("GITHUB_TOKEN")
    env_model = _secret_from_env("GEMINI_MODEL")
    if env_model:
        app.models.gemini_model = env_model

    if not app.database.path.is_absolute():
        app.database.path = (cpath.parent / app.database.path).resolve()
    return app
