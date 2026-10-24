#!/usr/bin/env bash
# ==============================================================================
# Aegis AI — Devin Hacks Hackathon Demo Launcher
# Tracks: Kill the Busywork / Make the Invisible Visible
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "=================================================================="
echo "  AEGIS AI — AUTONOMOUS ON-CALL SRE FOR ANY WEBSITE OR APP"
echo "  Devin Hacks Hackathon Demonstration Environment"
echo "=================================================================="

# 1. Environment & Database Check
mkdir -p data logs
export PYTHONPATH="${REPO_ROOT}"

if [ "$1" == "--reset" ]; then
    echo "▶ Resetting demo database..."
    rm -f data/aegis.db data/aegis.db-wal data/aegis.db-shm
fi

# 2. Check virtualenv
if [ -d ".venv" ]; then
    PYTHON=".venv/bin/python3"
else
    PYTHON="python3"
fi

# 3. Stop any existing demo target on port 5050
echo "▶ Ensuring port 5050 is free for demo target app..."
fuser -k 5050/tcp 2>/dev/null || true

# 4. Start Demo Target Flask App on port 5050
echo "▶ Launching Demo Target Microservice on http://127.0.0.1:5050..."
nohup "${PYTHON}" demo-target/app.py > logs/demo_target.log 2>&1 &
DEMO_TARGET_PID=$!
disown ${DEMO_TARGET_PID} 2>/dev/null || true
echo "  Demo target started (PID: ${DEMO_TARGET_PID})"

# Wait for demo target to become healthy
for i in {1..10}; do
    if curl -s http://127.0.0.1:5050/health >/dev/null 2>&1; then
        echo "  ✔ Demo target microservice is healthy (HTTP 200 OK)"
        break
    fi
    sleep 0.5
done

# 5. Check main server port 8000
if curl -s http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "  ✔ Aegis main server is already running on http://127.0.0.1:8000"
else
    echo "▶ Starting Aegis AI Control Plane on http://127.0.0.1:8000..."
    nohup "${PYTHON}" -m aegis.main --host 0.0.0.0 --port 8000 --db data/aegis.db > logs/server_8000.log 2>&1 &
    AEGIS_PID=$!
    echo "  Aegis server started (PID: ${AEGIS_PID})"
    for i in {1..15}; do
        if curl -s http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
            echo "  ✔ Aegis control plane ready"
            break
        fi
        sleep 0.5
    done
fi

# 6. Auto-register Demo Target in Aegis
echo "▶ Registering Demo Target in Aegis Target Registry..."
curl -s -X POST http://127.0.0.1:8000/api/targets \
    -H "Content-Type: application/json" \
    -d '{
        "name": "Checkout Payment Service",
        "url": "http://127.0.0.1:5050",
        "health_path": "/health",
        "github_repo_url": "https://github.com/1kailas/Aegis",
        "log_source": "demo-target/logs",
        "interval_s": 4.0
    }' >/dev/null 2>&1 || true

echo ""
echo "=================================================================="
echo "  🎉 DEMO READY FOR JUDGES!"
echo "=================================================================="
echo "  1. Aegis Dashboard:      http://localhost:8000"
echo "  2. Demo Target Service:  http://localhost:5050"
echo "  3. Target Health Check:  http://localhost:5050/health"
echo "  4. Trigger Planted Bug:  bash demo-target/trigger_bug.sh"
echo "  5. Live Fix / Recover:   bash demo-target/fix_bug.sh"
echo "=================================================================="
echo "  Hackathon Judge Demo Flow:"
echo "  1. Open http://localhost:8000 (Notice 'Checkout Payment Service' is HEALTHY)."
echo "  2. Click 'Simulate 500 Outage' or run 'bash demo-target/trigger_bug.sh'."
echo "  3. Observe Aegis detect the outage and create an Incident within 4 seconds."
echo "  4. Watch Gemini Tier 2 diagnose root cause (unhandled NullPointer in checkout.py)."
echo "  5. Inspect the generated Devin Brief with reproduction steps & acceptance criteria."
echo "  6. Click 'Verify & Close Loop' to demonstrate independent live recovery verification."
echo "  7. Click 'Safety Test (403)' to prove deterministic guardrails block dangerous commands."
echo "=================================================================="
