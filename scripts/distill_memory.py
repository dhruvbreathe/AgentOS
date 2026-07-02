"""Nightly memory distillation — mine an agent's session trajectories for
durable lessons, facts, and corrections, and print a condensed digest.

This is the deterministic *prep* half of the auto-distilled-memory pilot
(Option B, proposal 2026-06-17). It does NOT call an LLM and does NOT edit
LEARNINGS.md. A `bootstrap: lite` cron task runs this, reads the digest, and
the agent's own Claude session decides what (if anything) is worth appending
to LEARNINGS.md / MEMORY.md, deduped against what's already there. That keeps
the reasoning on the subscription, no API key, and memory stays legible.

Why trajectories and not memory/YYYY-MM-DD.md: the breadcrumb notes only hold
what the agent already chose to journal. The trajectory holds what actually
happened, the user's corrections, the tool errors and how they resolved, the
conclusions reached. That's where un-journaled lessons hide.

Usage:
    python scripts/distill_memory.py main                 # last 1 day
    python scripts/distill_memory.py main backend qa      # several agents
    python scripts/distill_memory.py main --days 2
    python scripts/distill_memory.py --all --days 1       # every agent
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
TRAJ_ROOT = ROOT / "logs" / "trajectories"

# Cues that a user prompt is correcting or redirecting the agent. Not a filter,
# just a flag the distiller surfaces so the writeback session weighs them
# higher. Corrections are the single richest source of durable lessons.
_CORRECTION_CUES = (
    "no,", "no.", "nope", "wrong", "actually", "that's not", "thats not",
    "incorrect", "don't ", "do not ", "instead", "not the", "you missed",
    "redo", "not what", "isn't", "stop ", "never ", "again,", "you should",
    "why do you", "i said", "as i mentioned",
)

# Cron / system-event prompts we don't want to mine as "user corrections".
_SYSTEM_PROMPT_MARKERS = (
    "[scheduled task", "triggered]", "systemevent", "[discord #",
)


def _truncate(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


def _is_correction(content: str) -> bool:
    low = content.lower()
    return any(cue in low for cue in _CORRECTION_CUES)


def _looks_like_user(content: str) -> bool:
    """Distinguish a real human prompt from a routed/scheduled system prompt."""
    low = content.lower()
    # Routed Discord prompts start with a channel tag we can keep, but pure
    # scheduled-task fires are noise for lesson-mining.
    return not any(m in low for m in ("[scheduled task", "triggered]"))


def _parse_session(path: Path) -> dict | None:
    prompts: list[dict] = []
    conclusions: list[str] = []
    errors: list[str] = []
    last_tool = "?"
    first_ts = last_ts = ""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        ts = ev.get("ts", "")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if t == "prompt":
            c = (ev.get("content") or "").strip()
            if not c or not _looks_like_user(c):
                continue
            prompts.append({"text": c, "correction": _is_correction(c)})
        elif t == "text":
            c = (ev.get("content") or "").strip()
            if c:
                conclusions.append(c)  # keep order; last one is the real conclusion
        elif t == "tool_use":
            last_tool = ev.get("name", "?")
        elif t == "tool_result" and ev.get("is_error"):
            snippet = _truncate(ev.get("content", ""), 220)
            if snippet:
                errors.append(f"{last_tool}: {snippet}")

    if not prompts and not errors:
        return None  # system-only / empty session, nothing to mine

    return {
        "stem": path.stem,
        "short": path.stem[:8],
        "date": (first_ts or last_ts)[:10],
        "prompts": prompts,
        "conclusion": conclusions[-1] if conclusions else "",
        "errors": errors[:6],
    }


def _recent_sessions(agent: str, days: int) -> list[Path]:
    agent_dir = TRAJ_ROOT / agent
    if not agent_dir.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for p in agent_dir.glob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def _render(agent: str, sessions: list[dict], max_chars: int) -> str:
    if not sessions:
        return f"== distill: {agent} == (no minable sessions in window)\n"

    lines = [f"== distill: {agent} ({len(sessions)} sessions) =="]
    for s in sessions:
        lines.append(f"\n### session {s['short']} ({s['date']})")
        if s["prompts"]:
            lines.append("PROMPTS:")
            for pr in s["prompts"][:8]:
                tag = " [CORRECTION]" if pr["correction"] else ""
                lines.append(f"- {_truncate(pr['text'], 260)}{tag}")
        if s["conclusion"]:
            lines.append("CONCLUSION:")
            lines.append(f"- {_truncate(s['conclusion'], 400)}")
        if s["errors"]:
            lines.append("TOOL ERRORS:")
            for e in s["errors"]:
                lines.append(f"- {e}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "\n\u2026 (truncated)\n"
    return out + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agents", nargs="*", help="Agent names; empty with --all = every agent")
    p.add_argument("--all", action="store_true", help="Every agent with a trajectory dir")
    p.add_argument("--days", type=int, default=1, help="Look back N days (default 1)")
    p.add_argument("--max-chars", type=int, default=8000, help="Cap digest size per agent")
    args = p.parse_args()

    if args.all:
        targets = sorted(d.name for d in TRAJ_ROOT.iterdir() if d.is_dir())
    else:
        targets = args.agents
    if not targets:
        print("Specify agent names or --all", file=sys.stderr)
        return 2

    for agent in targets:
        sessions_raw = _recent_sessions(agent, args.days)
        parsed = [s for s in (_parse_session(p) for p in sessions_raw) if s]
        print(_render(agent, parsed, args.max_chars))
    return 0


if __name__ == "__main__":
    sys.exit(main())
