"""Phase-0 session rotation (2026-07-05 orchestration master plan).

Problem: channel sessions never rotate. 20% of turns ran past 300k context
tokens (max 618k) — slower, costlier, measurably worse reasoning, and the
session-store mirror stays a permanently-refused tail (attached mid-life,
so chain_intact correctly rejects it at resume).

Fix: at turn start, if the channel's current session last reported context
above a ceiling, start a FRESH session seeded with a compact memory
handoff instead of resuming. Durable continuity lives where it always
should have: LEARNINGS/MEMORY (in the system prompt) + daily memory files
(tail injected into the first prompt of the fresh session). Fresh sessions
are also mirror-complete from birth, so store-backed resume finally engages.

Design constraints:
- FAIL-OPEN. Any error in the check → no rotation, resume exactly as before.
  Rotation is an optimization; breaking resume is never acceptable.
- Zero new state. The context signal is read from the post-turn telemetry
  relay.py already writes (logs/context-usage.jsonl); the handoff is built
  from the agent's existing memory/YYYY-MM-DD.md breadcrumbs.
- Discord relay only (bot.py). Cron sessions are always fresh; web_chat
  sessions are young — revisit if their telemetry starts showing bloat.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger("session-rotation")

ROOT = Path(__file__).parent
CONTEXT_USAGE_LOG = ROOT / "logs" / "context-usage.jsonl"
AGENTS_DIR = ROOT / "agents"

DEFAULT_MAX_CONTEXT_TOKENS = 150_000
# How much of the telemetry tail to scan for the session's last reading.
_TAIL_BYTES = 256 * 1024
# Cap the handoff so the fresh session starts lean — that's the point.
_HANDOFF_CHAR_BUDGET = 3_000


def _last_context_tokens(session_id: str) -> int | None:
    """Most recent total_tokens telemetry for `session_id`, or None.

    Reads only the tail of the JSONL (newest lines win). Telemetry is
    written post-turn by relay._capture_context_usage.
    """
    if not CONTEXT_USAGE_LOG.exists():
        return None
    with CONTEXT_USAGE_LOG.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - _TAIL_BYTES))
        tail = f.read().decode("utf-8", errors="replace")
    best: int | None = None
    for line in tail.splitlines():
        line = line.strip()
        if not line or f'"{session_id}"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("session_id") == session_id and d.get("total_tokens"):
            best = int(d["total_tokens"])  # keep last (chronological file)
    return best


def _memory_tail(agent_name: str) -> str:
    """Tail of today's (and, if thin, yesterday's) daily memory breadcrumbs."""
    chunks: list[str] = []
    today = date.today()
    for d in (today, today - timedelta(days=1)):
        p = AGENTS_DIR / agent_name / "memory" / f"{d.isoformat()}.md"
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        chunks.append(f"--- memory/{d.isoformat()}.md (tail) ---")
        chunks.append("\n".join(lines[-40:]))
        if sum(len(c) for c in chunks) > _HANDOFF_CHAR_BUDGET:
            break
    text = "\n".join(chunks)
    return text[-_HANDOFF_CHAR_BUDGET:] if text else "(no recent memory notes)"


def check(agent_name: str, session_id: str | None, cfg: dict | None) -> str | None:
    """Return a handoff note when the session should rotate, else None.

    Caller contract (bot.py): a non-None return means START A FRESH SESSION
    (drop the resume id) and prepend the note to this turn's prompt.
    """
    try:
        cfg = cfg or {}
        if not session_id or not cfg.get("enabled", True):
            return None
        ceiling = int(cfg.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS))
        tokens = _last_context_tokens(session_id)
        if tokens is None or tokens < ceiling:
            return None
        note = (
            f"[Automatic session rotation] Your previous conversation session "
            f"reached ~{tokens // 1000}k context tokens and was rotated for "
            f"performance; this is a fresh session in the same Discord "
            f"channel and the SAME ongoing conversation with the operator — "
            f"do not re-introduce yourself. Durable knowledge is already in "
            f"your system prompt (LEARNINGS/MEMORY). Recent working state "
            f"from your daily memory:\n\n{_memory_tail(agent_name)}\n\n"
            f"If the operator's message below references something not "
            f"covered here, check Sessions/ in the vault or your trajectory "
            f"logs before asking them to repeat it.\n"
            f"--- operator message follows ---"
        )
        log.info(
            "[%s] rotating session %s at %sk tokens (ceiling %sk)",
            agent_name, session_id, tokens // 1000, ceiling // 1000,
        )
        return note
    except Exception as e:  # noqa: BLE001 — rotation must never break a turn
        log.warning("[%s] rotation check failed open: %s", agent_name, e)
        return None
