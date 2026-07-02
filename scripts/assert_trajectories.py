"""assert_trajectories.py — deterministic regression checks over agent outputs.

Fix #2 from the OS-vs-X improvement map (2026-06-17): a lightweight assertion
harness that catches load-bearing-rule violations BEFORE they reach Dhruv,
without burning subscription cycles on a flaky agent-runner.

It scans recent trajectory JSONL and asserts the rules that measure what
actually SHIPS:
  - no obvious secret leaks into a posted output (text_lint does NOT strip
    secrets, so a key in a `text` event really would ship — this is a fail)
  - cross-agent routes stay under the 1900-char silent-truncation threshold
    (LEARNINGS 2026-05-08 — these split silently and ship truncated)

Plus one informational telemetry read:
  - dash-drift: how many em-dashes / en-dashes each agent GENERATED on an
    outbound surface and had stripped by text_lint.sanitize() before the post.
    This is read from logs/text_lint.jsonl, the authoritative record of the
    guard firing. It is INFO, not a fail: the dash was caught and did NOT
    ship. (Earlier this harness scanned raw trajectory `text` events for
    dashes and called them fails — wrong: those events are logged pre-strip,
    so they never reached Discord. text_lint.jsonl is the honest signal.)

The two assertions are deterministic and true-positive-heavy on purpose:
every fired assertion should be a real thing worth a human glance. Golden-
prompt behavioral evals (running an agent against canned inputs and checking
the reply) are phase 2 — heavier and flakier, so not in v1.

Usage:
    python scripts/assert_trajectories.py main                 # last 1 day
    python scripts/assert_trajectories.py main backend qa
    python scripts/assert_trajectories.py --all --days 1
    python scripts/assert_trajectories.py --all --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAJ_ROOT = ROOT / "logs" / "trajectories"
TEXT_LINT_LOG = ROOT / "logs" / "text_lint.jsonl"

SEND_TOOL = "mcp__agent_comms__send_to_agent"
ROUTE_LIMIT = 1900  # send_to_agent payloads at/over this split silently

# Real key prefixes only — kept specific so a fired assertion is a real leak,
# not a doc snippet. Generic "Bearer ..." is intentionally excluded.
_SECRET_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{20,}|xai-[A-Za-z0-9]{20,}|sbp_[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def _truncate(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "\u2026"


def _parse(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return events


# ---- assertions ------------------------------------------------------------
# Each returns a list of violation dicts: {check, sev, detail, ts}.

def _check_secret(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        if ev.get("type") != "text":
            continue
        m = _SECRET_RE.search(ev.get("content") or "")
        if m:
            tok = m.group(0)
            out.append({"check": "no-leaked-secret", "sev": "fail",
                        "ts": ev.get("ts", ""),
                        "detail": f"possible secret in output: {tok[:10]}…"})
    return out


def _check_route_size(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        if ev.get("type") == "tool_use" and ev.get("name") == SEND_TOOL:
            msg = str((ev.get("input") or {}).get("message") or "")
            if len(msg) >= ROUTE_LIMIT:
                to = (ev.get("input") or {}).get("agent", "?")
                out.append({"check": "route-under-1900", "sev": "warn",
                            "ts": ev.get("ts", ""),
                            "detail": f"route → {to} is {len(msg)} chars (≥{ROUTE_LIMIT}, "
                                      f"splits silently — route a vault path instead)"})
    return out


ASSERTIONS = (_check_secret, _check_route_size)


# ---- dash-drift telemetry (info, not a fail) -------------------------------
# Reads logs/text_lint.jsonl: the authoritative record of em-dashes the
# sanitizer caught on an outbound surface and stripped before the post. A
# count here means the guard FIRED and nothing shipped — it's a generation-
# hygiene signal, not a regression. surface="test" is a manual probe, skipped.

def dash_drift(days: int) -> dict:
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    by_agent: dict[str, int] = {}
    by_surface: dict[str, int] = {}
    total = 0
    for ev in _parse(TEXT_LINT_LOG):
        if (ev.get("ts") or "") < cutoff_iso:
            continue
        surface = ev.get("surface") or "unknown"
        if surface == "test":
            continue
        hits = int(ev.get("hits") or 0)
        if hits <= 0:
            continue
        agent = ev.get("agent") or "unknown"
        by_agent[agent] = by_agent.get(agent, 0) + hits
        by_surface[surface] = by_surface.get(surface, 0) + hits
        total += hits
    return {"total": total, "by_agent": by_agent, "by_surface": by_surface,
            "window_days": days}


def _recent(agent: str, days: int) -> list[Path]:
    d = TRAJ_ROOT / agent
    if not d.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for p in d.glob("*.jsonl"):
        try:
            mt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mt >= cutoff:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def run_agent(agent: str, days: int) -> dict:
    # Window by EVENT timestamp, not file mtime. A long resumed session has a
    # recent mtime but weeks of history inside it — scanning the whole file
    # re-flags old outputs every run. Keep only events within the window.
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    violations: list[dict] = []
    sessions = _recent(agent, days)
    for path in sessions:
        events = [e for e in _parse(path) if (e.get("ts") or "") >= cutoff_iso]
        for fn in ASSERTIONS:
            for v in fn(events):
                v = dict(v)
                v["session"] = path.stem[:8]
                v["agent"] = agent
                violations.append(v)
    violations.sort(key=lambda v: v.get("ts", ""), reverse=True)
    by_check: dict[str, int] = {}
    for v in violations:
        by_check[v["check"]] = by_check.get(v["check"], 0) + 1
    fails = sum(1 for v in violations if v["sev"] == "fail")
    warns = sum(1 for v in violations if v["sev"] == "warn")
    return {"agent": agent, "sessions": len(sessions), "by_check": by_check,
            "fail": fails, "warn": warns, "violations": violations}


def _agents_with_traj() -> list[str]:
    return sorted(d.name for d in TRAJ_ROOT.iterdir() if d.is_dir())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agents", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    targets = _agents_with_traj() if args.all else args.agents
    if not targets:
        print("Specify agent names or --all", file=sys.stderr)
        return 2

    reports = [run_agent(a, args.days) for a in targets]
    drift = dash_drift(args.days)

    if args.json:
        print(json.dumps({"reports": reports, "drift": drift}, indent=2))
        return 0

    total_fail = sum(r["fail"] for r in reports)
    total_warn = sum(r["warn"] for r in reports)
    for r in reports:
        if not r["violations"]:
            if r["sessions"]:
                print(f"✓ {r['agent']}: clean ({r['sessions']} sessions)")
            continue
        counts = ", ".join(f"{k} ×{n}" for k, n in sorted(r["by_check"].items()))
        print(f"✗ {r['agent']}: {r['fail']} fail, {r['warn']} warn  [{counts}]")
        # up to 2 examples per check so the report stays scannable
        shown: dict[str, int] = {}
        for v in r["violations"]:
            if shown.get(v["check"], 0) >= 2:
                continue
            shown[v["check"]] = shown.get(v["check"], 0) + 1
            mark = "FAIL" if v["sev"] == "fail" else "warn"
            print(f"    [{mark}] {v['check']} ({v['session']}): {v['detail']}")
    print(f"\nTotal: {total_fail} fail, {total_warn} warn across {len(reports)} agents")

    # dash-drift telemetry: caught and stripped before ship, info only.
    if drift["total"]:
        top = sorted(drift["by_agent"].items(), key=lambda kv: kv[1], reverse=True)[:5]
        agents_str = ", ".join(f"{a} ×{n}" for a, n in top)
        surf_str = ", ".join(f"{s} ×{n}" for s, n in
                             sorted(drift["by_surface"].items(), key=lambda kv: kv[1], reverse=True))
        print(f"\nDash-drift (caught + stripped, did NOT ship): {drift['total']} over "
              f"{drift['window_days']}d  | by agent: {agents_str}  | by surface: {surf_str}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
