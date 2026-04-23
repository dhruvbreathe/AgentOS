"""Tail the most recent trajectory for an agent and pretty-print events as
they land. Useful for watching an agent's thinking/tool-use live while
it's responding in Discord.

Usage:
    python scripts/tail_trajectory.py <agent>            # latest session
    python scripts/tail_trajectory.py <agent> <session>  # a specific one
    python scripts/tail_trajectory.py <agent> -f         # follow mode
    python scripts/tail_trajectory.py <agent> --replay   # print the last prompt
    python scripts/tail_trajectory.py <agent> --rerun    # re-execute last prompt via claude CLI
    python scripts/tail_trajectory.py <agent> --rerun --prompt "new text"  # alt prompt
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


def _extract_last_prompt(path: Path) -> str | None:
    """Walk a trajectory JSONL and return the last user prompt content."""
    last = None
    with path.open() as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "prompt":
                last = event.get("content")
    return last


def _replay_via_claude(prompt: str, extra_args: list[str] | None = None) -> int:
    """Shell to the bundled claude CLI and run the prompt one-shot."""
    import subprocess
    cmd = ["claude", "-p", prompt, "--output-format", "text"]
    if extra_args:
        cmd.extend(extra_args)
    print(f"→ shelling: {' '.join(cmd[:3])} ... ({len(prompt)} chars)")
    return subprocess.call(cmd)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agent")
    p.add_argument("session", nargs="?", default=None)
    p.add_argument("--follow", "-f", action="store_true", help="Follow live")
    p.add_argument("--replay", action="store_true",
                   help="Print the last user prompt from this session (for copy/paste)")
    p.add_argument("--rerun", action="store_true",
                   help="Shell to `claude -p` and re-execute the last prompt")
    p.add_argument("--prompt", default=None,
                   help="Override prompt text when using --rerun")
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

    # --replay / --rerun: extract the last prompt from this session.
    if args.replay or args.rerun:
        prompt_text = args.prompt or _extract_last_prompt(path)
        if not prompt_text:
            print(f"No user prompt found in {path.name}")
            return 1
        if args.replay:
            print(f"== last prompt from {path.name} ==\n")
            print(prompt_text)
            return 0
        # --rerun
        return _replay_via_claude(prompt_text)

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
