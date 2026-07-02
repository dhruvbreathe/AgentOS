#!/usr/bin/env python3
"""agent_cost_report.py — per-agent notional spend, OpenClaw-pattern cost view.

Reads the `agent_spend` table (populated by track_spend.py from trajectory
usage records), maps each agent to its configured model, applies public
list pricing, and prints a ranked per-agent cost report over a window.

Note on "notional": we run on Dhruv's Claude.ai subscription at a FIXED
$100/mo (downgraded from $200 on 2026-06-25). Real per-token billing is $0;
the binding constraint is the plan's weekly usage ceiling, not dollars.
These dollar figures are a SHARE-OF-CEILING signal: which agents eat the
most of the shared budget, so when usage throttles we know who to downshift
first (heavy routine agents -> Haiku, or thin their crons). Token counts are
exact; dollars are a relative-weight proxy, not an invoice.

Usage:
    python scripts/agent_cost_report.py                # last 7 days
    python scripts/agent_cost_report.py --days 30
    python scripts/agent_cost_report.py --refresh      # run track_spend first
    python scripts/agent_cost_report.py --json         # machine-readable
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import tasks  # noqa: E402

# Public list price per million tokens (USD). Used as a relative-weight proxy.
# Cache read ~= 0.1x input, cache write ~= 1.25x input (Anthropic convention).
PRICING = {
    "opus":   {"input": 15.0, "output": 75.0, "cache_read": 1.50, "cache_create": 18.75},
    "sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
    "haiku":  {"input": 0.80, "output": 4.0,  "cache_read": 0.08, "cache_create": 1.00},
}


def _model_family(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    return "sonnet"  # default + sonnet family


def _agent_model(agent: str) -> str:
    """Read the agent's primary model from its agent.yaml; default opus."""
    y = ROOT / "agents" / agent / "agent.yaml"
    if y.exists():
        try:
            cfg = yaml.safe_load(y.read_text()) or {}
            return cfg.get("model") or "claude-opus-4-8"
        except Exception:
            pass
    return "claude-opus-4-8"


def _cost(row: dict, fam: str) -> float:
    p = PRICING[fam]
    return (
        row.get("input_tokens", 0) * p["input"]
        + row.get("output_tokens", 0) * p["output"]
        + row.get("cache_read_tokens", 0) * p["cache_read"]
        + row.get("cache_create_tokens", 0) * p["cache_create"]
    ) / 1_000_000


def collect(days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    with tasks._conn() as c:
        rows = c.execute(
            "SELECT agent, "
            "SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens, "
            "SUM(cache_read_tokens) cache_read_tokens, "
            "SUM(cache_create_tokens) cache_create_tokens, "
            "SUM(session_count) session_count "
            "FROM agent_spend WHERE date >= ? GROUP BY agent",
            (cutoff,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        model = _agent_model(d["agent"])
        fam = _model_family(model)
        d["model"] = model.replace("claude-", "")
        d["cost"] = _cost(d, fam)
        d["total_tokens"] = (
            d["input_tokens"] + d["output_tokens"]
            + d["cache_read_tokens"] + d["cache_create_tokens"]
        )
        out.append(d)
    out.sort(key=lambda x: x["cost"], reverse=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--refresh", action="store_true",
                    help="run track_spend.py before reporting")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.refresh:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "track_spend.py"),
             "--days", str(args.days)],
            check=False,
        )

    rows = collect(args.days)
    total = sum(r["cost"] for r in rows)

    if args.json:
        print(json.dumps({"days": args.days, "total_notional_usd": round(total, 2),
                          "agents": rows}, indent=2, default=str))
        return 0

    print(f"Per-agent notional spend, last {args.days}d "
          f"(relative-weight proxy; real billing = $0 on subscription)\n")
    print(f"{'agent':<28}{'model':<16}{'tokens':>14}{'notional $':>12}")
    print("-" * 70)
    for r in rows:
        print(f"{r['agent']:<28}{r['model']:<16}"
              f"{r['total_tokens']:>14,}{r['cost']:>12.2f}")
    print("-" * 70)
    print(f"{'TOTAL':<44}{sum(r['total_tokens'] for r in rows):>14,}{total:>12.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
