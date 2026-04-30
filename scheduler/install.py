"""launchd-based scheduler for AgentOS agent tasks.

Replaces cron/install.py. Reads each `agents/<name>/tasks/*.md` for a `cron:`
frontmatter line, converts the cron syntax to a launchd `StartCalendarInterval`
plist, writes it to `~/Library/LaunchAgents/com.agentos.<agent>-<task>.plist`,
and loads it via `launchctl bootstrap`.

Why launchd? macOS privacy gates `crontab` write operations when invoked from a
sandboxed shell (Claude Code, xcode test runners, etc.), causing the write to
hang waiting on a TCC prompt that never appears. launchd has no such gate.

Usage:
    python scheduler/install.py               # dry-run — print plan
    python scheduler/install.py --apply       # write plists + bootstrap them
    python scheduler/install.py --list        # show currently loaded jobs
    python scheduler/install.py --remove-all  # bootout + delete every managed plist

Supported cron syntax (extend as needed):
    "M H * * *"         — every day at H:M
    "M H * * D"         — every week on day D (0=Sun … 6=Sat) at H:M
    "M H * * D1-D2"     — weekday range, expanded into multiple calendar intervals
    "M H * * D1,D2"     — weekday list, expanded into multiple calendar intervals

Anything more exotic (minute ranges, step values, specific months/days) will
raise ValueError — add handling when it actually comes up.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
TRIGGER = ROOT / "cron_trigger.py"
LOG_DIR = ROOT / "logs"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.agentos"
UID = os.getuid()

# Prefer the venv Python — it has aiohttp, discord.py, claude-agent-sdk, etc.
# Otherwise launchd jobs crash at import time with ModuleNotFoundError.
_VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text()
    m = FM_RE.match(text)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def _parse_weekdays(field: str) -> list[int]:
    """Parse the DOW field of a cron expression into a list of launchd
    Weekday integers (0=Sun, 6=Sat)."""
    field = field.strip()
    if field == "*":
        return []  # no weekday constraint
    result: set[int] = set()
    for part in field.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            for n in range(int(a), int(b) + 1):
                result.add(n % 7)
        else:
            result.add(int(part) % 7)
    return sorted(result)


def cron_to_calendar_interval(expr: str) -> list[dict]:
    """Convert a 5-field cron expression to one or more launchd
    StartCalendarInterval dicts."""
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"expected 5-field cron expression, got {expr!r}")
    minute, hour, dom, month, dow = fields

    # Reject anything we don't handle yet — fail loud so we don't silently
    # install a wrong schedule.
    for name, val in [("minute", minute), ("hour", hour)]:
        if not val.isdigit():
            raise ValueError(f"unsupported {name} field {val!r} in {expr!r}")
    if dom != "*" or month != "*":
        raise ValueError(f"day-of-month / month not yet supported: {expr!r}")

    base = {"Minute": int(minute), "Hour": int(hour)}
    weekdays = _parse_weekdays(dow)
    if not weekdays:
        return [base]
    return [{**base, "Weekday": d} for d in weekdays]


def build_plan() -> list[dict]:
    """Walk the agents tree and return one dict per scheduled task."""
    if not AGENTS_DIR.exists():
        return []
    plan: list[dict] = []
    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        tasks = agent_dir / "tasks"
        if not tasks.is_dir():
            continue
        for task in sorted(tasks.glob("*.md")):
            fm = _read_frontmatter(task)
            schedule = fm.get("cron")
            if not schedule:
                continue
            label = f"{LABEL_PREFIX}.{agent_dir.name}-{task.stem}"
            plan.append({
                "agent": agent_dir.name,
                "task": task.stem,
                "cron": str(schedule),
                "label": label,
                "plist": LAUNCH_AGENTS_DIR / f"{label}.plist",
                "log": LOG_DIR / f"{agent_dir.name}-{task.stem}.log",
            })
    return plan


def build_plist(entry: dict) -> dict:
    """Return the dict that will be serialised to the plist file."""
    intervals = cron_to_calendar_interval(entry["cron"])
    log = str(entry["log"])
    return {
        "Label": entry["label"],
        "ProgramArguments": [
            PYTHON,
            str(TRIGGER),
            entry["agent"],
            entry["task"],
        ],
        "WorkingDirectory": str(ROOT),
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "RunAtLoad": False,
        "StartCalendarInterval": intervals[0] if len(intervals) == 1 else intervals,
        "EnvironmentVariables": {
            "PATH": os.environ.get(
                "PATH",
                "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            ),
        },
    }


def _launchctl(args: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=check,
    )


def bootout(label: str) -> tuple[int, str]:
    r = _launchctl(["bootout", f"gui/{UID}/{label}"])
    return r.returncode, (r.stderr or "").strip()


def bootstrap(plist_path: Path) -> tuple[int, str]:
    r = _launchctl(["bootstrap", f"gui/{UID}", str(plist_path)])
    return r.returncode, (r.stderr or "").strip()


def list_managed() -> list[str]:
    r = _launchctl(["list"])
    if r.returncode != 0:
        return []
    return [
        line.split("\t")[-1]
        for line in r.stdout.splitlines()
        if LABEL_PREFIX + "." in line
    ]


def _cron_active_tasks() -> set[tuple[str, str]]:
    """Return {(agent, task)} for every entry currently in the user crontab's
    managed AgentOS block. Used to avoid installing a launchd job whose
    cron twin is still firing from the old installer."""
    try:
        out = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return set()
    pairs: set[tuple[str, str]] = set()
    pattern = re.compile(r"cron_trigger\.py\s+(\S+)\s+(\S+)")
    in_block = False
    for line in out.splitlines():
        if "# --- agentos (managed) ---" in line:
            in_block = True
            continue
        if "# --- /agentos ---" in line:
            in_block = False
            continue
        if not in_block:
            continue
        m = pattern.search(line)
        if m:
            pairs.add((m.group(1), m.group(2)))
    return pairs


def remove_all() -> None:
    """Bootout every currently-loaded managed job and delete its plist."""
    for label in list_managed():
        rc, err = bootout(label)
        print(f"bootout {label}: rc={rc} {err or 'ok'}")
    for p in LAUNCH_AGENTS_DIR.glob(f"{LABEL_PREFIX}.*.plist"):
        p.unlink()
        print(f"deleted {p.name}")


def apply_plan(plan: list[dict], force: bool = False) -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Skip anything still firing from legacy user crontab (would double-trigger).
    cron_active = _cron_active_tasks()
    if cron_active and not force:
        skipped = [
            e for e in plan if (e["agent"], e["task"]) in cron_active
        ]
        if skipped:
            print(
                f"# {len(skipped)} task(s) still in legacy user crontab — "
                "skipping to avoid double-trigger. Clear the managed block "
                "from a real terminal with `crontab -e` (remove lines between "
                "`# --- agentos (managed) ---` markers) then re-run "
                "`scheduler/install.py --apply`, or pass --force to install "
                "anyway."
            )
            for entry in skipped:
                print(f"  - {entry['agent']}/{entry['task']}")
        plan = [e for e in plan if (e["agent"], e["task"]) not in cron_active]

    # Determine which labels we're keeping; anything managed but not in the plan
    # gets removed — except one-shot deferred labels (prefix `<...>-_deferred_`),
    # which are owned by scripts/defer.py and self-clean after firing. The
    # recurring-scheduler must not clobber a pending deferred run.
    desired = {e["label"] for e in plan}
    loaded = set(list_managed())
    # Plus plists on disk under our prefix, even if not currently loaded.
    on_disk = {p.stem for p in LAUNCH_AGENTS_DIR.glob(f"{LABEL_PREFIX}.*.plist")}
    stale = {
        label for label in (loaded | on_disk) - desired
        if "-_deferred_" not in label
    }

    for label in sorted(stale):
        rc, err = bootout(label)
        plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
        if plist.exists():
            plist.unlink()
        print(f"- removed {label} (bootout rc={rc}{', '+err if err else ''})")

    for entry in plan:
        plist_path = entry["plist"]
        data = build_plist(entry)
        plist_path.write_bytes(plistlib.dumps(data))

        # If already loaded, bootout first so bootstrap picks up changes.
        if entry["label"] in loaded:
            bootout(entry["label"])
        rc, err = bootstrap(plist_path)
        status = "ok" if rc == 0 else f"ERR rc={rc} {err}"
        print(f"+ {entry['label']}  ({entry['cron']})  {status}")


def describe_plan(plan: list[dict]) -> None:
    if not plan:
        print("# No scheduled tasks found.")
        return
    print(f"# {len(plan)} scheduled task(s):")
    for entry in plan:
        try:
            intervals = cron_to_calendar_interval(entry["cron"])
        except ValueError as e:
            intervals = [f"<unsupported: {e}>"]
        print(
            f"- {entry['label']}\n"
            f"    cron:    {entry['cron']}\n"
            f"    launchd: {intervals}\n"
            f"    log:     {entry['log']}"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Write plists and bootstrap them via launchctl")
    p.add_argument("--list", action="store_true",
                   help="Show currently-loaded managed jobs and exit")
    p.add_argument("--remove-all", action="store_true",
                   help="Bootout and delete every managed plist")
    p.add_argument("--force", action="store_true",
                   help="Install even if task is still in legacy user crontab")
    args = p.parse_args()

    if args.list:
        for label in list_managed():
            print(label)
        return
    if args.remove_all:
        remove_all()
        return

    plan = build_plan()
    if not args.apply:
        describe_plan(plan)
        return
    apply_plan(plan, force=args.force)


if __name__ == "__main__":
    main()
