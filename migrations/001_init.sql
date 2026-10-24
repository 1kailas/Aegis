-- Aegis schema v1.
-- Tables: hosts, sensors, telemetry, thermal transitions, incidents,
-- actions, jobs, audit. WAL mode is enabled by the DB layer itself.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS host_inventory (
    host_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    platform TEXT,
    kernel TEXT,
    cpu_model TEXT,
    cpu_cores INTEGER,
    total_memory_mb INTEGER,
    registered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sensor_registry (
    sensor_id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL,
    kind TEXT NOT NULL,              -- temperature | fan | voltage | power
    label TEXT NOT NULL,
    source TEXT NOT NULL,            -- hwmon | psutil | nvme | override
    source_path TEXT,
    unit TEXT,
    discovered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sensor_host ON sensor_registry(host_id);

-- High-volume numeric telemetry; pruned by retention policy.
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    host_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    unit TEXT,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts);
CREATE INDEX IF NOT EXISTS idx_telemetry_metric_ts ON telemetry(metric, ts);

-- Full metric snapshots (bulk JSON) kept for dashboards, pruned on retention.
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    host_id TEXT NOT NULL,
    payload TEXT NOT NULL,           -- JSON snapshot of a full sample
    validity TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metric_snapshots_ts ON metric_snapshots(ts);

CREATE TABLE IF NOT EXISTS thermal_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    host_id TEXT NOT NULL,
    sensor_id TEXT,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    trigger_value_c REAL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,             -- INC-YYYYmmddTHHMMSSmmm
    host_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    evidence TEXT,                   -- JSON array of evidence refs
    lifecycle TEXT,                  -- JSON array of transitions
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_state ON incidents(state);
CREATE INDEX IF NOT EXISTS idx_incidents_ts ON incidents(created_at);

CREATE TABLE IF NOT EXISTS remediation_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    incident_id TEXT,
    action TEXT NOT NULL,
    target TEXT,
    reason TEXT,
    mode TEXT NOT NULL,              -- real | dry_run
    result TEXT NOT NULL,            -- VERIFIED | REJECTED | BLOCKED | FAILED
    detail TEXT                      -- JSON: pre/post state, verification
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,             -- QUEUED | RUNNING | DONE | FAILED | CANCELLED
    payload TEXT,
    result TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    timeout_s REAL NOT NULL DEFAULT 300,
    claimed_by TEXT,
    claim_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT,
    detail TEXT
);
