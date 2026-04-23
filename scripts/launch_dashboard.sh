#!/bin/bash
# launch_dashboard.sh — boot the AgentOS dashboard + ngrok tunnel.
# Idempotent: safe to run under launchd KeepAlive.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CREDS_FILE="logs/dashboard-creds.txt"
DASH_USER="${DASHBOARD_USER:-agentos}"
mkdir -p logs

# Ensure creds exist (generate on first run only)
if [ ! -f "$CREDS_FILE" ]; then
    PASS=$(openssl rand -hex 6)
    echo "generated pass: $PASS" > "$CREDS_FILE"
    echo "$DASH_USER:$PASS" >> "$CREDS_FILE"
    chmod 600 "$CREDS_FILE"
fi
AUTH=$(tail -n 1 "$CREDS_FILE")

# Start dashboard if not already running
if ! pgrep -f "dashboard.py --port 8080" > /dev/null 2>&1; then
    nohup ./.venv/bin/python dashboard.py --port 8080 \
        > logs/dashboard.log 2>&1 &
    sleep 1
fi

# Start ngrok if not already running
if ! pgrep -f "ngrok http 8080" > /dev/null 2>&1; then
    nohup /opt/homebrew/bin/ngrok http 8080 \
        --basic-auth "$AUTH" --log=stdout \
        > logs/ngrok.log 2>&1 &
fi

echo "launched at $(date -u +%FT%TZ)" >> logs/launch_dashboard.log
