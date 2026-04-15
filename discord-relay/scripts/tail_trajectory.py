"""Tail the most recent trajectory for an agent and pretty-print events as
they land. Useful for watching an agent's thinking/tool-use live while
it's responding in Discord.

Usage:
    python scripts/tail_trajectory.py <agent>            # latest session
    python scripts/tail_trajectory.py <agent> <session>  # a specific one
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAJ_ROOT = ROOT / "logs" / "trajectories"


def _latest(agent_dir: Path) -> Path | None:
    files = sorted(agent_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _fmt(event: dict) -> str:
    ts = event.get("ts", "")
    short_ts = ts.split("T")[-1].rstrip("Z") if "T" in ts else ts
    t = event.get("type", "?")
    role = event.get("role", "?")
    if t == "prompt":
        return f"[{short_ts}] 👤 PROMPT\n  {event.get('content', '').strip()[:500]}"
    if t == "thinking":
        return f"[{short_ts}] 🧠 thinking\n  {event.get('content', '').strip()[:500]}"
    if t == "text":
        return f"[{short_ts}] 💬 text\n  {event.get('content', '').strip()[:500]}"
    if t == "tool_use":
        inp = event.get("input", {})
        brief = json.dumps(inp)[:200] if inp else ""
        return f"[{short_ts}] 🔧 tool_use: {event.get('name')}  {brief}"
    if t == "tool_result":
        err = "❌ " if event.get("is_error") else "✅ "
        return f"[{short_ts}] {err}tool_result  {event.get('content', '')[:200]}"
    if t == "result":
        return (
            f"[{short_ts}] 🏁 result  "
            f"stop={event.get('stop_reason')}  "
            f"session={event.get('session_id')}"
        )
    return f"[{short_ts}] {role}/{t}  {json.dumps(event)[:200]}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agent")
    p.add_argument("session", nargs="?", default=None)
    p.add_argument("--follow", "-f", action="store_true", help="Follow live")
    args = p.parse_args()

    agent_dir = TRAJ_ROOT / args.agent
    if not agent_dir.exists():
        print(f"No trajectories for agent '{args.agent}' at {agent_dir}")
        return 1

    if args.session:
        path = agent_dir / f"{args.session}.jsonl"
    else:
        path = _latest(agent_dir)
    if not path or not path.exists():
        print(f"No trajectory file found under {agent_dir}")
        return 1

    print(f"== {path.name} ==\n")

    with path.open() as f:
        # Print existing content first
        for line in f:
            try:
                print(_fmt(json.loads(line)) + "\n")
            except json.JSONDecodeError:
                continue
        if not args.follow:
            return 0
        # Then follow
        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                try:
                    print(_fmt(json.loads(line)) + "\n")
                except json.JSONDecodeError:
                    continue
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
