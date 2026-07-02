"""File-backed SessionStore adapter for the Claude Agent SDK (>= 0.2.110).

Mirrors every session's transcript JSONL under a local directory so that:
- transcripts survive the CLI's own cleanupPeriodDays sweep,
- resume materializes from the mirror once a session has been mirrored
  (the SDK falls through to the CLI's local transcripts for sessions the
  store has never seen — pre-migration conversations keep resuming fine),
- session list/fork/delete APIs (`list_sessions_from_store` etc.) work
  against a store we own. A later phase can swap this file backend for the
  Agent Nervous System Supabase without touching call sites.

Layout mirrors the CLI's projects dir so humans can grep it:

    <root>/<project_key>/<session_id>.jsonl             main transcript
    <root>/<project_key>/<session_id>/<subpath>.jsonl   subagent transcripts

Contract notes (see SessionStore Protocol in claude_agent_sdk.types):
- append() treats entry ``uuid`` as an idempotency key: retried batches
  don't duplicate lines. Entries without a uuid (titles, tags, markers)
  append as-is.
- load() returns entries in append order, deduped defensively.
- delete() on a main key cascades to the session's subkey directory.
- list_session_summaries is intentionally NOT implemented: the SDK probes
  for method presence and falls back to list_sessions()+load(), which is
  fine at our scale. Do not add a stub that raises — presence == support.

Concurrency: per-file asyncio locks serialise appends within one process.
Cross-process races can't happen for a given session (channel locks in
bot.py serialise conversation turns; cron sessions are their own ids).
File I/O runs in asyncio.to_thread to stay off the event loop's hot path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ntpath
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("session-store")

# Session ids are UUIDs today; accept a slightly wider charset so a future
# CLI id format doesn't silently break mirroring, while still rejecting
# anything path-unsafe.
_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,80}$")
# project_key arrives pre-sanitized from the SDK (sanitized cwd — e.g.
# "-Users-celainc-Documents-Vayu-Vayu", leading dash included), but never
# trust a path component you didn't build. The charset excludes separators,
# so the only traversal-capable values are the literal "." / ".." — rejected
# explicitly in _project_dir.
_PKEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,220}$")

# Bound the per-process caches: at most this many sessions keep their
# seen-uuid set / lock hot. Evicted sessions re-hydrate from disk on the
# next append (rare — eviction only matters for long-lived bot processes).
_MAX_CACHED_SESSIONS = 64


def _safe_subpath(subpath: str, session_dir: Path) -> bool:
    """Mirror the SDK's own subpath safety rules (session_resume.py)."""
    if not subpath or "\x00" in subpath:
        return False
    if Path(subpath).is_absolute() or subpath.startswith(("/", "\\")):
        return False
    if ntpath.splitdrive(subpath)[0]:
        return False
    if any(p in (".", "..") for p in re.split(r"[\\/]", subpath)):
        return False
    target = session_dir / subpath
    try:
        resolved = target.with_name(target.name + ".jsonl").resolve()
        resolved.relative_to(session_dir.resolve())
    except (ValueError, OSError):
        return False
    return True


def _append_text(path: Path, data: str) -> None:
    created = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        f.write(data)
    if created:
        try:
            path.chmod(0o600)
        except OSError:
            pass


class FileSessionStore:
    """Duck-typed SessionStore (the SDK never isinstance-checks)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}
        self._seen: dict[str, set[str]] = {}

    # -- key -> path -------------------------------------------------------

    def _project_dir(self, project_key: str) -> Path | None:
        if project_key in (".", "..") or not _PKEY_RE.match(project_key or ""):
            log.warning("rejecting unsafe project_key: %r", project_key)
            return None
        return self.root / project_key

    def _path(self, key: dict[str, Any]) -> Path | None:
        pdir = self._project_dir(key.get("project_key", ""))
        sid = key.get("session_id", "")
        if pdir is None or not _SID_RE.match(sid):
            if pdir is not None:
                log.warning("rejecting unsafe session_id: %r", sid)
            return None
        sub = key.get("subpath")
        if not sub:
            return pdir / f"{sid}.jsonl"
        session_dir = pdir / sid
        if not _safe_subpath(sub, session_dir):
            log.warning("rejecting unsafe subpath: %r", sub)
            return None
        target = session_dir / sub
        return target.with_name(target.name + ".jsonl")

    def _lock_for(self, k: str) -> asyncio.Lock:
        lock = self._locks.get(k)
        if lock is None:
            if len(self._locks) >= _MAX_CACHED_SESSIONS:
                # FIFO evict; an in-flight append holds its own reference.
                self._locks.pop(next(iter(self._locks)), None)
            lock = asyncio.Lock()
            self._locks[k] = lock
        return lock

    @staticmethod
    def _hydrate_seen(path: Path) -> set[str]:
        seen: set[str] = set()
        if not path.exists():
            return seen
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    u = e.get("uuid") if isinstance(e, dict) else None
                    if u:
                        seen.add(u)
        except OSError as err:
            log.warning("seen-hydrate failed for %s: %s", path, err)
        return seen

    # -- required protocol methods -----------------------------------------

    async def append(self, key: dict[str, Any], entries: list[Any]) -> None:
        path = self._path(key)
        if path is None or not entries:
            return
        k = str(path)
        async with self._lock_for(k):
            seen = self._seen.get(k)
            if seen is None:
                seen = await asyncio.to_thread(self._hydrate_seen, path)
                if len(self._seen) >= _MAX_CACHED_SESSIONS:
                    self._seen.pop(next(iter(self._seen)), None)
                self._seen[k] = seen
            fresh: list[Any] = []
            for e in entries:
                u = e.get("uuid") if isinstance(e, dict) else None
                if u:
                    if u in seen:
                        continue
                    seen.add(u)
                fresh.append(e)
            if not fresh:
                return
            data = "".join(
                json.dumps(e, separators=(",", ":"), ensure_ascii=False) + "\n"
                for e in fresh
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_append_text, path, data)

    async def load(self, key: dict[str, Any]) -> list[Any] | None:
        path = self._path(key)
        if path is None:
            return None

        def _read() -> list[Any] | None:
            if not path.exists():
                return None
            out: list[Any] = []
            seen: set[str] = set()
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        log.warning("skipping corrupt line in %s", path)
                        continue
                    u = e.get("uuid") if isinstance(e, dict) else None
                    if u:
                        if u in seen:
                            continue
                        seen.add(u)
                    out.append(e)
            return out or None

        out = await asyncio.to_thread(_read)
        if out and not key.get("subpath"):
            # Refuse headless tail mirrors (store attached mid-session) so
            # resume falls through to the CLI's complete local transcript.
            # Single implementation lives in session_store_supabase.
            from session_store_supabase import chain_intact

            if not chain_intact(out):
                log.warning(
                    "refusing headless tail mirror %s (%d entries) — resume "
                    "falls through to CLI local transcript",
                    path, len(out),
                )
                return None
        return out

    # -- optional protocol methods ------------------------------------------

    async def list_sessions(self, project_key: str) -> list[dict[str, Any]]:
        pdir = self._project_dir(project_key)

        def _scan() -> list[dict[str, Any]]:
            if pdir is None or not pdir.exists():
                return []
            out = []
            for p in pdir.glob("*.jsonl"):
                try:
                    out.append(
                        {
                            "session_id": p.stem,
                            "mtime": int(p.stat().st_mtime * 1000),
                        }
                    )
                except OSError:
                    continue
            return out

        return await asyncio.to_thread(_scan)

    async def list_subkeys(self, key: dict[str, Any]) -> list[str]:
        pdir = self._project_dir(key.get("project_key", ""))
        sid = key.get("session_id", "")
        if pdir is None or not _SID_RE.match(sid):
            return []
        session_dir = pdir / sid

        def _scan() -> list[str]:
            if not session_dir.is_dir():
                return []
            out = []
            for p in session_dir.rglob("*.jsonl"):
                rel = p.relative_to(session_dir).as_posix()
                out.append(rel[: -len(".jsonl")])
            return out

        return await asyncio.to_thread(_scan)

    async def delete(self, key: dict[str, Any]) -> None:
        path = self._path(key)
        if path is None:
            return
        pdir = self._project_dir(key.get("project_key", ""))
        sid = key.get("session_id", "")
        cascade = (
            pdir / sid if (not key.get("subpath") and pdir is not None) else None
        )

        def _rm() -> None:
            import shutil

            try:
                path.unlink(missing_ok=True)
            except OSError as err:
                log.warning("delete failed for %s: %s", path, err)
            if cascade is not None and cascade.is_dir():
                shutil.rmtree(cascade, ignore_errors=True)

        self._seen.pop(str(path), None)
        await asyncio.to_thread(_rm)
