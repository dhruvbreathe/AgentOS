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
  ./.venv/bin/python task_ledger.py create --title "..." --to backend-developer \
      [--done-when "acceptance criteria"]
  ./.venv/bin/python task_ledger.py list [--to X] [--status open] [--json]
  ./.venv/bin/python task_ledger.py update <id-prefix> --status done \
      --evidence "how the done_when contract is met"
  ./.venv/bin/python task_ledger.py sweep [--days 3] [--json]
  ./.venv/bin/python task_ledger.py orphans [--hours 48] [--json]

Status vocabulary (matches existing rows + seed_tasks.py):
  open | in_progress | blocked | waiting | done

Ledger v2 (Wave 3 P0-4, 2026-07-18 — Hermes completion contracts):
  - `done_when`: acceptance criteria captured AT CREATION. A task that
    carries a contract cannot be closed without `--evidence` (or --result)
    stating how the contract is met. Kills "I think I fixed it".
  - `orphans`: active tasks whose to_agent is not in the live agents/
    roster (retired agent, typo). Surface these to the operator — they rot
    silently otherwise (the Mira-retirement failure, Jul 15).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("task-ledger")

_UA = "PranaAgentOS-TaskLedger/1.0"
_TIMEOUT_S = 8
STATUSES = {"open", "in_progress", "blocked", "waiting", "done"}
# "todo" = legacy status on 4 pre-Phase-1 seed rows (classifier blocked the
# DB migration, 2026-07-05). Reads must include it or those rows are
# invisible to list/sweep forever; writes stay canonical ("open").
ACTIVE_STATUSES = ("open", "in_progress", "blocked", "waiting", "todo")


_AGENTS_DIR = Path(__file__).parent / "agents"
# Human owners: tasks assigned to the operator are owned, not orphaned.
_HUMAN_OWNERS = {"dhruv", "operator"}


def _now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _roster() -> set[str]:
    """Live agent names = dirs under agents/ that hold an agent.yaml.
    Empty set on any FS problem — callers must treat that as 'unknown',
    never 'everyone is an orphan'."""
    try:
        return {
            p.name
            for p in _AGENTS_DIR.iterdir()
            if p.is_dir()
            and not p.name.startswith(("_", "."))
            and (p / "agent.yaml").exists()
        }
    except OSError:
        return set()


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
    done_when: str | None = None,
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
        "done_when": (done_when or "")[:1000] or None,
        "assigned_at": now,
        "created_at": now,
        "updated_at": now,
    }
    out = _request("POST", "tasks", row)
    return out[0] if isinstance(out, list) and out else out


def update_task(task_id_prefix: str, **fields: Any) -> dict:
    """Update by full UUID or unambiguous prefix. Auto-stamps updated_at,
    plus started_at / completed_at on the matching transitions.

    Completion-contract enforcement (v2): a task whose row carries
    `done_when` cannot transition to done unless evidence exists — either
    an incoming `evidence`/`result` field or one already on the row. The
    contract was stated at creation; closing means showing your work."""
    status = fields.get("status")
    if status and status not in STATUSES:
        raise ValueError(f"bad status {status!r}; allowed: {sorted(STATUSES)}")
    # PostgREST returns 404 for a `like` filter on the uuid `id` column, so
    # prefix-resolution is done client-side: fetch ids, filter in Python.
    all_ids = _request("GET", "tasks?select=id,status,done_when,result,evidence")
    matches = [r for r in all_ids if r["id"].startswith(task_id_prefix)][:5]
    if not matches:
        raise ValueError(f"no task with id prefix {task_id_prefix!r}")
    if len(matches) > 1:
        ids = ", ".join(m["id"][:8] for m in matches)
        raise ValueError(f"ambiguous prefix {task_id_prefix!r}: {ids}")
    row = matches[0]
    task_id = row["id"]
    if status == "done" and row.get("done_when"):
        evidence = (
            fields.get("evidence")
            or fields.get("result")
            or row.get("evidence")
            or row.get("result")
        )
        if not (evidence or "").strip():
            raise ValueError(
                f"task {task_id[:8]} carries a done_when contract: "
                f"{row['done_when']!r}\n"
                f"Closing it requires evidence — rerun with "
                f"--evidence \"<how the contract is met>\""
            )
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
        "blocked_reason,due_at,done_when,created_at,updated_at",
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
        f"blocked_reason,due_at,done_when,created_at,updated_at"
        f"&status=in.({','.join(ACTIVE_STATUSES)})"
        f"&updated_at=lt.{cutoff}"
        "&order=updated_at.asc&limit=100"
    )
    return _request("GET", q)


def orphans(min_hours: float = 0.0) -> list[dict]:
    """Active tasks whose to_agent is not a live agent (and not a human
    owner) — work that will NEVER be picked up. The Mira retirement (Jul 15)
    left 3-4 of these rotting silently.

    `min_hours` filters to tasks untouched for at least that long; the
    escalation contract is 48h → surface to the operator. Returns [] when
    the roster can't be read (unknown ≠ everyone-is-an-orphan)."""
    roster = _roster()
    if not roster:
        return []
    known = roster | _HUMAN_OWNERS
    cutoff = datetime.now(timezone.utc) - timedelta(hours=min_hours)
    out = []
    rows = list_tasks(limit=200)
    if len(rows) >= 200:
        # No silent caps: past the fetch limit, unscanned tasks would rot
        # invisibly, exactly what the orphan sweep exists to prevent.
        log.warning(
            "orphan sweep hit the 200-row fetch cap; active tasks beyond "
            "it were NOT scanned. Raise the limit or page."
        )
    for t in rows:
        if (t.get("to_agent") or "") in known:
            continue
        try:
            upd = datetime.fromisoformat(
                (t.get("updated_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            upd = None
        if upd is None or upd <= cutoff:
            out.append(t)
    return out


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
    roster = _roster()
    known = (roster | _HUMAN_OWNERS) if roster else set()
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
        contract = "  dw✓" if t.get("done_when") else ""
        no_owner = (
            "  ⚠️ NO-OWNER"
            if known and (t.get("to_agent") or "") not in known
            and t.get("status") != "done"
            else ""
        )
        print(
            f"{t['id'][:8]}  {t['status']:<12} {t['priority']:<7} "
            f"{t['from_agent']}→{t['to_agent']:<14} "
            f"age {_fmt_age(t.get('updated_at')):<4} {t['title'][:70]}"
            f"{contract}{blocked}{overdue}{no_owner}"
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
    c.add_argument("--done-when", default=None, dest="done_when",
                   help="acceptance criteria; closing then requires --evidence")

    ls = sub.add_parser("list", help="list active tasks")
    ls.add_argument("--to", default=None, dest="to_agent")
    ls.add_argument("--status", default=None, choices=sorted(STATUSES))
    ls.add_argument("--all", action="store_true", help="include done")
    ls.add_argument("--json", action="store_true")

    u = sub.add_parser("update", help="update a task by id prefix")
    u.add_argument("task_id")
    u.add_argument("--status", default=None, choices=sorted(STATUSES))
    u.add_argument("--result", default=None)
    u.add_argument("--evidence", default=None,
                   help="how the done_when contract is met (required to "
                        "close a contracted task)")
    u.add_argument("--done-when", default=None, dest="done_when",
                   help="add/replace the acceptance contract")
    u.add_argument("--blocked-reason", default=None, dest="blocked_reason")
    u.add_argument("--priority", default=None,
                   choices=["low", "medium", "high", "urgent"])
    u.add_argument("--due", default=None, help="YYYY-MM-DD")

    sw = sub.add_parser("sweep", help="active tasks stale for N+ days")
    sw.add_argument("--days", type=int, default=3)
    sw.add_argument("--json", action="store_true")

    orp = sub.add_parser(
        "orphans",
        help="active tasks assigned to no live agent (retired/typo owner)",
    )
    orp.add_argument("--hours", type=float, default=48,
                     help="only tasks untouched this long (default 48 — "
                          "the operator-escalation threshold)")
    orp.add_argument("--json", action="store_true")

    args = ap.parse_args()

    if args.cmd == "create":
        due = f"{args.due}T00:00:00Z" if args.due else None
        t = create_task(
            args.title, args.to_agent, from_agent=args.from_agent,
            description=args.desc, priority=args.priority, due_at=due,
            task_type=args.task_type, done_when=args.done_when,
        )
        dw = "  (contract set)" if args.done_when else ""
        print(f"created {t['id'][:8]}  {t['title']}{dw}")
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
        if args.evidence:
            fields["evidence"] = args.evidence
        if args.done_when:
            fields["done_when"] = args.done_when
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
    elif args.cmd == "orphans":
        rows = orphans(min_hours=args.hours)
        if args.json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print(f"clean: no ownerless active task untouched {args.hours:.0f}h+")
        else:
            print(
                f"{len(rows)} ownerless active task(s) untouched "
                f"{args.hours:.0f}h+ — reassign or close (escalate to "
                f"operator per 48h contract):"
            )
            _print_rows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
