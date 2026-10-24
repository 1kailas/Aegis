-- Aegis v3.0 Migration: One-Click Model & Workload Deployments
CREATE TABLE IF NOT EXISTS model_deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT UNIQUE NOT NULL,
    model_name TEXT NOT NULL,
    runtime TEXT NOT NULL, -- docker, k8s, ollama
    status TEXT NOT NULL,  -- PROVISIONING, RUNNING, FAILED, STOPPED
    target TEXT,           -- container_id or k8s deployment name
    port INTEGER,
    manifest TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deployments_status ON model_deployments(status);
CREATE INDEX IF NOT EXISTS idx_deployments_created ON model_deployments(created_at);
