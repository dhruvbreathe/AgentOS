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

import asyncio
import json
import logging
import time
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


FLUSH_PROMPT = """\
[pre-rotation memory flush] This session has crossed its context ceiling and \
will be ROTATED after this turn: your next turn starts a fresh session that \
sees only your system prompt plus a short tail of your daily memory file. \
Anything not written down now is gone.

Do this, quickly and silently (your final text here is discarded, nothing \
posts to Discord):
1. Append a compact working-state block to today's daily memory file \
(agents/<you>/memory/YYYY-MM-DD.md) using the Write/Edit tools: current \
task and its exact state, decisions taken this session that aren't yet in \
a durable file, next concrete step, and any operator asks still open.
2. IN-FLIGHT WORK IS THE PRIORITY: if you launched background agents, \
tasks, or long-running jobs this session, record for EACH one: its goal, \
its full relaunch prompt (verbatim or a file path to it), and how far it \
got. Background processes DIE with this session — the note is how your \
next self relaunches them.
3. Do NOT start new work, do NOT post to any webhook, do NOT route to \
other agents. Under ~10 tool calls.
4. Reply with exactly FLUSH_DONE when the memory file is written.
"""


async def flush(agent, session_id: str | None, cfg: dict | None) -> bool:
    """OpenClaw-style pre-rotation memory flush (Wave 3 P0-1, 2026-07-18).

    Runs ONE silent distillation turn in the about-to-rotate session so the
    agent writes its working state to the daily memory file — which is
    exactly what `check()` builds the fresh-session handoff from. Caller
    should re-run check() afterwards to rebuild the handoff note on top of
    the freshly-flushed memory.

    Fail-open by contract: any error/timeout returns False and rotation
    proceeds with whatever memory already existed. Returns True only when
    the flush turn completed.
    """
    try:
        cfg = cfg or {}
        fcfg = cfg.get("flush") or {}
        if not fcfg.get("enabled", False) or not session_id:
            return False
        # Deferred import — relay imports agent_loader; importing relay at
        # module load would cycle through bot.py's import graph.
        from relay import CollectingSink, run_agent

        timeout_s = float(fcfg.get("timeout_seconds", 240))
        t0 = time.time()
        await asyncio.wait_for(
            run_agent(
                agent,
                FLUSH_PROMPT,
                CollectingSink(),
                resume_session_id=session_id,
                model_override=fcfg.get("model"),
                effort_override=fcfg.get("effort"),
                max_turns_override=int(fcfg.get("max_turns", 8)),
            ),
            timeout=timeout_s,
        )
        log.info(
            "[%s] pre-rotation flush completed in %.0fs (session %s)",
            agent.name, time.time() - t0, session_id,
        )
        return True
    except Exception as e:  # noqa: BLE001 — flush must never block rotation
        log.warning("[%s] pre-rotation flush failed open: %s",
                    getattr(agent, "name", "?"), e)
        return False


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
        # Phase-2 compaction tuning: optional per-agent ceiling override
        # (config.yaml defaults.session_rotation.per_agent.<name>). Lets
        # heavy-payload agents (e.g. media) rotate earlier than the fleet.
        _per_agent = cfg.get("per_agent") or {}
        if agent_name in _per_agent:
            ceiling = int(_per_agent[agent_name])
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
