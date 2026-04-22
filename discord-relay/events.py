"""events.py — tiny in-memory pub/sub for live agent activity.

One process-global Bus. Publishers (web_chat._append_history,
agent_tools.send_to_agent) fire events; subscribers (the SSE endpoint
at /api/chat/events) drain their own queue.

Every event also gets appended to logs/events.jsonl so a restart-surviving
"unread since last seen" count works without every subscriber online.

Event shape:
  {
    "ts": ISO-8601 UTC,
    "type": "message" | "routed" | "reply" | ...,
    "agent": "<agent name>" (receiver),
    "thread": "<thread id>" (default "default"),
    "from": "operator" | "<agent name>",
    "preview": "<first 140 chars of content>",
    "hop": optional int for routed,
  }
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EVENTS_LOG = ROOT / "logs" / "events.jsonl"
EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)


class Bus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, ev: dict[str, Any]) -> None:
        """Sync publisher — safe to call from anywhere. Appends to the
        durable log immediately; fans out to live subscribers without
        blocking on slow consumers (drops instead of backing up)."""
        ev = {**ev}
        ev.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        # Durable — so "unread since last seen" works across restarts.
        try:
            with EVENTS_LOG.open("a") as f:
                f.write(json.dumps(ev) + "\n")
        except Exception:
            pass
        # Fan out to live subscribers. Non-blocking: if a queue is full,
        # drop rather than stall the publisher.
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass


bus = Bus()


def publish(ev: dict[str, Any]) -> None:
    bus.publish(ev)


# ---- cross-process fan-in (tail events.jsonl) ------------------------------
#
# The bus is in-memory per-process. But publishers can come from TWO
# processes: the dashboard (web chat turns) AND bot.py (Discord turns,
# where send_to_agent usually fires). To make browsers see everyone's
# events, one process (the dashboard) tails the durable log and republishes
# any line it didn't originate into its in-memory bus.
#
# We avoid re-publishing our own writes by keeping a running byte offset
# and deduping via object id() in a tiny ring buffer is unnecessary —
# the tailer just seeks to current EOF at startup so it only picks up
# lines written AFTER startup by *other* processes (or by us between
# restarts).

async def tail_events_forever(poll_interval: float = 0.5) -> None:
    """Long-running task: read new lines appended to EVENTS_LOG and
    republish them into the in-memory bus so SSE subscribers see events
    originated from other processes. Called once at dashboard startup."""
    EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_LOG.touch(exist_ok=True)
    # Start from current EOF so we don't re-broadcast historical events.
    offset = EVENTS_LOG.stat().st_size
    # Track our own process's writes so we don't fan them back out twice.
    seen_own: set[str] = set()
    while True:
        try:
            size = EVENTS_LOG.stat().st_size
            if size < offset:
                # Log was truncated/rotated; restart from the new EOF.
                offset = size
            elif size > offset:
                with EVENTS_LOG.open() as f:
                    f.seek(offset)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        # Dedupe by (ts, agent, preview) — our own publish()
                        # already fanned this out before we saw it on disk.
                        key = f"{ev.get('ts')}|{ev.get('agent')}|{ev.get('preview','')[:50]}"
                        if key in seen_own:
                            seen_own.discard(key)
                            continue
                        # Fan it out; and remember it so if this same event
                        # also gets picked up by our next poll we ignore it.
                        seen_own.add(key)
                        for q in list(bus._subs):
                            try:
                                q.put_nowait(ev)
                            except asyncio.QueueFull:
                                pass
                    offset = f.tell()
                # Cap the dedupe set so it doesn't grow unbounded.
                if len(seen_own) > 1000:
                    seen_own.clear()
        except Exception:
            pass
        await asyncio.sleep(poll_interval)


# ---- unread counts (simple: count events newer than a "since" ts) ----------

def events_since(since_iso: str | None, limit: int = 500) -> list[dict]:
    """Read the durable log tail. `since_iso` is an ISO-8601 timestamp;
    any event with ts > since_iso is returned (up to `limit`)."""
    if not EVENTS_LOG.exists():
        return []
    out: list[dict] = []
    with EVENTS_LOG.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_iso and ev.get("ts", "") <= since_iso:
                continue
            out.append(ev)
    return out[-limit:]


def unread_by_agent(since_iso: str | None) -> dict[str, int]:
    """Count events per receiving agent since `since_iso`. An event counts
    as unread only if its `from` is neither the operator (who triggered it)
    nor the agent itself (replies aren't news in their own channel). That
    leaves: another agent routing in, an origin="discord" mirror, etc."""
    counts: dict[str, int] = {}
    for ev in events_since(since_iso, limit=5000):
        a = ev.get("agent")
        sender = ev.get("from")
        if not a or not sender:
            continue
        if sender == "operator" or sender == a:
            continue
        counts[a] = counts.get(a, 0) + 1
    return counts
