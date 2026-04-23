#!/usr/bin/env bash
# update.sh — pull latest, install deps, signal bot restart.
#
# This is the "auto-update" pattern we borrow from OpenClaw. It does NOT
# run unattended — operator invokes it manually or from a cron. The
# restart is cooperative (touch logs/.restart-requested; bot exits cleanly
# between turns; autorestart.sh brings it back up).
#
# Usage:
#   ./scripts/update.sh                 # pull + install + restart
#   ./scripts/update.sh --check         # check if update available, don't apply
#   ./scripts/update.sh --no-restart    # pull + install, skip restart signal

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="apply"
for arg in "$@"; do
    case "$arg" in
        --check) MODE="check" ;;
        --no-restart) MODE="no-restart" ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

echo "→ fetching remote state"
git fetch --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "$LOCAL")

if [[ "$LOCAL" == "$REMOTE" ]]; then
    echo "✅ up to date ($LOCAL)"
    exit 0
fi

echo "📦 update available:"
git log --oneline "$LOCAL..$REMOTE" | head -10
echo ""

if [[ "$MODE" == "check" ]]; then
    exit 0
fi

# Refuse to update if there are uncommitted changes — avoid silent conflicts.
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "❌ uncommitted changes present. Commit or stash first, then retry."
    exit 1
fi

echo "→ pulling"
git pull --ff-only --quiet

if [[ -f pyproject.toml ]] && [[ -d .venv ]]; then
    echo "→ installing python deps"
    ./.venv/bin/pip install --quiet -e . || true
fi

if [[ -f package.json ]]; then
    echo "→ installing node deps"
    npm install --silent || true
fi

if [[ "$MODE" == "no-restart" ]]; then
    echo "✅ updated. Skipping restart (--no-restart)."
    exit 0
fi

echo "→ signalling bot restart"
mkdir -p logs
touch logs/.restart-requested

echo "✅ updated. Bot will restart after current turn finishes."
echo "   New HEAD: $(git rev-parse --short HEAD)"
