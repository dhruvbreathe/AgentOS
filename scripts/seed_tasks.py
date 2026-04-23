#!/usr/bin/env python3
"""seed_tasks.py — one-shot import of Agents/TASKS.md + Company/STRATEGY.md
into the new SQLite store.

Run once after tasks.py schema lands. Idempotent via title-match: re-running
skips tasks/goals whose title already exists.

Usage:
    python scripts/seed_tasks.py [--vault /path/to/Vayu]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tasks  # noqa: E402

DEFAULT_VAULT = os.environ.get("VAULT_PATH")
if not DEFAULT_VAULT:
    raise RuntimeError("VAULT_PATH is not set — copy .env.example to .env and fill it in.")


PRIORITY_MAP = {
    "🔴": "urgent", "urgent": "urgent",
    "🟡": "normal", "this week": "normal",
    "🟢": "low", "backlog": "low",
}


def _ensure_goal(title: str, description: str | None = None,
                 kind: str = "objective", parent_id: int | None = None) -> int:
    existing = next((g for g in tasks.list_goals() if g["title"] == title), None)
    if existing:
        return existing["id"]
    return tasks.create_goal(title, description, parent_id=parent_id, kind=kind)


def seed_mission(strategy_path: Path) -> dict[str, int]:
    """Create a mission + a few key objectives pulled from STRATEGY.md headers."""
    if not strategy_path.exists():
        print(f"  skip strategy: {strategy_path} not found")
        return {}
    text = strategy_path.read_text()
    # Extract mission sentence under ## Mission
    m = re.search(r"##\s*Mission\s*\n+\*\*(.+?)\*\*", text, re.DOTALL)
    mission_text = m.group(1).strip() if m else "Make holistic wellbeing accessible."
    v = re.search(r"##\s*Vision\s*\n+\*\*(.+?)\*\*", text, re.DOTALL)
    vision_text = v.group(1).strip() if v else None

    mission_id = _ensure_goal(mission_text, kind="mission")
    vision_id = _ensure_goal(vision_text, kind="vision", parent_id=mission_id) if vision_text else None

    # A few default objectives that every task can roll up to.
    roots = {
        "mission": mission_id,
        "Grow paid subscribers": _ensure_goal(
            "Grow paid subscribers", kind="objective", parent_id=mission_id,
            description="B2C subscription growth — main revenue line"),
        "Raise $500K pre-seed": _ensure_goal(
            "Raise $500K pre-seed", kind="objective", parent_id=mission_id,
            description="Pre-seed round close"),
        "Ship product reliably": _ensure_goal(
            "Ship product reliably", kind="objective", parent_id=mission_id,
            description="iOS + Android stability, release cadence"),
        "Build agent OS": _ensure_goal(
            "Build agent OS", kind="objective", parent_id=mission_id,
            description="Prana Agent OS — the mesh behind the company"),
    }
    if vision_id:
        roots["vision"] = vision_id
    return roots


TASK_BLOCK = re.compile(
    r"###\s*\[?(?P<date>\d{4}-\d{2}-\d{2})?\]?\s*(?P<title>.+?)\n"
    r"(?P<body>(?:(?!###|^##\s).+\n?)+)",
    re.MULTILINE,
)

PRIO_SECTION = re.compile(r"##\s*(🔴\s*Urgent|🟡[^\n]*|🟢[^\n]*|In progress|Blocked|Done)", re.IGNORECASE)


def seed_tasks_from_md(tasks_path: Path, goal_roots: dict[str, int]) -> int:
    if not tasks_path.exists():
        print(f"  skip tasks: {tasks_path} not found")
        return 0
    text = tasks_path.read_text()

    # Split by priority headers and label each chunk with its priority.
    sections: list[tuple[str, str]] = []
    pos = 0
    cur_prio = "normal"
    for m in PRIO_SECTION.finditer(text):
        if pos < m.start():
            sections.append((cur_prio, text[pos:m.start()]))
        label = m.group(1).lower()
        if "urgent" in label or "🔴" in label:
            cur_prio = "urgent"
        elif "🟡" in label or "week" in label or "in progress" in label:
            cur_prio = "normal"
        elif "done" in label:
            cur_prio = "done"
        else:
            cur_prio = "low"
        pos = m.end()
    sections.append((cur_prio, text[pos:]))

    existing_titles = {t["title"] for t in tasks.list_tasks(include_done=True)}
    created = 0
    default_goal = goal_roots.get("Ship product reliably")

    for prio, chunk in sections:
        for m in TASK_BLOCK.finditer(chunk):
            title = m.group("title").strip().rstrip("*")
            if not title or title in existing_titles:
                continue
            body = m.group("body").strip()
            # Heuristic: pull "Assigned to:" line into owner
            owner = None
            om = re.search(r"\*\*Assigned to:\*\*\s*([^\n\(]+?)(?:\(|\n|$)", body)
            if om:
                first = om.group(1).strip().split(",")[0].split("+")[0].strip()
                # Strip parenthetical names like "ios-developer (Sora)"
                owner = re.sub(r"\s*\(.*?\)", "", first).strip().lower() or None
                # Normalise to our agent names
                owner = owner.replace(" ", "-")
            # Status heuristic from body markers
            status = "open"
            if prio == "done" or "✅" in body.lower():
                status = "done"
            elif "blocked" in body.lower() or "🚧" in body:
                status = "blocked"
            elif "waiting" in body.lower():
                status = "waiting"
            elif "in progress" in body.lower() or "🚧" in body:
                status = "in_progress"
            real_prio = "normal" if prio == "done" else prio
            if real_prio not in ("urgent", "normal", "low"):
                real_prio = "normal"
            try:
                tasks.create_task(
                    title=title,
                    description=body[:4000],
                    status=status,
                    priority=real_prio,
                    owner=owner,
                    goal_id=default_goal,
                    actor="seed",
                )
                created += 1
            except Exception as e:
                print(f"  warn: {title!r}: {e}")
    return created


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    args = ap.parse_args()
    vault = Path(args.vault)
    print(f"vault: {vault}")
    goals = seed_mission(vault / "Company" / "STRATEGY.md")
    print(f"  goals: {len(goals)} ({', '.join(goals)})")
    n = seed_tasks_from_md(vault / "Agents" / "TASKS.md", goals)
    print(f"  tasks created: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
