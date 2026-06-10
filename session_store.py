"""Cross-process-safe persistence for logs/sessions.json.

Three independent writers touch this file (each RelayBot client, the
dashboard's web_chat, and anything else that maps channel -> session id).
Before this module each writer rewrote the WHOLE file from its own stale
in-memory snapshot, so the last writer silently clobbered everyone else's
keys and conversations reset.

Fix: every mutation is a read-merge-write of only the caller's keys,
serialized by an fcntl lock, written atomically (tmp + os.replace) so a
crash mid-write can't truncate the file.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSIONS_FILE = ROOT / "logs" / "sessions.json"
_LOCK_FILE = ROOT / "logs" / "sessions.json.lock"


def _read_unlocked() -> dict[str, str]:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def load() -> dict[str, str]:
    """Snapshot read. Fine for resume lookups; never write this back whole."""
    return _read_unlocked()


def update(updates: dict[str, str | None]) -> dict[str, str]:
    """Merge `updates` into the file under an exclusive lock.

    A value of None deletes the key. Returns the merged mapping.
    """
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOCK_FILE, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            data = _read_unlocked()
            for key, value in updates.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            fd, tmp = tempfile.mkstemp(dir=SESSIONS_FILE.parent, prefix=".sessions-")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, SESSIONS_FILE)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return data
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def set_session(key: str, session_id: str) -> None:
    update({key: session_id})
