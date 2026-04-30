#!/usr/bin/env python3
"""Schedule a one-shot deferred run for an agent.

This is the only legitimate way for an agent to "come back later." There is
no daemon, no background loop, no implicit continuation between turns. If
the agent doesn't schedule itself, it doesn't wake up.

Mechanism: writes a one-shot task file under agents/<agent>/tasks/ and a
launchd plist under ~/Library/LaunchAgents/. At the target time, launchd
runs cron_trigger.py against the task file. cron_trigger detects the
oneshot frontmatter and bootouts + deletes both files after the run.

Usage:
    python scripts/defer.py <agent> "in 30m" "Re-check Apollo verification"
    python scripts/defer.py <agent> "15:00" "Send the morning batch"
    python scripts/defer.py <agent> "2026-04-29T18:00" "..."

Time syntax:
    "in <N>(s|m|h|d)"     relative
    "HH:MM"               today (or tomorrow if already past)
    ISO 8601              absolute
"""
from __future__ import annotations

import argparse
import os
import plistlib
import re
import secrets
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.agentos"
TRIGGER = ROOT / "cron_trigger.py"

_VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

_DELTA_RE = re.compile(r"^in\s+(\d+)\s*([smhd])$")
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_when(s: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    s = s.strip()
    m = _DELTA_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "s": timedelta(seconds=n),
            "m": timedelta(minutes=n),
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
        }[unit]
        return now + delta
    m = _HHMM_RE.match(s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return datetime.fromisoformat(s)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("agent", help="Agent name (folder under agents/)")
    p.add_argument("when", help='Time spec: "in 30m", "15:00", or ISO 8601')
    p.add_argument("prompt", help="Prompt body the agent should receive at fire time")
    args = p.parse_args()

    agent_dir = AGENTS_DIR / args.agent
    if not agent_dir.is_dir():
        sys.exit(f"unknown agent: {args.agent} (no agents/{args.agent}/)")

    raw_target = parse_when(args.when)
    now = datetime.now()
    # launchd StartCalendarInterval has minute resolution. Round UP to the
    # next minute so the reported fire time matches when launchd actually
    # fires, and a "30s from now" never accidentally reports "0s from now".
    when = raw_target.replace(second=0, microsecond=0)
    if when <= raw_target:
        when += timedelta(minutes=1)
    if when <= now + timedelta(seconds=10):
        sys.exit(f"refusing to schedule <10s in the future: {when.isoformat()}")
    if when > now + timedelta(days=300):
        sys.exit("defer must be <1 year (launchd StartCalendarInterval has no Year field)")

    tasks_dir = agent_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)

    short_id = secrets.token_hex(3)
    task_name = f"_deferred_{when.strftime('%Y%m%dT%H%M')}_{short_id}"
    task_file = tasks_dir / f"{task_name}.md"
    fm = (
        "---\n"
        "oneshot: true\n"
        f"deferred_at: {now.isoformat(timespec='seconds')}\n"
        f"target: {when.isoformat(timespec='seconds')}\n"
        "---\n"
    )
    task_file.write_text(fm + args.prompt.rstrip() + "\n")

    label = f"{LABEL_PREFIX}.{args.agent}-{task_name}"
    plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = str(log_dir / f"{args.agent}-{task_name}.log")

    plist_data = {
        "Label": label,
        "ProgramArguments": [PYTHON, str(TRIGGER), args.agent, task_name],
        "WorkingDirectory": str(ROOT),
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
        "RunAtLoad": False,
        "StartCalendarInterval": {
            "Month": when.month,
            "Day": when.day,
            "Hour": when.hour,
            "Minute": when.minute,
        },
        "EnvironmentVariables": {
            "PATH": os.environ.get(
                "PATH", "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
            ),
        },
    }
    plist_path.write_bytes(plistlib.dumps(plist_data))

    uid = os.getuid()
    r = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        task_file.unlink(missing_ok=True)
        plist_path.unlink(missing_ok=True)
        sys.exit(
            f"launchctl bootstrap failed (rc={r.returncode}): {r.stderr.strip()}"
        )

    delta = when - now
    print(f"deferred  {label}")
    print(f"  fires:    {when.isoformat(timespec='seconds')}  (in {delta})")
    print(f"  task:     {task_file.relative_to(ROOT)}")
    print(f"  plist:    {plist_path}")
    print(f"  log:      {log_path}")
    print(f"  cleanup:  automatic after first run")


if __name__ == "__main__":
    main()
