#!/usr/bin/env python3
"""status.py — per-agent structured status rollup.

Gathers, for each agent:
  - last trajectory session ID + timestamp
  - open task count (ActiveTasks/*.md with status != done)
  - scheduled tasks next-fire times
  - size of memory/YYYY-MM-DD.md today

Emits a single markdown dashboard report to stdout. Pipe to a vault file
for a persistent daily snapshot:

    python scripts/status.py > "$VAULT_PATH/Agents/STATUS-$(date +%F).md"

Or emit JSON for the dashboard to consume:

    python scripts/status.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
LOGS_DIR = ROOT / "logs"


def last_trajectory(agent: str) -> dict | None:
    d = LOGS_DIR / "trajectories" / agent
    if not d.exists():
        return None
    latest = max(d.glob("*.jsonl"), default=None, key=lambda p: p.stat().st_mtime)
    if not latest:
        return None
    mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    return {
        "session_id": latest.stem,
        "mtime_utc": mtime.isoformat(timespec="seconds"),
        "size_bytes": latest.stat().st_size,
        "age_minutes": int((datetime.now(timezone.utc) - mtime).total_seconds() // 60),
    }


def open_tasks(agent_dir: Path) -> int:
    d = agent_dir / "ActiveTasks"
    if not d.exists():
        return 0
    count = 0
    for f in d.glob("*.md"):
        if f.name == "README.md":
            continue
        text = f.read_text()
        m = re.search(r"^status:\s*(\S+)", text, re.MULTILINE)
        if m and m.group(1) not in ("done", "archived"):
            count += 1
    return count


def scheduled_tasks(agent_dir: Path) -> list[dict]:
    d = agent_dir / "tasks"
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.md")):
        text = f.read_text()
        # Extract cron from YAML frontmatter
        m = re.search(r"^cron:\s*(.+)$", text, re.MULTILINE)
        kind_m = re.search(r"^kind:\s*(\S+)", text, re.MULTILINE)
        out.append({
            "name": f.stem,
            "cron": m.group(1).strip() if m else None,
            "kind": kind_m.group(1).strip() if kind_m else "post",
        })
    return out


def today_memory_size(agent_dir: Path) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = agent_dir / "memory" / f"{today}.md"
    return p.stat().st_size if p.exists() else 0


def build_report(agent_dir: Path) -> dict:
    cfg_path = agent_dir / "agent.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    return {
        "agent": agent_dir.name,
        "channel_id": cfg.get("channel_id"),
        "model": cfg.get("model"),
        "max_turns": cfg.get("max_turns"),
        "last_trajectory": last_trajectory(agent_dir.name),
        "open_tasks": open_tasks(agent_dir),
        "scheduled_tasks": scheduled_tasks(agent_dir),
        "today_memory_bytes": today_memory_size(agent_dir),
    }


def print_markdown(reports: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"# AgentOS Status — {now}")
    print()
    print(f"**{len(reports)} agents**")
    print()
    print("| agent | last turn | open tasks | scheduled | today mem |")
    print("|---|---|---|---|---|")
    for r in reports:
        last = r["last_trajectory"]
        last_str = (
            f"{last['age_minutes']}m ago"
            if last else "—"
        )
        print(
            f"| {r['agent']} | {last_str} | {r['open_tasks']} "
            f"| {len(r['scheduled_tasks'])} | {r['today_memory_bytes']}B |"
        )
    print()
    print("## Scheduled tasks (next-fire crontab)")
    print()
    for r in reports:
        if r["scheduled_tasks"]:
            print(f"### {r['agent']}")
            for t in r["scheduled_tasks"]:
                kind = f" _({t['kind']})_" if t["kind"] == "systemEvent" else ""
                print(f"- `{t['cron']}`  — {t['name']}{kind}")
            print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-agent status rollup")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not AGENTS_DIR.exists():
        print("error: no agents/ directory", file=sys.stderr)
        return 2

    agents = [
        d for d in sorted(AGENTS_DIR.iterdir())
        if d.is_dir() and not d.name.startswith((".", "_"))
        and (d / "agent.yaml").exists()
    ]
    reports = [build_report(d) for d in agents]

    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        print_markdown(reports)
    return 0


if __name__ == "__main__":
    sys.exit(main())
