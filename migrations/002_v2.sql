-- Aegis schema v2.
-- Adds cluster mesh nodes, canary rollout tracking, RBAC API keys, and notification channels.

CREATE TABLE IF NOT EXISTS cluster_nodes (
    node_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    ip_address TEXT,
    status TEXT NOT NULL,           -- ONLINE | DEGRADED | OFFLINE | DRAINED
    labels TEXT,                    -- JSON object of labels
    telemetry_summary TEXT,         -- JSON object of latest vitals
    last_heartbeat_at TEXT NOT NULL,
    registered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cluster_node_status ON cluster_nodes(status);

CREATE TABLE IF NOT EXISTS canary_rollouts (
    rollout_id TEXT PRIMARY KEY,
    target TEXT NOT NULL,           -- container name or deployment path
    state TEXT NOT NULL,            -- CANARY_INIT | CANARY_OBSERVING | PROMOTED | ROLLING_BACK | ROLLED_BACK
    current_step INTEGER NOT NULL DEFAULT 1,
    total_steps INTEGER NOT NULL DEFAULT 3,
    error_rate REAL DEFAULT 0.0,
    latency_ms REAL DEFAULT 0.0,
    revert_trigger TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canary_state ON canary_rollouts(state);

CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,             -- ADMIN | OPERATOR | READONLY | NODE_AGENT
    scopes TEXT NOT NULL,           -- JSON array of granted permission strings
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_token_hash ON api_keys(token_hash);

CREATE TABLE IF NOT EXISTS notification_channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel_type TEXT NOT NULL,     -- webhook | slack | pagerduty | generic_json
    webhook_url TEXT NOT NULL,
    min_severity TEXT NOT NULL DEFAULT 'warning', -- info | warning | critical
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
