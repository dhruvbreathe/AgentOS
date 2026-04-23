#!/usr/bin/env python3
"""track_spend.py — scan trajectory JSONLs and populate the agent_spend table.

Run on-demand (or from a cron in `agents/<name>/tasks/`). Reads every
trajectory file under logs/trajectories/<agent>/*.jsonl, looks for usage
records (fields `usage`, `input_tokens`, `output_tokens`, or nested in
`message.usage`), and upserts daily totals. Idempotent re-runs are safe
because it resets the (agent, date) row before re-filling from scratch
within the scan window.

Usage:
    python scripts/track_spend.py                   # all agents, full history
    python scripts/track_spend.py --agent main      # one agent
    python scripts/track_spend.py --days 30         # last 30d
    python scripts/track_spend.py --rebuild         # wipe before scan
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running directly from scripts/ without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tasks  # noqa: E402

TRAJ_DIR = ROOT / "logs" / "trajectories"


def _extract_usage(obj: dict) -> dict[str, int]:
    """Pull token counts out of whatever shape the SDK happens to write.
    Returns {input, output, cache_read, cache_create}, defaulting to 0."""
    out = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    usage = obj.get("usage") or obj.get("message", {}).get("usage") or {}
    if isinstance(usage, dict):
        out["input"] += int(usage.get("input_tokens") or 0)
        out["output"] += int(usage.get("output_tokens") or 0)
        out["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
        out["cache_create"] += int(usage.get("cache_creation_input_tokens") or 0)
    # Some SDK paths emit flat fields at the event root.
    for k_src, k_dst in (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_create"),
    ):
        if k_src in obj and isinstance(obj[k_src], (int, float)):
            out[k_dst] += int(obj[k_src])
    return out


def _event_date(obj: dict) -> str | None:
    ts = (obj.get("timestamp") or obj.get("ts")
          or obj.get("created") or obj.get("created_at"))
    if not ts:
        return None
    try:
        # Accepts both ISO strings and epoch floats.
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def scan_agent(agent: str, *, since: datetime | None = None) -> dict:
    """Iterate every event in every trajectory file for `agent`. Returns
    a running summary {date: totals}."""
    dir_ = TRAJ_DIR / agent
    if not dir_.is_dir():
        return {"files": 0, "events_with_usage": 0, "days": 0}
    per_day: dict[str, dict[str, int]] = {}
    sessions_by_day: dict[str, set[str]] = {}
    files = 0
    events = 0
    for jsonl in sorted(dir_.glob("*.jsonl")):
        # Skip obviously old files when --days is given.
        if since and datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc) < since:
            continue
        files += 1
        session_id = jsonl.stem
        with jsonl.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                u = _extract_usage(obj)
                if sum(u.values()) == 0:
                    continue
                d = _event_date(obj)
                if not d:
                    # Fall back to file mtime date.
                    d = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
                bucket = per_day.setdefault(d, {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0})
                for k, v in u.items():
                    bucket[k] += v
                sessions_by_day.setdefault(d, set()).add(session_id)
                events += 1
    # Flush into SQLite.
    for d, totals in per_day.items():
        tasks.reset_spend(agent=agent, date=d)  # idempotent rebuild of the day
        for session_id in sessions_by_day.get(d, {None}):
            pass  # record_spend will bump session_count per new id
        tasks.record_spend(
            agent,
            input_tokens=totals["input"],
            output_tokens=totals["output"],
            cache_read_tokens=totals["cache_read"],
            cache_create_tokens=totals["cache_create"],
            session_id=next(iter(sessions_by_day.get(d, {None}))),
            date=d,
        )
    return {"files": files, "events_with_usage": events, "days": len(per_day)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", help="single agent name; default = all")
    ap.add_argument("--days", type=int, help="only scan files modified in last N days")
    ap.add_argument("--rebuild", action="store_true",
                    help="wipe agent_spend before scan (default is incremental per-day)")
    args = ap.parse_args()

    since = None
    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    if args.rebuild:
        n = tasks.reset_spend(agent=args.agent)
        print(f"rebuild: cleared {n} rows")

    agents = [args.agent] if args.agent else sorted(
        d.name for d in TRAJ_DIR.iterdir() if d.is_dir()
    ) if TRAJ_DIR.is_dir() else []
    if not agents:
        print(f"no trajectory dirs under {TRAJ_DIR}")
        return 1

    for a in agents:
        s = scan_agent(a, since=since)
        print(f"  {a}: {s['files']} files, {s['events_with_usage']} events, {s['days']} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
