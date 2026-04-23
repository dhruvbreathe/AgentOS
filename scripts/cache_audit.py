#!/usr/bin/env python3
"""
cache_audit.py — Measure prompt-cache hit rate per agent from trajectory logs.

Reads JSONL trajectory files under logs/trajectories/<agent>/<session>.jsonl
and reports:
  - total input tokens
  - cache_read tokens
  - cache_creation tokens
  - hit rate (cache_read / total input) per agent and roster-wide

Why care: the 5-minute Anthropic prompt cache TTL means a well-structured
session should have >60% cache hit rate on multi-turn runs. If we're
below that, something's wrong (prompt churn, too-short turns, model swaps).

Usage:
  python scripts/cache_audit.py                  # all agents, all time
  python scripts/cache_audit.py --agent main     # just one agent
  python scripts/cache_audit.py --days 7         # last 7 days only
  python scripts/cache_audit.py --json           # machine-readable output
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
TRAJ_DIR = ROOT / "logs" / "trajectories"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--agent", help="Filter to one agent")
    p.add_argument("--days", type=int, default=0,
                   help="Only include sessions modified in last N days (0=all)")
    p.add_argument("--json", action="store_true", help="JSON output")
    return p.parse_args()


def iter_sessions(agent_filter=None, days=0):
    if not TRAJ_DIR.exists():
        return
    cutoff = None
    if days > 0:
        cutoff = datetime.now() - timedelta(days=days)
    for agent_dir in sorted(TRAJ_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        if agent_filter and agent_dir.name != agent_filter:
            continue
        for jsonl in agent_dir.glob("*.jsonl"):
            if cutoff:
                mtime = datetime.fromtimestamp(jsonl.stat().st_mtime)
                if mtime < cutoff:
                    continue
            yield agent_dir.name, jsonl


def measure_session(path: Path) -> dict:
    """Parse a single JSONL and sum token usage fields."""
    totals = {
        "input": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "output": 0,
        "turns": 0,
    }
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # SDK trajectory events include usage blocks on assistant messages.
                usage = rec.get("usage") or rec.get("message", {}).get("usage")
                if not usage:
                    continue
                totals["turns"] += 1
                totals["input"] += usage.get("input_tokens", 0) or 0
                totals["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
                totals["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0
                totals["output"] += usage.get("output_tokens", 0) or 0
    except OSError:
        pass
    return totals


def main():
    args = parse_args()

    by_agent = defaultdict(lambda: {
        "input": 0, "cache_read": 0, "cache_creation": 0,
        "output": 0, "turns": 0, "sessions": 0,
    })

    for agent, jsonl in iter_sessions(args.agent, args.days):
        m = measure_session(jsonl)
        if m["turns"] == 0:
            continue
        a = by_agent[agent]
        for k in ("input", "cache_read", "cache_creation", "output", "turns"):
            a[k] += m[k]
        a["sessions"] += 1

    # Roster totals
    roster = {"input": 0, "cache_read": 0, "cache_creation": 0,
              "output": 0, "turns": 0, "sessions": 0}
    for a in by_agent.values():
        for k in roster:
            roster[k] += a[k]

    def rate(cache_read, total_input):
        total = total_input + cache_read
        if total == 0:
            return 0.0
        return cache_read / total

    if args.json:
        out = {
            "agents": {
                name: {
                    **stats,
                    "cache_hit_rate": rate(stats["cache_read"], stats["input"]),
                }
                for name, stats in by_agent.items()
            },
            "roster": {
                **roster,
                "cache_hit_rate": rate(roster["cache_read"], roster["input"]),
            },
        }
        print(json.dumps(out, indent=2))
        return

    print(f"{'agent':<28} {'sess':>5} {'turns':>6} {'in':>10} {'cache_r':>10} {'cache_c':>10} {'out':>10} {'hit%':>6}")
    print("-" * 95)
    for name in sorted(by_agent):
        a = by_agent[name]
        hit = rate(a["cache_read"], a["input"]) * 100
        print(f"{name:<28} {a['sessions']:>5} {a['turns']:>6} "
              f"{a['input']:>10,} {a['cache_read']:>10,} "
              f"{a['cache_creation']:>10,} {a['output']:>10,} {hit:>5.1f}%")
    print("-" * 95)
    hit = rate(roster["cache_read"], roster["input"]) * 100
    print(f"{'ROSTER':<28} {roster['sessions']:>5} {roster['turns']:>6} "
          f"{roster['input']:>10,} {roster['cache_read']:>10,} "
          f"{roster['cache_creation']:>10,} {roster['output']:>10,} {hit:>5.1f}%")

    # Verdict
    if hit >= 60:
        print(f"\n✅ Cache hit rate {hit:.1f}% — healthy (>60% target).")
    elif hit >= 40:
        print(f"\n⚠️  Cache hit rate {hit:.1f}% — mediocre. Look at prompt churn or short sessions.")
    else:
        print(f"\n❌ Cache hit rate {hit:.1f}% — low. Prompts likely changing every turn or TTL expiring.")


if __name__ == "__main__":
    main()
