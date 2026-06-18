#!/usr/bin/env bash
# Install/uninstall the com.agentos.bot LaunchAgent — the boot-survival
# service for the persistent Discord gateway.
#
# Replaces the manual `nohup scripts/autorestart.sh &`. launchd starts the
# wrapper at login (RunAtLoad) and respawns it if it ever dies (KeepAlive).
# The wrapper still owns the in-session 3s restart loop and the
# logs/.restart-requested signal — launchd only catches the cases the
# wrapper itself cannot (reboot, wrapper killed, OOM of the group).
#
# Usage:
#   scripts/install_bot_service.sh             # stop manual stack, install, start
#   scripts/install_bot_service.sh --uninstall # bootout + delete plist + stop
#   scripts/install_bot_service.sh --status    # show service + process state
#
# WHY the anchored pkill patterns matter: a bare `pkill -f autorestart.sh`
# also matches unrelated processes whose argv merely CONTAINS that string
# (e.g. the Claude Agent SDK that may be running this very script). We anchor
# to `scripts/autorestart.sh` so only the real wrapper is hit. `Python bot.py`
# (capital P = the framework python binary) is the exact pattern restart.sh
# uses and matches only the gateway process.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LABEL="com.agentos.bot"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${ROOT}/logs"
SERVICE_LOG="${LOG_DIR}/bot-service.log"

# Anchored patterns — see header note. WRAPPER_PAT must stay anchored to
# scripts/ to avoid nuking the agent that runs this installer.
WRAPPER_PAT="scripts/autorestart.sh"
BOT_PAT="Python bot.py"

_log() { echo "[install_bot_service] $*"; }

stop_manual_stack() {
    # Order matters: kill the wrapper FIRST so it can't relaunch bot.py while
    # we're killing bot.py, then kill bot.py. Escalate to SIGKILL if needed.
    _log "stopping any manual autorestart.sh wrapper (pattern: ${WRAPPER_PAT})"
    pkill -f "$WRAPPER_PAT" 2>/dev/null || true
    sleep 1
    if pgrep -f "$WRAPPER_PAT" >/dev/null 2>&1; then
        _log "wrapper still alive after SIGTERM — sending SIGKILL"
        pkill -9 -f "$WRAPPER_PAT" 2>/dev/null || true
        sleep 1
    fi

    _log "stopping any orphan bot.py (pattern: ${BOT_PAT})"
    pkill -f "$BOT_PAT" 2>/dev/null || true
    sleep 2
    if pgrep -f "$BOT_PAT" >/dev/null 2>&1; then
        _log "bot.py still alive after SIGTERM — sending SIGKILL"
        pkill -9 -f "$BOT_PAT" 2>/dev/null || true
        sleep 1
    fi

    if pgrep -f "$BOT_PAT" >/dev/null 2>&1 || pgrep -f "$WRAPPER_PAT" >/dev/null 2>&1; then
        _log "WARNING: a process survived. Inspect with:"
        _log "    pgrep -fl '$WRAPPER_PAT'; pgrep -fl '$BOT_PAT'"
        _log "Aborting to avoid double-running under launchd."
        exit 1
    fi
    _log "manual stack clear."
}

write_plist() {
    mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
    # PATH is the full login PATH the scheduler bakes in, so subprocesses the
    # bot shells out to (git, etc.) resolve even when launchd's boot PATH is
    # minimal. The venv itself is guaranteed because autorestart.sh calls
    # ./.venv/bin/python from WorkingDirectory.
    local path_val="/Users/celainc/.pyenv/versions/3.9.16/bin:/Users/celainc/.rvm/gems/ruby-2.7.6/bin:/Users/celainc/.rvm/gems/ruby-2.7.6@global/bin:/Users/celainc/.rvm/rubies/ruby-2.7.6/bin:/Users/celainc/.local/bin:/Users/celainc/.pyenv/shims:/Users/celainc/.codeium/windsurf/bin:/Users/celainc/.bun/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Library/Apple/usr/bin"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>${path_val}</string>
	</dict>
	<key>Label</key>
	<string>${LABEL}</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>${ROOT}/scripts/autorestart.sh</string>
	</array>
	<key>WorkingDirectory</key>
	<string>${ROOT}</string>
	<key>StandardOutPath</key>
	<string>${SERVICE_LOG}</string>
	<key>StandardErrorPath</key>
	<string>${SERVICE_LOG}</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ProcessType</key>
	<string>Interactive</string>
</dict>
</plist>
EOF
    _log "wrote ${PLIST}"
}

bootstrap_service() {
    # bootout any prior copy (ignore failure if not loaded), then bootstrap.
    launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
    if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
        _log "ERROR: bootstrap failed. Check: launchctl print ${DOMAIN}/${LABEL}"
        exit 1
    fi
    # kickstart -k: (re)start now even though RunAtLoad already triggered, so
    # install is immediate and idempotent.
    launchctl kickstart -k "${DOMAIN}/${LABEL}" 2>/dev/null || true
    _log "bootstrapped + kickstarted ${LABEL} in ${DOMAIN}"
}

uninstall() {
    _log "uninstalling ${LABEL}"
    launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
    if [ -f "$PLIST" ]; then
        rm -f "$PLIST"
        _log "deleted ${PLIST}"
    fi
    # Kill any wrapper/bot the service spawned so we don't leave an orphan.
    pkill -f "$WRAPPER_PAT" 2>/dev/null || true
    sleep 1
    pkill -f "$BOT_PAT" 2>/dev/null || true
    _log "uninstalled. (verify: launchctl print ${DOMAIN}/${LABEL} should fail)"
}

status() {
    if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
        echo "service: loaded (${DOMAIN}/${LABEL})"
    else
        echo "service: not loaded"
    fi
    pgrep -fl "$BOT_PAT" 2>/dev/null || echo "bot.py: not running"
}

case "${1:-}" in
    --uninstall) uninstall ;;
    --status)    status ;;
    "")
        stop_manual_stack
        write_plist
        bootstrap_service
        _log "done. tail -f ${SERVICE_LOG}  (and logs/bot.log for gateway output)"
        ;;
    *)
        echo "usage: $0 [--uninstall|--status]" >&2
        exit 2
        ;;
esac
