#!/usr/bin/env bash
# Triggers the planted outage bug on the demo target app (HTTP 500)
set -e
TARGET_URL="${1:-http://127.0.0.1:5050}"
echo "Triggering defect on ${TARGET_URL}..."
curl -s -X POST "${TARGET_URL}/break"
echo ""
echo "Current health check response:"
curl -s -i "${TARGET_URL}/health" | head -n 12
echo ""
