#!/usr/bin/env bash
# THE single entry point for AgentOS. Runs bot.py in a loop — whenever
# bot exits (crash, restart signal, SIGTERM), waits 3s and starts fresh.
#
# Usage:
#   scripts/autorestart.sh           # run in foreground (ctrl-C to stop for real)
#   nohup scripts/autorestart.sh &   # run in background, survives terminal close
#
# Agents trigger a restart by:
#   touch logs/.restart-requested
# The bot checks for this file between turns and exits cleanly.
# This wrapper catches the exit and restarts.

set -euo pipefail
cd "$(dirname "$0")/.."

LOG="logs/autorestart.log"
mkdir -p logs

_log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

trap '_log "autorestart wrapper stopped (SIGINT/SIGTERM)"; exit 0' INT TERM

while true; do
    _log "starting bot.py"
    # bot.py now OWNS logs/bot.log via a rotating logging handler. Redirect
    # the shell's stdout/stderr to a SEPARATE file so uncaught tracebacks and
    # bare print()s are still captured WITHOUT a second writer fighting the
    # rotating handler over logs/bot.log. restart.sh's markers come from the
    # logger and still land in logs/bot.log, so it is unaffected.
    ./.venv/bin/python bot.py >> logs/bot.console.log 2>&1 || true
    EXIT_CODE=$?
    _log "bot.py exited (code $EXIT_CODE) — restarting in 3s"
    sleep 3
done
