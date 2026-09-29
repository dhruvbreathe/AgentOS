"""Subscription quota breaker (2026-09-29).

Patterns from Hermes (credential_pool: park an exhausted credential until the
provider's reset time, 1h TTL fallback) and OpenClaw (model-failover: brown
out background work before the hard stop), adapted to ONE subscription
account, since the fleet is subscription-only by operator policy.

The CLI emits a RateLimitEvent whenever a window's status changes. relay.py
records each one here (logs/quota.json, shared by bot.py and every cron
process) and three policies read it:

- background_allowed(): heartbeats and non-critical crons stand down when a
  window is at allowed_warning past `warn_utilization`, or rejected.
- model_for(model): when only the weekly Opus window is rejected, Opus runs
  move to the fallback model until it resets. This is the one real fallback
  inside one account; --fallback-model only fires on 529 overloads.
- blocked(): a five_hour / seven_day window is rejected. Operator turns get
  an immediate "paused until" reply and are queued for replay.

Fail-open everywhere: unreadable state, or a reading past its resets_at,
means allowed. The breaker must never be the thing that stops the fleet.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("quota")

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "logs" / "quota.json"
_LOCK_FILE = ROOT / "logs" / "quota.json.lock"
DEFERRED_CRONS = ROOT / "logs" / "deferred_crons.jsonl"

# Windows that stop every model when rejected.
_HARD_WINDOWS = ("five_hour", "seven_day")
# Readings with no resets_at expire after this long (Hermes' 429 TTL).
_NO_RESET_TTL_S = 3600
_DEFAULT_WARN_UTILIZATION = 0.85


def _cfg() -> dict[str, Any]:
    try:
        from agent_loader import load_global

        d = load_global().get("defaults", {}) or {}
        return {**(d.get("quota") or {}), "_fallback": d.get("fallback_model")}
    except Exception:
        return {}


def _read() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _update(mutate) -> None:
    """Read-modify-write under an exclusive lock, atomic replace."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOCK_FILE, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            data = _read()
            mutate(data)
            fd, tmp = tempfile.mkstemp(dir=STATE_FILE.parent, prefix=".quota-")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, STATE_FILE)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _expires_at(w: dict[str, Any]) -> float:
    return float(w.get("resets_at") or (w.get("updated", 0) + _NO_RESET_TTL_S))


def _live_windows(now: float | None = None) -> dict[str, dict[str, Any]]:
    """Windows whose reading hasn't expired yet."""
    now = now or time.time()
    return {
        k: w
        for k, w in (_read().get("windows") or {}).items()
        if _expires_at(w) > now
    }


# ---- recording (relay.py) ---------------------------------------------

def record_event(info: Any) -> None:
    """Persist a RateLimitInfo. Logs status transitions once."""
    try:
        kind = getattr(info, "rate_limit_type", None) or "unknown"
        status = getattr(info, "status", None) or "allowed"
        reading = {
            "status": status,
            "utilization": getattr(info, "utilization", None),
            "resets_at": getattr(info, "resets_at", None),
            "updated": time.time(),
        }
        prev: dict[str, Any] = {}

        def _m(data: dict) -> None:
            windows = data.setdefault("windows", {})
            prev.update(windows.get(kind) or {})
            windows[kind] = reading

        _update(_m)
        if prev.get("status") != status:
            log.warning(
                "quota %s: %s -> %s (utilization=%s, resets_at=%s)",
                kind, prev.get("status", "unknown"), status,
                reading["utilization"], _fmt(reading["resets_at"]),
            )
    except Exception as e:  # noqa: BLE001 — telemetry must never break a turn
        log.debug("quota record_event skipped: %s", e)


def record_error(kind: str, detail: str = "") -> None:
    """A rate_limit / billing error with no window info: park 'unknown' as
    rejected for the TTL so background work backs off, then re-probe."""
    if kind not in ("rate_limit", "billing_error"):
        return
    try:
        def _m(data: dict) -> None:
            data.setdefault("windows", {})["unknown"] = {
                "status": "rejected",
                "utilization": None,
                "resets_at": None,
                "updated": time.time(),
                "detail": detail[:200],
            }

        _update(_m)
        log.warning("quota error (%s): %s", kind, detail[:200])
    except Exception as e:  # noqa: BLE001
        log.debug("quota record_error skipped: %s", e)


# ---- policies -----------------------------------------------------------

def blocked() -> tuple[bool, str | None, float | None]:
    """(True, window, resets_at) when every model is out of quota."""
    try:
        live = _live_windows()
        for k in (*_HARD_WINDOWS, "unknown"):
            w = live.get(k)
            if w and w.get("status") == "rejected":
                return True, k, _expires_at(w)
    except Exception:
        pass
    return False, None, None


def background_allowed() -> tuple[bool, str]:
    """(allowed, reason). Heartbeats / non-critical crons check this."""
    try:
        warn = float(_cfg().get("warn_utilization", _DEFAULT_WARN_UTILIZATION))
        for k, w in _live_windows().items():
            status, util = w.get("status"), w.get("utilization")
            if status == "rejected":
                return False, f"{k} rejected until {_fmt(_expires_at(w))}"
            if status == "allowed_warning" and (util is None or util >= warn):
                pct = f"{util:.0%}" if util is not None else "warning"
                return False, f"{k} at {pct} (resets {_fmt(_expires_at(w))})"
    except Exception:
        pass
    return True, ""


def model_for(model: str | None) -> str | None:
    """Swap Opus for the fallback while only the weekly Opus window is out."""
    try:
        if not model or "opus" not in model.lower():
            return model
        w = _live_windows().get("seven_day_opus")
        if w and w.get("status") == "rejected":
            fb = _cfg().get("opus_fallback") or _cfg().get("_fallback")
            if fb and "opus" not in fb.lower():
                return fb
    except Exception:
        pass
    return model


def summary() -> dict[str, Any]:
    """For doctor / dashboard."""
    b, window, until = blocked()
    ok, reason = background_allowed()
    return {
        "blocked": b,
        "blocked_window": window,
        "blocked_until": _fmt(until),
        "background_allowed": ok,
        "background_reason": reason,
        "windows": _read().get("windows") or {},
    }


def _fmt(ts: float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%a %H:%M", time.localtime(float(ts)))


# ---- deferred crons -------------------------------------------------------

def defer_cron(agent: str, task: str, reason: str) -> None:
    """Record a cron that stood down so bot.py can replay it after reset."""
    DEFERRED_CRONS.parent.mkdir(parents=True, exist_ok=True)
    with DEFERRED_CRONS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "agent": agent, "task": task, "reason": reason,
            "deferred_at": time.time(),
        }) + "\n")


def take_deferred_crons(max_age_s: float = 24 * 3600) -> list[tuple[str, str]]:
    """Drain the deferred queue: unique (agent, task) pairs newer than
    max_age_s. Stale entries (a daily digest from 2 days ago) are dropped."""
    if not DEFERRED_CRONS.exists():
        return []
    with open(_LOCK_FILE, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            lines = DEFERRED_CRONS.read_text().splitlines()
            DEFERRED_CRONS.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
    now, seen, out = time.time(), set(), []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        key = (d.get("agent"), d.get("task"))
        if None in key or key in seen or now - d.get("deferred_at", 0) > max_age_s:
            continue
        seen.add(key)
        out.append(key)
    return out
