#!/usr/bin/env bash
# Restart the relay cleanly. Shows what's currently running, kills it,
# starts the new one, waits for all clients to reconnect, tails any errors.
#
# Usage:
#   scripts/restart.sh           # normal restart
#   scripts/restart.sh --status  # show current state without restarting

set -euo pipefail

cd "$(dirname "$0")/.."
LOG="logs/bot.log"
mkdir -p logs

_status() {
    local pids
    pids="$(pgrep -f "Python bot.py" || true)"
    if [ -z "$pids" ]; then
        echo "bot: ❌ not running"
        return 1
    fi
    echo "bot: ✅ running"
    ps -o pid,lstart,cmd -p $pids 2>/dev/null | head -5 || true
    if [ -f "$LOG" ]; then
        local up
        up="$(grep -c 'logged in as' "$LOG" 2>/dev/null || echo 0)"
        local want
        want="$(ls -1 agents/ | grep -Ev '^(_|\.)' | wc -l | tr -d ' ')"
        echo "clients: $up / $want declared"
    fi
    return 0
}

if [ "${1:-}" = "--status" ]; then
    _status
    exit 0
fi

echo "── stopping ──"
pkill -f "Python bot.py" 2>/dev/null || true
sleep 2
if pgrep -f "Python bot.py" >/dev/null; then
    echo "⚠️  bot.py still running after SIGTERM, sending SIGKILL"
    pkill -9 -f "Python bot.py" 2>/dev/null || true
    sleep 1
fi

echo "── starting ──"
./.venv/bin/python bot.py > "$LOG" 2>&1 &
BOT_PID=$!
echo "pid: $BOT_PID"

echo "── waiting for clients to connect ──"
want="$(ls -1 agents/ | grep -Ev '^(_|\.)' | wc -l | tr -d ' ')"
for i in $(seq 1 40); do
    up="$(grep -c 'logged in as' "$LOG" 2>/dev/null || echo 0)"
    if [ "$up" -ge "$want" ]; then
        echo "✅ $up/$want clients up"
        break
    fi
    sleep 1
done

if grep -qE "Error|Traceback" "$LOG"; then
    echo ""
    echo "⚠️  errors in log:"
    grep -E "Error|Traceback" "$LOG" | head -5
fi

echo ""
_status
