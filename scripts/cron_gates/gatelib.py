"""Shared helpers for cron gate scripts (the `script:` key in a task file).

Contract (cron_trigger._run): exit != 0 wakes the agent with the output,
empty stdout = nothing to do, a last stdout line of
{"wakeAgent": false, "deliver": "..."} skips the model and posts `deliver`,
anything else is appended to the agent's prompt. Gates are read-only and
fail loud: an unexpected error must exit non-zero, never print nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[2]
VENV_PY = ROOT / ".venv" / "bin" / "python"
USER_AGENT = "PranaAgentOS-cron-gate/1.0"


def load_env() -> None:
    """cron_trigger already exports .env; this makes direct runs behave the same."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - venv always has python-dotenv
        return
    load_dotenv(ROOT / ".env", override=False)


def require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        fail(f"missing env var(s): {', '.join(missing)}")
    return {n: os.environ[n] for n in names}


def fail(msg: str, code: int = 1) -> NoReturn:
    """Exit non-zero so cron_trigger wakes the agent with this message."""
    print(msg, file=sys.stderr)
    sys.exit(code)


def skip(deliver: str | None = None) -> NoReturn:
    """No model call. `deliver` (optional) is posted to the channel as-is."""
    payload: dict = {"wakeAgent": False}
    if deliver:
        payload["deliver"] = deliver
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(0)


def http_get(url: str, headers: dict | None = None, timeout: float = 30):
    """GET only. Returns (status, body_bytes, elapsed_s). Network errors raise."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, method="GET")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.monotonic() - t0


def run(argv: list[str], timeout: float = 90, env: dict | None = None,
        cwd: Path | str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=str(cwd or ROOT))
