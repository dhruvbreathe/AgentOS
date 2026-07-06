"""Task ledger on the Agent Nervous System DB (Phase 1, masterplan item 6).

One shared ledger: `agents.tasks` in the NS Supabase project. Three writers:
  1. `send_to_agent` auto-creates an entry per first-hop handoff (agent_tools.py).
  2. Agents update / close entries via this file's CLI (Bash).
  3. Tempo's heartbeat + scrum read `sweep` for aging work and escalate.

Design rules:
  - Fail-open everywhere: the ledger is bookkeeping. A ledger outage must
    never block a route, a turn, or a cron.
  - Timestamps in Z-format (`%Y-%m-%dT%H:%M:%SZ`), never `+00:00` — PostgREST
    URL-decodes `+` to a space and 400s (LEARNINGS 2026-06-10).
  - Explicit User-Agent on urllib (Cloudflare 1010, LESSONS #3).
  - `updated_at` is stamped explicitly on every write — no DB trigger assumed.

CLI (agents run via Bash):
  ./.venv/bin/python task_ledger.py create --title "..." --to backend-developer
  ./.venv/bin/python task_ledger.py list [--to X] [--status open] [--json]
  ./.venv/bin/python task_ledger.py update <id-prefix> --status done --result "..."
  ./.venv/bin/python task_ledger.py sweep [--days 3] [--json]

Status vocabulary (matches existing rows + seed_tasks.py):
  open | in_progress | blocked | waiting | done
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("task-ledger")

_UA = "PranaAgentOS-TaskLedger/1.0"
_TIMEOUT_S = 8
STATUSES = {"open", "in_progress", "blocked", "waiting", "done"}
# "todo" = legacy status on 4 pre-Phase-1 seed rows (classifier blocked the
# DB migration, 2026-07-05). Reads must include it or those rows are
# invisible to list/sweep forever; writes stay canonical ("open").
ACTIVE_STATUSES = ("open", "in_progress", "blocked", "waiting", "todo")


def _now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_url() -> str | None:
    url = (os.environ.get("SUPABASE_AGENT_NS_URL") or "").rstrip("/")
    if not url:
        return None
    if not url.endswith("/rest/v1"):
        url = f"{url}/rest/v1"
    return url


def _key() -> str | None:
    return os.environ.get("SUPABASE_AGENT_NS_SERVICE_ROLE_KEY") or None


def _headers(write: bool = False) -> dict[str, str] | None:
    key = _key()
    if not key:
        return None
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "User-Agent": _UA,
        "Content-Type": "application/json",
        # agents schema, both directions
        "Accept-Profile": "agents",
    }
    if write:
        h["Content-Profile"] = "agents"
        h["Prefer"] = "return=representation"
    return h


def _request(method: str, path: str, payload: dict | None = None) -> Any:
    """Raw REST call. Raises on transport/HTTP errors — callers decide
    whether to fail open (hook) or surface (CLI)."""
    base = _base_url()
    headers = _headers(write=payload is not None or method in ("PATCH", "POST"))
    if not base or not headers:
        raise RuntimeError("SUPABASE_AGENT_NS_URL / _SERVICE_ROLE_KEY not set")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{base}/{path}", data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        body = resp.read().decode() or "[]"
    return json.loads(body)


# ---- Core operations ---------------------------------------------------

def create_task(
    title: str,
    to_agent: str,
    from_agent: str = "main",
    description: str | None = None,
    priority: str = "medium",
    due_at: str | None = None,
    task_type: str = "task",
) -> dict:
    now = _now_z()
    row = {
        "title": title[:200],
        "description": (description or "")[:4000] or None,
        "type": task_type,
        "from_agent": from_agent,
        "to_agent": to_agent,
        "status": "open",
        "priority": priority,
        "due_at": due_at,
        "assigned_at": now,
        "created_at": now,
        "updated_at": now,
    }
    out = _request("POST", "tasks", row)
    return out[0] if isinstance(out, list) and out else out


def update_task(task_id_prefix: str, **fields: Any) -> dict:
    """Update by full UUID or unambiguous prefix. Auto-stamps updated_at,
    plus started_at / completed_at on the matching transitions."""
    status = fields.get("status")
    if status and status not in STATUSES:
        raise ValueError(f"bad status {status!r}; allowed: {sorted(STATUSES)}")
    # PostgREST returns 404 for a `like` filter on the uuid `id` column, so
    # prefix-resolution is done client-side: fetch ids, filter in Python.
    all_ids = _request("GET", "tasks?select=id,status")
    matches = [r for r in all_ids if r["id"].startswith(task_id_prefix)][:5]
    if not matches:
        raise ValueError(f"no task with id prefix {task_id_prefix!r}")
    if len(matches) > 1:
        ids = ", ".join(m["id"][:8] for m in matches)
        raise ValueError(f"ambiguous prefix {task_id_prefix!r}: {ids}")
    task_id = matches[0]["id"]
    fields["updated_at"] = _now_z()
    if status == "in_progress" and "started_at" not in fields:
        fields["started_at"] = fields["updated_at"]
    if status == "done" and "completed_at" not in fields:
        fields["completed_at"] = fields["updated_at"]
    out = _request("PATCH", f"tasks?id=eq.{task_id}", fields)
    return out[0] if isinstance(out, list) and out else out


def list_tasks(
    to_agent: str | None = None,
    status: str | None = None,
    include_done: bool = False,
    limit: int = 50,
) -> list[dict]:
    q = [
        "select=id,title,type,from_agent,to_agent,status,priority,"
        "blocked_reason,due_at,created_at,updated_at",
        f"limit={limit}",
        "order=updated_at.desc",
    ]
    if to_agent:
        q.append(f"to_agent=eq.{to_agent}")
    if status:
        q.append(f"status=eq.{status}")
    elif not include_done:
        q.append(f"status=in.({','.join(ACTIVE_STATUSES)})")
    return _request("GET", "tasks?" + "&".join(q))


def sweep(days: int = 3) -> list[dict]:
    """Active tasks not touched in `days` days — the escalation feed.
    Oldest-neglect first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    q = (
        "tasks?select=id,title,from_agent,to_agent,status,priority,"
        f"blocked_reason,due_at,created_at,updated_at"
        f"&status=in.({','.join(ACTIVE_STATUSES)})"
        f"&updated_at=lt.{cutoff}"
        "&order=updated_at.asc&limit=100"
    )
    return _request("GET", q)


# ---- Async fire-and-forget for the send_to_agent hook ------------------

async def aledger_create_handoff(
    from_agent: str, to_agent: str, message: str
) -> str | None:
    """Best-effort ledger entry for a routed handoff. Returns the short task
    id on success, None on ANY failure (fail-open: the route already
    happened; bookkeeping must not un-happen it)."""
    try:
        import aiohttp

        base = _base_url()
        headers = _headers(write=True)
        if not base or not headers:
            return None
        first_line = message.strip().splitlines()[0] if message.strip() else "handoff"
        now = _now_z()
        row = {
            "title": first_line[:200],
            "description": message[:4000],
            "type": "handoff",
            "from_agent": from_agent,
            "to_agent": to_agent,
            "status": "open",
            "priority": "medium",
            "assigned_at": now,
            "created_at": now,
            "updated_at": now,
        }
        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{base}/tasks", json=row, headers=headers
            ) as resp:
                if resp.status >= 300:
                    body = await resp.text()
                    log.warning(
                        "ledger auto-create %s→%s failed: %s %s",
                        from_agent, to_agent, resp.status, body[:200],
                    )
                    return None
                out = await resp.json()
        task_id = (out[0] if isinstance(out, list) and out else out).get("id", "")
        return task_id[:8] or None
    except Exception as e:  # noqa: BLE001 — fail-open by contract
        log.warning("ledger auto-create %s→%s errored: %s", from_agent, to_agent, e)
        return None


# ---- CLI ----------------------------------------------------------------

def _fmt_age(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - then
        if delta.days:
            return f"{delta.days}d"
        return f"{delta.seconds // 3600}h"
    except Exception:
        return "?"


def _print_rows(rows: list[dict]) -> None:
    if not rows:
        print("(no tasks)")
        return
    for t in rows:
        overdue = ""
        if t.get("due_at"):
            try:
                due = datetime.fromisoformat(t["due_at"].replace("Z", "+00:00"))
                if due < datetime.now(timezone.utc) and t["status"] != "done":
                    overdue = "  ⚠️ OVERDUE"
            except Exception:
                pass
        blocked = f"  [{t['blocked_reason']}]" if t.get("blocked_reason") else ""
        print(
            f"{t['id'][:8]}  {t['status']:<12} {t['priority']:<7} "
            f"{t['from_agent']}→{t['to_agent']:<14} "
            f"age {_fmt_age(t.get('updated_at')):<4} {t['title'][:70]}"
            f"{blocked}{overdue}"
        )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="NS task ledger (agents.tasks)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create a task")
    c.add_argument("--title", required=True)
    c.add_argument("--to", required=True, dest="to_agent")
    c.add_argument("--from", default=os.environ.get("AGENT_NAME", "main"),
                   dest="from_agent")
    c.add_argument("--desc", default=None)
    c.add_argument("--priority", default="medium",
                   choices=["low", "medium", "high", "urgent"])
    c.add_argument("--due", default=None, help="YYYY-MM-DD")
    c.add_argument("--type", default="task", dest="task_type")

    ls = sub.add_parser("list", help="list active tasks")
    ls.add_argument("--to", default=None, dest="to_agent")
    ls.add_argument("--status", default=None, choices=sorted(STATUSES))
    ls.add_argument("--all", action="store_true", help="include done")
    ls.add_argument("--json", action="store_true")

    u = sub.add_parser("update", help="update a task by id prefix")
    u.add_argument("task_id")
    u.add_argument("--status", default=None, choices=sorted(STATUSES))
    u.add_argument("--result", default=None)
    u.add_argument("--blocked-reason", default=None, dest="blocked_reason")
    u.add_argument("--priority", default=None,
                   choices=["low", "medium", "high", "urgent"])
    u.add_argument("--due", default=None, help="YYYY-MM-DD")

    sw = sub.add_parser("sweep", help="active tasks stale for N+ days")
    sw.add_argument("--days", type=int, default=3)
    sw.add_argument("--json", action="store_true")

    args = ap.parse_args()

    if args.cmd == "create":
        due = f"{args.due}T00:00:00Z" if args.due else None
        t = create_task(
            args.title, args.to_agent, from_agent=args.from_agent,
            description=args.desc, priority=args.priority, due_at=due,
            task_type=args.task_type,
        )
        print(f"created {t['id'][:8]}  {t['title']}")
    elif args.cmd == "list":
        rows = list_tasks(
            to_agent=args.to_agent, status=args.status, include_done=args.all
        )
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            _print_rows(rows)
    elif args.cmd == "update":
        fields: dict[str, Any] = {}
        if args.status:
            fields["status"] = args.status
        if args.result:
            fields["result"] = args.result
        if args.blocked_reason:
            fields["blocked_reason"] = args.blocked_reason
            fields.setdefault("status", "blocked")
        if args.priority:
            fields["priority"] = args.priority
        if args.due:
            fields["due_at"] = f"{args.due}T00:00:00Z"
        if not fields:
            print("nothing to update — pass --status/--result/…")
            return 2
        t = update_task(args.task_id, **fields)
        print(f"updated {t['id'][:8]} → {t['status']}")
    elif args.cmd == "sweep":
        rows = sweep(days=args.days)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            if not rows:
                print(f"clean: no active task older than {args.days}d")
            else:
                print(f"{len(rows)} task(s) stale {args.days}d+ (oldest first):")
                _print_rows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
