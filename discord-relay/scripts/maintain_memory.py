"""Weekly memory maintenance for an agent (or all agents).

Archives old `memory/YYYY-MM-DD.md` files into `memory/archive/YYYY-MM/`
and prints a distilled summary the agent can use on its next session to
update LEARNINGS.md. This script does not edit LEARNINGS.md — the agent
does that, because only the agent knows what's worth promoting.

Usage:
    python scripts/maintain_memory.py                # all agents
    python scripts/maintain_memory.py main marketing # specific ones
    python scripts/maintain_memory.py --archive-days 14  # keep last 14
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"


def _maintain(agent_dir: Path, archive_days: int) -> dict[str, int]:
    mem = agent_dir / "memory"
    if not mem.exists():
        return {"archived": 0, "kept": 0, "total": 0}

    cutoff = datetime.now().date() - timedelta(days=archive_days)
    archived = kept = 0
    for path in mem.glob("20*.md"):
        # Parse YYYY-MM-DD from filename; skip anything not matching.
        try:
            date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if date < cutoff:
            dest = mem / "archive" / f"{date.strftime('%Y-%m')}"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), dest / path.name)
            archived += 1
        else:
            kept += 1
    return {"archived": archived, "kept": kept, "total": archived + kept}


def _recent_summary(agent_dir: Path, days: int = 7) -> str:
    mem = agent_dir / "memory"
    if not mem.exists():
        return "(no memory/ directory)"
    cutoff = datetime.now().date() - timedelta(days=days)
    lines = []
    for path in sorted(mem.glob("20*.md")):
        try:
            date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if date < cutoff:
            continue
        content = path.read_text().strip()
        if content:
            lines.append(f"### {path.stem}\n{content}")
    return "\n\n".join(lines) or "(no recent raw notes)"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agents", nargs="*", help="Agent names; empty = all")
    p.add_argument("--archive-days", type=int, default=14,
                   help="Move memory files older than N days into archive/")
    p.add_argument("--summary-days", type=int, default=7,
                   help="Window size for printed summary")
    args = p.parse_args()

    if args.agents:
        targets = [AGENTS / n for n in args.agents if (AGENTS / n).is_dir()]
    else:
        targets = [
            d for d in AGENTS.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_"))
            and (d / "agent.yaml").exists()
        ]

    for agent_dir in sorted(targets, key=lambda d: d.name):
        stats = _maintain(agent_dir, args.archive_days)
        print(f"== {agent_dir.name} ==")
        print(f"  archived {stats['archived']}, kept {stats['kept']} (cutoff {args.archive_days}d)")
        if args.summary_days:
            summary = _recent_summary(agent_dir, args.summary_days)
            # Tight preview — operator or the agent reads the full file on demand
            head = summary[:800]
            print(f"  recent {args.summary_days}d summary (first 800 chars):")
            for line in head.splitlines():
                print(f"    {line}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
