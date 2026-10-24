#!/usr/bin/env bash
# Resolves the planted defect on the demo target app (HTTP 200 OK)
set -e
TARGET_URL="${1:-http://127.0.0.1:5050}"
echo "Fixing defect on ${TARGET_URL}..."
curl -s -X POST "${TARGET_URL}/fix"
echo ""
echo "Current health check response:"
curl -s -i "${TARGET_URL}/health" | head -n 12
echo ""
