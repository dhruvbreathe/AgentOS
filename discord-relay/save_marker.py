"""Save-to-Obsidian via Discord 💾 reaction.

When the operator reacts 💾 on any message in an agent's channel, we
extract the most recent turn from the agent's trajectory JSONL and write
it as a Sessions note in the Obsidian vault. The bot then reacts ✅ on
the message so the operator sees the save landed (the "save icon" UX).

The full trajectory is JSONL-logged regardless; this just promotes one
slice into the vault, where it sits alongside operator-authored notes
and is reachable via `recall.py --vault` and Smart Connections.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("save-marker")

ROOT = Path(__file__).parent
TRAJ_ROOT = ROOT / "logs" / "trajectories"


def _latest_trajectory(agent_name: str, session_id: str | None) -> Path | None:
    agent_dir = TRAJ_ROOT / agent_name
    if not agent_dir.exists():
        return None
    if session_id:
        candidate = agent_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    files = sorted(agent_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _last_turn_window(events: list[dict], fallback: int = 12) -> list[dict]:
    """Slice from the most recent user prompt to the end. If there's no
    user prompt in the file (rare — pure cron output), return the tail."""
    for i in range(len(events) - 1, -1, -1):
        ev = events[i]
        if ev.get("role") == "user" and ev.get("type") == "prompt":
            return events[i:]
    return events[-fallback:]


def _format_window(events: list[dict]) -> str:
    out: list[str] = []
    for ev in events:
        ts = ev.get("ts", "")
        role = ev.get("role", "?")
        etype = ev.get("type", "?")
        if role == "user" and etype == "prompt":
            content = str(ev.get("content", "")).strip()
            out.append(f"### User · {ts}\n\n{content}")
        elif role == "assistant" and etype == "text":
            content = str(ev.get("content", "")).strip()
            if content:
                out.append(f"### Assistant · {ts}\n\n{content}")
        elif role == "assistant" and etype == "thinking":
            content = str(ev.get("content", "")).strip()
            if content:
                out.append(
                    f"<details><summary>thinking · {ts}</summary>\n\n"
                    f"{content}\n\n</details>"
                )
        elif role == "assistant" and etype == "tool_use":
            name = ev.get("name", "?")
            inp = ev.get("input", {})
            inp_brief = json.dumps(inp, ensure_ascii=False)
            if len(inp_brief) > 400:
                inp_brief = inp_brief[:397] + "..."
            out.append(f"- **tool** `{name}` · {ts}\n  ```\n  {inp_brief}\n  ```")
        elif role == "tool" and etype == "tool_result":
            content = str(ev.get("content", "")).strip()
            if content:
                snippet = content[:600].replace("\n", " ")
                if len(content) > 600:
                    snippet += " ..."
                out.append(f"  - result: {snippet}")
        elif role == "system" and etype == "result":
            usage = ev.get("usage", {}) or {}
            out.append(
                f"_(turn end — {usage.get('input_tokens', 0)} in · "
                f"{usage.get('output_tokens', 0)} out)_"
            )
    return "\n\n".join(out)


_DISCORD_PREFIX_RE = re.compile(r"^\[Discord #[^\]]+\][^\n]*\n*", re.MULTILINE)


def _slug(text: str, max_len: int = 40) -> str:
    text = _DISCORD_PREFIX_RE.sub("", text)
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    text = re.sub(r"\s+", "-", text)
    return text[:max_len].strip("-") or "saved-turn"


def save_turn(
    *,
    agent_name: str,
    channel_name: str,
    session_id: str | None,
    vault_path: Path,
) -> Path | None:
    """Find the most recent turn in this agent's trajectory and write it
    as a Sessions note in the vault. Returns the saved path or None."""
    traj = _latest_trajectory(agent_name, session_id)
    if traj is None:
        log.warning("save: no trajectory found for %s", agent_name)
        return None
    events = _read_jsonl(traj)
    if not events:
        log.warning("save: trajectory %s is empty", traj)
        return None
    window = _last_turn_window(events)

    first_prompt = next(
        (ev for ev in window
         if ev.get("role") == "user" and ev.get("type") == "prompt"),
        None,
    )
    seed_text = (first_prompt or {}).get("content", "").splitlines()
    slug = _slug(seed_text[0] if seed_text else "saved-turn")

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M")

    sessions_dir = vault_path / "Sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    out_path = sessions_dir / f"{date_str}-{agent_name}-{time_str}-{slug}.md"

    try:
        traj_rel = traj.relative_to(ROOT)
    except ValueError:
        traj_rel = traj

    body = _format_window(window)
    front = (
        "---\n"
        f"date: {date_str}\n"
        f"agent: {agent_name}\n"
        f"channel: {channel_name}\n"
        f"session_id: {session_id or 'unknown'}\n"
        f"trajectory: {traj_rel}\n"
        f"saved_at: {now.isoformat()}\n"
        "tags: [session-log, saved-turn, discord-relay]\n"
        "---\n\n"
    )
    title = f"# Saved turn — {agent_name} ({date_str} {time_str}Z)\n\n"
    out_path.write_text(front + title + body + "\n")
    log.info("saved turn for %s -> %s", agent_name, out_path)
    return out_path
