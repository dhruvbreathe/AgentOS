"""Durable agent-to-agent task board (2026-09-29, Hermes kanban_db design).

`send_to_agent` is conversational: a message lands in the target's channel
and nothing brings the result back (the chain guard even blocks the reply
route A->B->A). The board is for WORK:

  delegate_task  -> a card (todo until its parents are done, then ready)
  dispatcher     -> claims ready cards by compare-and-swap, so a card never
                    runs twice; runs the assignee in a fresh per-task session
  complete_task  -> the worker records done / blocked + a summary
  wake           -> once every card the requester created in one turn has
                    settled, the requester gets one turn with all results
                    (OpenClaw sessions_yield: wake once, not per card)

Storage: SQLite in WAL mode at logs/kanban.db; every write is a BEGIN
IMMEDIATE transaction. Cards are mirrored best-effort into the Supabase task
ledger (task_ledger.py) so Tempo's sweep still sees them.

Statuses: todo -> ready -> running -> done | failed | blocked
"""
from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "logs" / "kanban.db"
DISPATCH_LOCK = ROOT / "logs" / "kanban.dispatch.lock"

TERMINAL = ("done", "failed", "blocked")
MAX_DEPTH = 3          # delegation depth ceiling (cards spawned by cards)
MAX_ATTEMPTS = 2       # a card lost twice (crash/restart) is marked failed
CLAIM_TTL_S = 45 * 60  # a running card older than this is presumed lost

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  done_when TEXT NOT NULL DEFAULT '',
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  status TEXT NOT NULL,
  parents TEXT NOT NULL DEFAULT '[]',
  chain TEXT NOT NULL DEFAULT '',
  batch TEXT,
  depth INTEGER NOT NULL DEFAULT 0,
  claim_lock TEXT,
  claimed_at REAL,
  attempts INTEGER NOT NULL DEFAULT 0,
  summary TEXT,
  ledger_id TEXT,
  woke INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS tasks_batch ON tasks(batch);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  data TEXT,
  ts REAL NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


@contextlib.contextmanager
def _txn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _event(conn: sqlite3.Connection, task_id: str, kind: str, data: Any = None) -> None:
    conn.execute(
        "INSERT INTO task_events(task_id, kind, data, ts) VALUES (?,?,?,?)",
        (task_id, kind, json.dumps(data) if data is not None else None, time.time()),
    )


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["parents"] = json.loads(d.get("parents") or "[]")
    return d


# ---- cards ---------------------------------------------------------------

def create(
    from_agent: str,
    to_agent: str,
    title: str,
    body: str = "",
    done_when: str = "",
    parents: list[str] | None = None,
    chain: str = "",
    batch: str | None = None,
    depth: int = 0,
) -> dict:
    parents = [p.strip() for p in (parents or []) if p.strip()]
    now = time.time()
    task_id = "T-" + secrets.token_hex(3)
    with _txn() as conn:
        if parents:
            rows = conn.execute(
                f"SELECT id, status FROM tasks WHERE id IN ({','.join('?' * len(parents))})",
                parents,
            ).fetchall()
            found = {r["id"]: r["status"] for r in rows}
            missing = [p for p in parents if p not in found]
            if missing:
                raise ValueError(f"unknown parent task(s): {', '.join(missing)}")
            status = "ready" if all(s == "done" for s in found.values()) else "todo"
        else:
            status = "ready"
        conn.execute(
            "INSERT INTO tasks(id,title,body,done_when,from_agent,to_agent,status,"
            "parents,chain,batch,depth,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, title[:200], body[:8000], done_when[:1000], from_agent,
             to_agent, status, json.dumps(parents), chain, batch, depth, now, now),
        )
        _event(conn, task_id, "created", {"from": from_agent, "to": to_agent})
        return _row(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def get(task_id: str) -> dict | None:
    conn = _connect()
    try:
        return _row(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    finally:
        conn.close()


def set_ledger_id(task_id: str, ledger_id: str) -> None:
    with _txn() as conn:
        conn.execute("UPDATE tasks SET ledger_id=? WHERE id=?", (ledger_id, task_id))


def complete(task_id: str, status: str, summary: str, by: str | None = None) -> dict | None:
    """Close a running card. Returns the card, or None if it wasn't running
    (already closed, or `by` isn't its assignee)."""
    if status not in TERMINAL:
        raise ValueError(f"status must be one of {TERMINAL}")
    with _txn() as conn:
        q = "UPDATE tasks SET status=?, summary=?, updated_at=? WHERE id=? AND status='running'"
        args: list[Any] = [status, summary[:4000], time.time(), task_id]
        if by:
            q += " AND to_agent=?"
            args.append(by)
        if conn.execute(q, args).rowcount != 1:
            return None
        _event(conn, task_id, status, {"summary": summary[:500]})
        return _row(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


# ---- dispatcher steps -----------------------------------------------------

def promote() -> None:
    """todo -> ready once every parent is done; blocked if a parent failed."""
    with _txn() as conn:
        for r in conn.execute("SELECT id, parents FROM tasks WHERE status='todo'").fetchall():
            parents = json.loads(r["parents"] or "[]")
            if not parents:
                states = []
            else:
                states = [x["status"] for x in conn.execute(
                    f"SELECT status FROM tasks WHERE id IN ({','.join('?' * len(parents))})",
                    parents,
                ).fetchall()]
            if all(s == "done" for s in states):
                conn.execute("UPDATE tasks SET status='ready', updated_at=? WHERE id=?",
                             (time.time(), r["id"]))
                _event(conn, r["id"], "ready")
            elif any(s in ("failed", "blocked") for s in states):
                conn.execute(
                    "UPDATE tasks SET status='blocked', summary=?, updated_at=? WHERE id=?",
                    ("a parent task failed or is blocked", time.time(), r["id"]),
                )
                _event(conn, r["id"], "blocked", {"reason": "parent"})


def claim_next(busy_agents: set[str], limit: int = 4) -> list[dict]:
    """Claim up to `limit` ready cards, at most one per agent and none for
    agents already running a card. CAS: the UPDATE only succeeds while the
    card is still ready and unclaimed, so two dispatchers can't both win."""
    claimed: list[dict] = []
    conn = _connect()
    try:
        candidates = conn.execute(
            "SELECT id, to_agent FROM tasks WHERE status='ready' ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    taken = set(busy_agents)
    for c in candidates:
        if len(claimed) >= limit or c["to_agent"] in taken:
            continue
        lock = secrets.token_hex(4)
        with _txn() as conn:
            ok = conn.execute(
                "UPDATE tasks SET status='running', claim_lock=?, claimed_at=?, "
                "attempts=attempts+1, updated_at=? "
                "WHERE id=? AND status='ready' AND claim_lock IS NULL",
                (lock, time.time(), time.time(), c["id"]),
            ).rowcount == 1
            if ok:
                _event(conn, c["id"], "claimed", {"lock": lock})
                claimed.append(_row(conn.execute(
                    "SELECT * FROM tasks WHERE id=?", (c["id"],)).fetchone()))
                taken.add(c["to_agent"])
    return claimed


def reclaim(all_running: bool = False) -> list[str]:
    """Running cards presumed lost (claim older than the TTL, or every running
    card at dispatcher startup, since a restart kills in-flight workers) go
    back to ready, or to failed after MAX_ATTEMPTS."""
    out = []
    cutoff = time.time() - CLAIM_TTL_S
    with _txn() as conn:
        rows = conn.execute(
            "SELECT id, attempts, claimed_at FROM tasks WHERE status='running'"
        ).fetchall()
        for r in rows:
            if not all_running and (r["claimed_at"] or 0) > cutoff:
                continue
            if r["attempts"] >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE tasks SET status='failed', summary=?, claim_lock=NULL, "
                    "updated_at=? WHERE id=?",
                    ("worker lost twice (crash/restart/timeout)", time.time(), r["id"]),
                )
                _event(conn, r["id"], "failed", {"reason": "lost"})
            else:
                conn.execute(
                    "UPDATE tasks SET status='ready', claim_lock=NULL, updated_at=? WHERE id=?",
                    (time.time(), r["id"]),
                )
                _event(conn, r["id"], "reclaimed")
            out.append(r["id"])
    return out


def requeue(task_id: str) -> bool:
    """running -> ready without counting a lost attempt (e.g. the worker hit
    the quota wall: the card should simply run again after the reset)."""
    with _txn() as conn:
        ok = conn.execute(
            "UPDATE tasks SET status='ready', claim_lock=NULL, "
            "attempts=MAX(attempts-1, 0), updated_at=? WHERE id=? AND status='running'",
            (time.time(), task_id),
        ).rowcount == 1
        if ok:
            _event(conn, task_id, "requeued", {"reason": "quota"})
        return ok


def take_settled_batches() -> list[dict]:
    """Batches whose cards are all terminal and whose requester hasn't been
    woken yet. Marks them woken in the same transaction (no double wake)."""
    out = []
    with _txn() as conn:
        batches = conn.execute(
            "SELECT DISTINCT batch FROM tasks WHERE woke=0 AND batch IS NOT NULL "
            "AND status IN ('done','failed','blocked')"
        ).fetchall()
        for b in batches:
            rows = conn.execute("SELECT * FROM tasks WHERE batch=?", (b["batch"],)).fetchall()
            if any(r["status"] not in TERMINAL for r in rows):
                continue
            conn.execute("UPDATE tasks SET woke=1 WHERE batch=?", (b["batch"],))
            out.append({
                "batch": b["batch"],
                "requester": rows[0]["from_agent"],
                "tasks": [_row(r) for r in rows],
            })
    return out


def board(limit: int = 30) -> list[dict]:
    conn = _connect()
    try:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()


# ---- prompts --------------------------------------------------------------

def worker_prompt(card: dict) -> str:
    parents = ""
    for pid in card.get("parents") or []:
        p = get(pid)
        if p:
            parents += f"\n- {pid} (@{p['to_agent']}, {p['status']}): {(p.get('summary') or '')[:800]}"
    return (
        f"[task board] You are working task {card['id']}, delegated by "
        f"@{card['from_agent']}. This runs in a fresh session, not your channel "
        f"conversation; nobody is watching live.\n\n"
        f"## {card['title']}\n{card['body']}\n"
        + (f"\n**Done when:** {card['done_when']}\n" if card.get("done_when") else "")
        + (f"\n**Results from prerequisite tasks:**{parents}\n" if parents else "")
        + f"\nWhen finished, call `complete_task` with task_id `{card['id']}`, "
        f"status `done` (or `blocked` if you cannot finish, saying exactly what is "
        f"missing), and a summary the requester can act on without re-doing your "
        f"work: what you did, results, file paths, anything they must decide."
    )


def wake_prompt(batch: dict) -> str:
    lines = []
    for t in batch["tasks"]:
        lines.append(
            f"- **{t['id']}** @{t['to_agent']} ({t['status']}): {t['title']}\n"
            f"  {(t.get('summary') or '(no summary)')[:1500]}"
        )
    return (
        "[task board] Results are back for work you delegated:\n\n"
        + "\n".join(lines)
        + "\n\nContinue whatever this unblocks. If the operator should know, say "
        "so here in a few lines; if a task is blocked, decide the next step."
    )
