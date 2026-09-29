"""Gate for qa/api_health_checks: the task's vayu-prana.com probes, GET only.

Primary /api/status (overall + every service "operational"), then the
secondary routes with their expected keys. All pass -> no model call, a
one-line all-green summary is posted (slow notes included: endpoint > 2s,
service responseTime > 500ms). Any failure (non-200, network error, missing
key, non-operational service) -> print the full result table and wake the
agent so it can grade severity and post the report.
"""
from __future__ import annotations

import json

from gatelib import http_get, skip

BASE = "https://vayu-prana.com"
SERVICES = ["Database", "Authentication", "Storage", "API", "Website", "AI Service"]
SLOW_S = 2.0
SLOW_SERVICE_MS = 500


def _positive_count(d) -> bool:
    return isinstance(d.get("count"), (int, float)) and d["count"] > 0


# route -> (expected key description, predicate on the parsed JSON dict)
SECONDARY = [
    ("/api/health", "status", lambda d: "status" in d),
    ("/api/blog?limit=1", "posts (len >= 1)",
     lambda d: isinstance(d.get("posts"), list) and len(d["posts"]) >= 1),
    ("/api/public/facts", "name", lambda d: "name" in d),
    ("/api/public/faq", "count > 0", _positive_count),
    ("/api/public/techniques", "count > 0", _positive_count),
    ("/api/public/comparisons", "count > 0", _positive_count),
    ("/api/public/changelog", "count > 0", _positive_count),
    ("/api/public/openapi.json", 'openapi == "3.0.0"', lambda d: d.get("openapi") == "3.0.0"),
    ("/api/mcp", "name", lambda d: "name" in d),
]


def probe(route: str):
    """-> (http_status or None, elapsed_s, parsed dict or None, error str or None)"""
    try:
        status, body, dt = http_get(BASE + route, timeout=20)
    except Exception as e:  # noqa: BLE001 - network failure is a probe failure
        return None, 0.0, None, f"{type(e).__name__}: {e}"
    try:
        data = json.loads(body)
    except ValueError:
        return status, dt, None, "non-JSON body"
    return status, dt, data if isinstance(data, dict) else None, None


def main() -> None:
    lines, failures, notes = [], [], []

    # Primary probe
    status, dt, d, err = probe("/api/status")
    if err or status != 200 or d is None:
        failures.append(f"/api/status: {err or f'HTTP {status}'} (Sev-1: primary probe failed)")
        lines.append(f"/api/status | {status} | {dt:.2f}s | FAIL {err or ''}")
    else:
        svc = {s.get("name"): s for s in d.get("services") or []}
        bad = [n for n in SERVICES if (svc.get(n) or {}).get("status") != "operational"]
        overall = d.get("overall")
        ok = overall == "operational" and not bad
        lines.append(f"/api/status | 200 | {dt:.2f}s | overall={overall} "
                     f"{'OK' if ok else 'FAIL'}")
        for n in SERVICES:
            s = svc.get(n) or {}
            lines.append(f"  service {n}: {s.get('status', 'MISSING')} "
                         f"({s.get('responseTime', '?')}ms)")
            rt = s.get("responseTime")
            if isinstance(rt, (int, float)) and rt > SLOW_SERVICE_MS:
                notes.append(f"{n} {rt}ms")
        if overall != "operational":
            failures.append(f"/api/status overall={overall!r} (Sev-1)")
        if bad:
            failures.append(f"non-operational/missing services: {', '.join(bad)} (Sev-1)")
        if dt > SLOW_S:
            notes.append(f"/api/status {dt:.1f}s")

    # Secondary probes: the task runs them only if the primary passes.
    if not failures:
        for route, want, pred in SECONDARY:
            status, dt, d, err = probe(route)
            ok = err is None and status == 200 and d is not None and pred(d)
            why = "; ".join(x for x in (f"HTTP {status}" if status else "", err or "") if x)
            lines.append(f"{route} | {status} | {dt:.2f}s | "
                         f"{'OK' if ok else f'FAIL (expected {want}; {why})'}")
            if not ok:
                failures.append(f"{route}: {why or 'key check failed'}; expected {want}")
            elif dt > SLOW_S:
                notes.append(f"{route} {dt:.1f}s")

    if not failures:
        slow = f" Slow: {', '.join(notes)}." if notes else ""
        skip(f"✅ API health: /api/status operational (6/6 services), "
             f"{len(SECONDARY)}/{len(SECONDARY)} public endpoints OK.{slow}")

    print("API health gate: FAILURES detected, full results below.")
    print("Failures:\n" + "\n".join(f"- {f}" for f in failures))
    if notes:
        print("Slow: " + ", ".join(notes))
    print("Results (route | status | time | verdict):\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
