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
        local up want
        # Count only since the most recent "starting N Discord" marker —
        # under autorestart.sh the log appends across restarts, so a naive
        # grep -c would sum every historical login.
        up="$(awk '/starting [0-9]+ Discord/{n=0} /logged in as/{n++} END{print n+0}' "$LOG" 2>/dev/null || echo 0)"
        up="${up:-0}"
        want="$(grep -aoE 'starting [0-9]+ Discord' "$LOG" 2>/dev/null | tail -1 | grep -oE '[0-9]+' || true)"
        want="${want:-?}"
        echo "clients: $up / $want"
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
# `want` = the N the bot announces at startup ("starting N Discord client(s)").
# That's agents grouped by unique bot_token, which is the real Discord client count.
want=""
for i in $(seq 1 10); do
    want="$(grep -aoE 'starting [0-9]+ Discord' "$LOG" 2>/dev/null | tail -1 | grep -oE '[0-9]+' || true)"
    [ -n "$want" ] && break
    sleep 1
done
want="${want:-0}"
for i in $(seq 1 40); do
    up="$(awk '/starting [0-9]+ Discord/{n=0} /logged in as/{n++} END{print n+0}' "$LOG" 2>/dev/null || echo 0)"
    up="${up:-0}"
    if [ "$want" -gt 0 ] && [ "${up:-0}" -ge "${want:-0}" ]; then
        echo "✅ $up/$want clients up"
        break
    fi
    sleep 1
done

if grep -aqE "Error|Traceback" "$LOG"; then
    echo ""
    echo "⚠️  errors in log:"
    grep -aE "Error|Traceback" "$LOG" | head -5
fi

echo ""
_status
