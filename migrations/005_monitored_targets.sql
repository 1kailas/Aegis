-- Aegis v3.2 Migration: Monitored Website Targets
CREATE TABLE IF NOT EXISTS monitored_targets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    health_path TEXT NOT NULL DEFAULT '/health',
    github_repo_url TEXT NOT NULL DEFAULT '',
    log_source TEXT DEFAULT '',
    interval_s REAL NOT NULL DEFAULT 5.0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_status_code INTEGER,
    last_latency_ms REAL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    last_error TEXT,
    last_checked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_monitored_targets_status ON monitored_targets(status);
