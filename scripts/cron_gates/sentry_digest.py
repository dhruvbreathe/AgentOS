"""Sentry gate: one GET of the project's unresolved issues (24h stats).

Wakes the agent (prints the issue list) when anything in the last 24h is
worth the model's attention:
  - a NEW issue (firstSeen in the window)
  - a REGRESSED or ESCALATING issue with events in the window
  - an issue with >= HOT_EVENTS events in the window, or >= TOTAL_EVENTS
    events across all issues (Android's backend-spike threshold)
  - crash mode (iOS crash triage): ANY event in the window, or new Xcode
    Organizer crash data on disk
Otherwise: no model call, one-line "no new issues" delivered to the channel
(with a note of any low-volume activity on ongoing issues).

API errors / missing env -> non-zero exit, so the agent wakes and reports.
The project slug is hard-coded per wrapper: $SENTRY_PROJECT in .env is the
web project; agents override it in agent.yaml, gates don't see that.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from gatelib import fail, http_get, load_env, require_env, skip

API = "https://sentry.io/api/0"
HOT_EVENTS = 5
TOTAL_EVENTS = 10
SPARK = "▁▂▃▄▅▆▇█"


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _get(path: str, token: str):
    status, body, _ = http_get(API + path, {"Authorization": f"Bearer {token}"}, timeout=30)
    if status != 200:
        fail(f"Sentry API error: HTTP {status} for {path.split('?')[0]}\n"
             f"{body[:300].decode(errors='replace')}")
    try:
        return json.loads(body)
    except ValueError:
        fail(f"Sentry API returned non-JSON for {path.split('?')[0]}: {body[:200]!r}")


def _trend(org: str, project: str, token: str) -> str:
    since = int(time.time()) - 7 * 86400
    pts = _get(f"/projects/{org}/{project}/stats/?stat=received&resolution=1d&since={since}", token)
    counts = [int(c) for _, c in pts][-7:]
    if not counts:
        return ""
    top = max(counts) or 1
    spark = "".join(SPARK[min(len(SPARK) - 1, c * (len(SPARK) - 1) // top)] for c in counts)
    return f" 7d received: {spark} ({sum(counts)} events)."


def _xcode_crash_updates(bundle_dir: Path, cutoff: datetime) -> list[str]:
    if not bundle_dir.is_dir():
        return []
    cut = cutoff.timestamp()
    return [str(p) for p in bundle_dir.rglob("*") if p.is_file() and p.stat().st_mtime >= cut]


def main(project: str, label: str, *, crash_mode: bool = False, trend: bool = False,
         xcode_crash_dir: Path | None = None) -> None:
    load_env()
    env = require_env("SENTRY_AUTH_TOKEN", "SENTRY_ORG")
    token, org = env["SENTRY_AUTH_TOKEN"], env["SENTRY_ORG"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    q = quote("is:unresolved")
    issues = _get(f"/projects/{org}/{project}/issues/?statsPeriod=24h&sort=date"
                  f"&limit=25&query={q}", token)
    if not isinstance(issues, list):
        fail(f"unexpected Sentry response shape: {str(issues)[:200]}")

    rows, reasons = [], []
    total24 = 0
    for i in issues:
        ev24 = sum(int(p[1]) for p in (i.get("stats") or {}).get("24h", []))
        first, last = _ts(i.get("firstSeen")), _ts(i.get("lastSeen"))
        active = ev24 > 0 or (last is not None and last >= cutoff)
        new = first is not None and first >= cutoff
        sub = (i.get("substatus") or "").lower()
        if not (active or new):
            continue
        total24 += ev24
        why = []
        if new:
            why.append("NEW")
        if active and sub in ("regressed", "escalating"):
            why.append(sub.upper())
        if ev24 >= HOT_EVENTS:
            why.append(f"HOT({ev24}/24h)")
        if crash_mode and active:
            why.append("ACTIVE")
        rows.append((i, ev24, why))
        if why:
            reasons.append(f"{i.get('shortId')}: {', '.join(why)}")
    if total24 >= TOTAL_EVENTS:
        reasons.append(f"{total24} events across issues in 24h (>= {TOTAL_EVENTS})")

    xcode_files = _xcode_crash_updates(xcode_crash_dir, cutoff) if xcode_crash_dir else []
    if xcode_files:
        reasons.append(f"{len(xcode_files)} Xcode Organizer crash file(s) updated in 24h")

    if not reasons:
        msg = (f"✅ {label}: no Sentry events or new issues in the last 24h."
               if crash_mode else f"✅ {label} Sentry: no new issues in the last 24h.")
        if rows:
            ids = ", ".join(str(i.get("shortId")) for i, _, _ in rows[:5])
            msg += (f" {total24} event(s) on {len(rows)} ongoing issue(s) ({ids}),"
                    f" below alert threshold.")
        if trend:
            msg += _trend(org, project, token)
        skip(msg)

    out = [f"Sentry gate for `{project}` ({label}), window {cutoff:%Y-%m-%d %H:%MZ} to "
           f"{now:%Y-%m-%d %H:%MZ}. Woken because: " + "; ".join(reasons),
           f"Unresolved issues with activity or first seen in the last 24h "
           f"({len(rows)} of {len(issues)} fetched, {total24} events in 24h):"]
    for i, ev24, why in rows:
        out.append(
            f"- {i.get('shortId')} [{i.get('level')}/{i.get('substatus')}] "
            f"24h={ev24} lifetime={i.get('count')} users={i.get('userCount')} "
            f"first={i.get('firstSeen')} last={i.get('lastSeen')} "
            f"{'<' + ','.join(why) + '> ' if why else ''}"
            f"{(i.get('title') or '')[:120]} | culprit: {(i.get('culprit') or '')[:80]}"
        )
    for p in xcode_files[:10]:
        out.append(f"- Xcode Organizer file updated: {p}")
    out.append("(Gate data only. Run the task's own queries for backlog, trend and details.)")
    print("\n".join(out))
