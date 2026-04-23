"""tasks.py — structured task / goal / spend store for the AgentOS.

Replaces the markdown-first `Agents/TASKS.md` control plane with a real
ticket model. Goals hang off a mission root; tasks hang off goals; task
events are the audit trail. Agent spend is tracked per-day from trajectory
token counts so the dashboard can show burn vs. monthly budget.

This module is intentionally self-contained: single SQLite file, stdlib
only. FastAPI routes and the seed script import it.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "logs" / "tasks.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_LOCK = threading.Lock()

STATUSES = ("backlog", "open", "in_progress", "waiting", "blocked", "done")
PRIORITIES = ("urgent", "normal", "low")
GOAL_KINDS = ("mission", "vision", "objective", "initiative")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn():
    """Serialised connection — SQLite is fine for our scale and we avoid
    write-write contention across dashboard + bot turns."""
    with _LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT,
  parent_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
  kind TEXT NOT NULL DEFAULT 'objective',
  created TEXT NOT NULL,
  updated TEXT NOT NULL,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'backlog',
  priority TEXT NOT NULL DEFAULT 'normal',
  owner TEXT,
  goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
  parent_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
  discord_thread_id TEXT,
  session_ref TEXT,
  created TEXT NOT NULL,
  updated TEXT NOT NULL,
  closed_at TEXT,
  cost_tokens INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  actor TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT,
  created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_spend (
  agent TEXT NOT NULL,
  date TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_create_tokens INTEGER NOT NULL DEFAULT 0,
  session_count INTEGER NOT NULL DEFAULT 0,
  last_session TEXT,
  updated TEXT NOT NULL,
  PRIMARY KEY (agent, date)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner);
CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_spend_date ON agent_spend(date);
"""


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)


# ---- Goals -----------------------------------------------------------------

def create_goal(title: str, description: str | None = None,
                parent_id: int | None = None, kind: str = "objective") -> int:
    if kind not in GOAL_KINDS:
        raise ValueError(f"bad goal kind: {kind}")
    now = _now()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO goals (title, description, parent_id, kind, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, description, parent_id, kind, now, now),
        )
        return cur.lastrowid


def list_goals() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM goals ORDER BY kind, id").fetchall()
    return [dict(r) for r in rows]


def goal_tree() -> list[dict]:
    """Return goals as a nested tree keyed by id."""
    flat = list_goals()
    by_id = {g["id"]: {**g, "children": []} for g in flat}
    roots: list[dict] = []
    for g in by_id.values():
        if g["parent_id"] and g["parent_id"] in by_id:
            by_id[g["parent_id"]]["children"].append(g)
        else:
            roots.append(g)
    return roots


def update_goal(goal_id: int, **fields: Any) -> None:
    allowed = {"title", "description", "parent_id", "kind", "closed_at"}
    cols = [k for k in fields if k in allowed]
    if not cols:
        return
    sets = ", ".join(f"{k} = ?" for k in cols) + ", updated = ?"
    vals = [fields[k] for k in cols] + [_now(), goal_id]
    with _conn() as c:
        c.execute(f"UPDATE goals SET {sets} WHERE id = ?", vals)


def delete_goal(goal_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


# ---- Tasks -----------------------------------------------------------------

def create_task(title: str, *, description: str | None = None,
                status: str = "backlog", priority: str = "normal",
                owner: str | None = None, goal_id: int | None = None,
                parent_task_id: int | None = None,
                discord_thread_id: str | None = None,
                session_ref: str | None = None,
                actor: str = "human") -> int:
    if status not in STATUSES:
        raise ValueError(f"bad status: {status}")
    if priority not in PRIORITIES:
        raise ValueError(f"bad priority: {priority}")
    now = _now()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tasks (title, description, status, priority, owner, "
            "goal_id, parent_task_id, discord_thread_id, session_ref, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, description, status, priority, owner, goal_id,
             parent_task_id, discord_thread_id, session_ref, now, now),
        )
        tid = cur.lastrowid
        c.execute(
            "INSERT INTO task_events (task_id, actor, kind, payload, created) "
            "VALUES (?, ?, 'create', ?, ?)",
            (tid, actor, json.dumps({"title": title, "owner": owner}), now),
        )
    return tid


def get_task(task_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        task = dict(row)
        evs = c.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        task["events"] = [dict(e) for e in evs]
        return task


def list_tasks(*, status: str | None = None, owner: str | None = None,
               goal_id: int | None = None,
               include_done: bool = False) -> list[dict]:
    where: list[str] = []
    args: list[Any] = []
    if status:
        where.append("status = ?")
        args.append(status)
    if owner:
        where.append("owner = ?")
        args.append(owner)
    if goal_id is not None:
        where.append("goal_id = ?")
        args.append(goal_id)
    if not include_done and not status:
        where.append("status != 'done'")
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, updated DESC"
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def update_task(task_id: int, *, actor: str = "human", **fields: Any) -> None:
    allowed = {"title", "description", "status", "priority", "owner",
               "goal_id", "parent_task_id", "discord_thread_id",
               "session_ref", "closed_at", "cost_tokens"}
    cols = [k for k in fields if k in allowed]
    if not cols:
        return
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"bad status: {fields['status']}")
    if "priority" in fields and fields["priority"] not in PRIORITIES:
        raise ValueError(f"bad priority: {fields['priority']}")
    now = _now()
    sets = ", ".join(f"{k} = ?" for k in cols) + ", updated = ?"
    vals = [fields[k] for k in cols] + [now, task_id]
    # If status is being moved to done, stamp closed_at.
    if fields.get("status") == "done" and "closed_at" not in fields:
        sets = sets.rstrip(", updated = ?") + ", closed_at = ?, updated = ?"
        vals.insert(-1, now)
    with _conn() as c:
        c.execute(f"UPDATE tasks SET {sets} WHERE id = ?", vals)
        c.execute(
            "INSERT INTO task_events (task_id, actor, kind, payload, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, actor,
             "status" if "status" in fields else "update",
             json.dumps({k: fields[k] for k in cols}), now),
        )


def add_comment(task_id: int, actor: str, body: str) -> None:
    now = _now()
    with _conn() as c:
        c.execute(
            "INSERT INTO task_events (task_id, actor, kind, payload, created) "
            "VALUES (?, ?, 'comment', ?, ?)",
            (task_id, actor, json.dumps({"body": body}), now),
        )
        c.execute("UPDATE tasks SET updated = ? WHERE id = ?", (now, task_id))


def delete_task(task_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


# ---- Spend -----------------------------------------------------------------

def record_spend(agent: str, *, input_tokens: int = 0, output_tokens: int = 0,
                 cache_read_tokens: int = 0, cache_create_tokens: int = 0,
                 session_id: str | None = None,
                 date: str | None = None) -> None:
    """Upsert today's (or given date's) token totals for an agent.
    Idempotent-ish — call with incremental deltas. `session_id` bumps the
    session_count when a new session id is seen."""
    d = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = _now()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM agent_spend WHERE agent = ? AND date = ?",
            (agent, d),
        ).fetchone()
        if row:
            new_session = 1 if session_id and row["last_session"] != session_id else 0
            c.execute(
                "UPDATE agent_spend SET "
                "input_tokens = input_tokens + ?, "
                "output_tokens = output_tokens + ?, "
                "cache_read_tokens = cache_read_tokens + ?, "
                "cache_create_tokens = cache_create_tokens + ?, "
                "session_count = session_count + ?, "
                "last_session = COALESCE(?, last_session), "
                "updated = ? "
                "WHERE agent = ? AND date = ?",
                (input_tokens, output_tokens, cache_read_tokens,
                 cache_create_tokens, new_session, session_id, now, agent, d),
            )
        else:
            c.execute(
                "INSERT INTO agent_spend (agent, date, input_tokens, output_tokens, "
                "cache_read_tokens, cache_create_tokens, session_count, last_session, updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (agent, d, input_tokens, output_tokens, cache_read_tokens,
                 cache_create_tokens, 1 if session_id else 0, session_id, now),
            )


def reset_spend(agent: str | None = None, date: str | None = None) -> int:
    """Delete rows matching the filters. Returns rows deleted. Use for
    rebuilds after a trajectory rescan."""
    where: list[str] = []
    args: list[Any] = []
    if agent:
        where.append("agent = ?")
        args.append(agent)
    if date:
        where.append("date = ?")
        args.append(date)
    sql = "DELETE FROM agent_spend"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _conn() as c:
        cur = c.execute(sql, args)
        return cur.rowcount


def spend_totals(month: str | None = None) -> dict[str, dict]:
    """Return per-agent totals for the given YYYY-MM (default: current month).
    Shape: {agent: {input, output, cache_read, cache_create, sessions, total}}."""
    if month is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
    with _conn() as c:
        rows = c.execute(
            "SELECT agent, "
            "SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens, "
            "SUM(cache_read_tokens) AS cache_read_tokens, "
            "SUM(cache_create_tokens) AS cache_create_tokens, "
            "SUM(session_count) AS sessions "
            "FROM agent_spend WHERE date LIKE ? "
            "GROUP BY agent ORDER BY agent",
            (f"{month}-%",),
        ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        d["total"] = (d["input_tokens"] or 0) + (d["output_tokens"] or 0)
        out[d["agent"]] = d
    return out


def spend_daily(agent: str, days: int = 30) -> list[dict]:
    """Return last N days of totals for one agent (oldest first)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM agent_spend WHERE agent = ? "
            "ORDER BY date DESC LIMIT ?",
            (agent, days),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


# ---- Bootstrap on import ---------------------------------------------------

init_db()
