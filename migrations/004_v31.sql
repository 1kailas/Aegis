-- Aegis v3.1 Migration: Model Benchmarks & Dynamic AI Memory Optimization
CREATE TABLE IF NOT EXISTS model_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    benchmark_id TEXT UNIQUE NOT NULL,
    model_name TEXT NOT NULL,
    runtime TEXT NOT NULL,
    ttft_ms REAL NOT NULL,
    tokens_per_sec REAL NOT NULL,
    total_tokens INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    prompt_eval_tps REAL,
    vram_used_mb REAL,
    status TEXT NOT NULL, -- COMPLETED, FAILED, SIMULATED
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_benchmarks_model ON model_benchmarks(model_name);
CREATE INDEX IF NOT EXISTS idx_benchmarks_created ON model_benchmarks(created_at);
