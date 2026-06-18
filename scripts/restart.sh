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
    # service: is com.agentos.bot loaded under launchd (gui domain)?
    if launchctl print "gui/$(id -u)/com.agentos.bot" >/dev/null 2>&1; then
        echo "service: ✅ loaded (com.agentos.bot)"
    else
        echo "service: ❌ not loaded (run scripts/install_bot_service.sh)"
    fi
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

# If the launchd service (com.agentos.bot) owns the gateway, a manual
# pkill + relaunch here would race the KeepAlive wrapper and briefly
# double-run bot.py into Discord. Delegate the restart to launchd instead.
if launchctl print "gui/$(id -u)/com.agentos.bot" >/dev/null 2>&1; then
    echo "service com.agentos.bot is loaded — restarting via launchctl kickstart"
    launchctl kickstart -k "gui/$(id -u)/com.agentos.bot" || true
    sleep 3
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
# bot.py owns logs/bot.log via its rotating handler; send the shell's
# stdout/stderr to a separate console file so the two don't fight over the
# rotated file. restart.sh still parses "$LOG" (bot.log) for the markers.
./.venv/bin/python bot.py > logs/bot.console.log 2>&1 &
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
