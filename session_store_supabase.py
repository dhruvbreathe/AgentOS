"""Supabase-backed SessionStore adapter (Agent Nervous System project).

Same duck-typed protocol as session_store_file.FileSessionStore, backed by
PostgREST on the Agent NS Supabase (schema=agents, table=session_store).
One row per transcript JSONL entry:

    (project_key, session_id, subpath, dedup_key, entry jsonb, id identity)

- dedup_key is the entry's ``uuid`` when present (idempotent appends across
  retries AND process restarts, enforced by a DB unique index +
  ``Prefer: resolution=ignore-duplicates``). Entries without a uuid get a
  client-synthesized uuid4 so they always insert — mirroring the file
  store's append-as-is semantics for titles/tags/markers.
- load() streams rows in ``id`` order (keyset pagination, PostgREST's
  max-rows cap never truncates us) and returns None on any failure so the
  SDK falls through to the CLI's local transcripts — Supabase being down
  degrades to the pre-mirror world, never to a broken resume.
- Gap safety: if an append batch is lost after a retry, the session is
  marked tainted, mirroring for it stops, and its mirrored rows are
  best-effort deleted so a HOLE-y transcript can never win over the CLI's
  complete local copy at resume time. Tainted sessions re-attempt the
  purge (not the append) on subsequent flushes.
- Circuit breaker: 3 consecutive transport failures disable the store for
  a cooldown so a Supabase outage costs one warning, not a per-batch
  timeout tax on every turn.

Env (loaded by bot.py/cron_trigger.py via dotenv):
    SUPABASE_AGENT_NS_URL                bare project URL or .../rest/v1
    SUPABASE_AGENT_NS_SERVICE_ROLE_KEY   service role (RLS: anon revoked)
    SUPABASE_AGENT_NS_SCHEMA             defaults to "agents"
"""
from __future__ import annotations

import asyncio
import logging
import ntpath
import os
import re
import time
import uuid as uuid_mod
from typing import Any

import httpx

log = logging.getLogger("session-store")

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{5,80}$")
_PKEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,220}$")

_TABLE = "session_store"
_SESSIONS_VIEW = "session_store_sessions"
_SUBKEYS_VIEW = "session_store_subkeys"
_PAGE = 200
_MAX_CACHED_SESSIONS = 64
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 120.0
_TIMEOUT = httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)


def _safe_subpath(subpath: str) -> bool:
    """SDK subpath rules, minus filesystem resolution (no fs here)."""
    if not subpath or "\x00" in subpath:
        return False
    if subpath.startswith(("/", "\\")):
        return False
    if ntpath.splitdrive(subpath)[0]:
        return False
    if any(p in (".", "..") for p in re.split(r"[\\/]", subpath)):
        return False
    return len(subpath) <= 512


def chain_intact(entries: list[Any]) -> bool:
    """True if the transcript's message chain starts at a real head.

    CLI transcripts are parent-linked: every user/assistant entry carries a
    ``parentUuid``; the true head of a session (or a post-compact summary
    boundary) has ``parentUuid: null``. A mirror that attached to an ALREADY
    RUNNING session captures only the tail — its first message entry points
    at a parent that isn't in the mirror. Serving that tail to the SDK's
    resume materializer would silently amnesia everything before the attach
    point, shadowing the CLI's complete local transcript. Refusing it makes
    resume fall through to local instead (store-miss semantics).

    Rules: first message entry must have a null parentUuid, and every
    non-null parentUuid must resolve within the mirror (no holes). Entries
    without type user/assistant (queue-operation, mode, attachment, ...)
    don't participate. Synthetic/foreign entries with no parent field pass.
    """
    uuids = {
        e.get("uuid")
        for e in entries
        if isinstance(e, dict) and e.get("uuid")
    }
    saw_head = False
    for e in entries:
        if not isinstance(e, dict) or e.get("type") not in ("user", "assistant"):
            continue
        parent = e.get("parentUuid")
        if not saw_head:
            if parent is not None:
                return False  # headless tail
            saw_head = True
            continue
        if parent is not None and parent not in uuids:
            return False  # hole mid-chain
    return True


def from_env() -> "SupabaseSessionStore | None":
    """Build a store from SUPABASE_AGENT_NS_* env, or None if not wired."""
    url = (os.environ.get("SUPABASE_AGENT_NS_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_AGENT_NS_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        return None
    schema = (os.environ.get("SUPABASE_AGENT_NS_SCHEMA") or "agents").strip()
    return SupabaseSessionStore(url, key, schema=schema)


class SupabaseSessionStore:
    """Duck-typed SessionStore (the SDK never isinstance-checks)."""

    def __init__(self, url: str, service_key: str, schema: str = "agents"):
        url = url.rstrip("/")
        self._rest = url if url.endswith("/rest/v1") else url + "/rest/v1"
        self._key = service_key
        self._schema = schema
        self._client: httpx.AsyncClient | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._seen: dict[str, set[str]] = {}
        self._tainted: set[str] = set()
        self._fail_count = 0
        self._disabled_until = 0.0
        self._breaker_logged = False

    # -- plumbing ------------------------------------------------------------

    def _headers(self, write: bool = False) -> dict[str, str]:
        h = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "User-Agent": "VayuAgentOS/1.0 (+https://vayu-prana.com)",
            "Accept-Profile": self._schema,
        }
        if write:
            h["Content-Profile"] = self._schema
        return h

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    def _breaker_open(self) -> bool:
        if time.monotonic() < self._disabled_until:
            return True
        return False

    def _note_failure(self, what: str, err: Exception) -> None:
        self._fail_count += 1
        if self._fail_count >= _BREAKER_THRESHOLD:
            self._disabled_until = time.monotonic() + _BREAKER_COOLDOWN_S
            self._fail_count = 0
            if not self._breaker_logged:
                log.warning(
                    "supabase session store: circuit open for %ss after %s: %s",
                    int(_BREAKER_COOLDOWN_S), what, err,
                )
                self._breaker_logged = True
        else:
            log.warning("supabase session store %s failed: %s", what, err)

    def _note_success(self) -> None:
        self._fail_count = 0
        self._breaker_logged = False

    @staticmethod
    def _norm_key(key: dict[str, Any]) -> tuple[str, str, str] | None:
        pkey = key.get("project_key", "") or ""
        sid = key.get("session_id", "") or ""
        sub = key.get("subpath") or ""
        if pkey in (".", "..") or not _PKEY_RE.match(pkey):
            log.warning("rejecting unsafe project_key: %r", pkey)
            return None
        if not _SID_RE.match(sid):
            log.warning("rejecting unsafe session_id: %r", sid)
            return None
        if sub and not _safe_subpath(sub):
            log.warning("rejecting unsafe subpath: %r", sub)
            return None
        return pkey, sid, sub

    def _lock_for(self, k: str) -> asyncio.Lock:
        lock = self._locks.get(k)
        if lock is None:
            if len(self._locks) >= _MAX_CACHED_SESSIONS:
                self._locks.pop(next(iter(self._locks)), None)
            lock = asyncio.Lock()
            self._locks[k] = lock
        return lock

    async def _purge(self, pkey: str, sid: str, sub: str) -> bool:
        """Delete mirrored rows for a key. True on success."""
        params: dict[str, str] = {
            "project_key": f"eq.{pkey}",
            "session_id": f"eq.{sid}",
        }
        if sub:
            params["subpath"] = f"eq.{sub}"
        r = await self._http().delete(
            f"{self._rest}/{_TABLE}", params=params, headers=self._headers(write=True)
        )
        r.raise_for_status()
        return True

    # -- required protocol methods --------------------------------------------

    async def append(self, key: dict[str, Any], entries: list[Any]) -> None:
        norm = self._norm_key(key)
        if norm is None or not entries or self._breaker_open():
            return
        pkey, sid, sub = norm
        k = f"{pkey}/{sid}/{sub}"
        async with self._lock_for(k):
            if k in self._tainted:
                # Don't extend a holey mirror; try to purge it instead so
                # resume falls through to the CLI's complete local copy.
                try:
                    await self._purge(pkey, sid, sub)
                    self._tainted.discard(k)
                    self._seen.pop(k, None)
                    log.info("purged tainted mirror for %s", k)
                except Exception as err:  # noqa: BLE001
                    self._note_failure("taint-purge", err)
                return
            seen = self._seen.get(k)
            if seen is None:
                seen = set()
                if len(self._seen) >= _MAX_CACHED_SESSIONS:
                    self._seen.pop(next(iter(self._seen)), None)
                self._seen[k] = seen
            rows: list[dict[str, Any]] = []
            fresh_uuids: list[str] = []
            for e in entries:
                u = e.get("uuid") if isinstance(e, dict) else None
                if u:
                    if u in seen:
                        continue
                    fresh_uuids.append(u)
                rows.append(
                    {
                        "project_key": pkey,
                        "session_id": sid,
                        "subpath": sub,
                        "dedup_key": u or uuid_mod.uuid4().hex,
                        "entry": e,
                    }
                )
            if not rows:
                return
            try:
                r = await self._http().post(
                    f"{self._rest}/{_TABLE}",
                    params={"on_conflict": "project_key,session_id,subpath,dedup_key"},
                    json=rows,
                    headers={
                        **self._headers(write=True),
                        "Prefer": "resolution=ignore-duplicates,return=minimal",
                    },
                )
                r.raise_for_status()
            except Exception as err:  # noqa: BLE001
                # One retry; POSTs are idempotent thanks to dedup_key.
                try:
                    r = await self._http().post(
                        f"{self._rest}/{_TABLE}",
                        params={
                            "on_conflict": "project_key,session_id,subpath,dedup_key"
                        },
                        json=rows,
                        headers={
                            **self._headers(write=True),
                            "Prefer": "resolution=ignore-duplicates,return=minimal",
                        },
                    )
                    r.raise_for_status()
                except Exception as err2:  # noqa: BLE001
                    self._tainted.add(k)
                    self._note_failure("append", err2)
                    log.warning(
                        "mirror for %s marked tainted (dropped batch of %d)",
                        k, len(rows),
                    )
                    return
            seen.update(fresh_uuids)
            self._note_success()

    async def load(self, key: dict[str, Any]) -> list[Any] | None:
        norm = self._norm_key(key)
        if norm is None or self._breaker_open():
            return None
        pkey, sid, sub = norm
        out: list[Any] = []
        dedup: set[str] = set()
        last_id = 0
        try:
            while True:
                r = await self._http().get(
                    f"{self._rest}/{_TABLE}",
                    params={
                        "select": "id,dedup_key,entry",
                        "project_key": f"eq.{pkey}",
                        "session_id": f"eq.{sid}",
                        "subpath": f"eq.{sub}",
                        "id": f"gt.{last_id}",
                        "order": "id.asc",
                        "limit": str(_PAGE),
                    },
                    headers=self._headers(),
                )
                r.raise_for_status()
                page = r.json()
                if not page:
                    break
                for row in page:
                    last_id = row["id"]
                    dk = row.get("dedup_key")
                    if dk:
                        if dk in dedup:
                            continue
                        dedup.add(dk)
                    out.append(row["entry"])
                if len(page) < _PAGE:
                    break
        except Exception as err:  # noqa: BLE001
            self._note_failure("load", err)
            return None  # SDK falls through to CLI local transcripts
        self._note_success()
        if out and not sub and not chain_intact(out):
            log.warning(
                "refusing headless tail mirror for %s/%s (%d entries) — "
                "resume falls through to CLI local transcript",
                pkey, sid, len(out),
            )
            return None
        return out or None

    # -- optional protocol methods ---------------------------------------------

    async def list_sessions(self, project_key: str) -> list[dict[str, Any]]:
        if (
            project_key in (".", "..")
            or not _PKEY_RE.match(project_key or "")
            or self._breaker_open()
        ):
            return []
        try:
            r = await self._http().get(
                f"{self._rest}/{_SESSIONS_VIEW}",
                params={
                    "select": "session_id,mtime",
                    "project_key": f"eq.{project_key}",
                    "limit": "1000",
                },
                headers=self._headers(),
            )
            r.raise_for_status()
            self._note_success()
            return [
                {"session_id": row["session_id"], "mtime": int(row["mtime"])}
                for row in r.json()
            ]
        except Exception as err:  # noqa: BLE001
            self._note_failure("list_sessions", err)
            return []

    async def list_subkeys(self, key: dict[str, Any]) -> list[str]:
        norm = self._norm_key({**key, "subpath": None})
        if norm is None or self._breaker_open():
            return []
        pkey, sid, _ = norm
        try:
            r = await self._http().get(
                f"{self._rest}/{_SUBKEYS_VIEW}",
                params={
                    "select": "subpath",
                    "project_key": f"eq.{pkey}",
                    "session_id": f"eq.{sid}",
                    "limit": "1000",
                },
                headers=self._headers(),
            )
            r.raise_for_status()
            self._note_success()
            return [row["subpath"] for row in r.json() if row.get("subpath")]
        except Exception as err:  # noqa: BLE001
            self._note_failure("list_subkeys", err)
            return []

    async def delete(self, key: dict[str, Any]) -> None:
        norm = self._norm_key(key)
        if norm is None:
            return
        pkey, sid, sub = norm
        # Main key (no subpath) cascades to every subpath row for the session,
        # matching the file store's directory cascade — evict caches to match.
        if sub:
            self._seen.pop(f"{pkey}/{sid}/{sub}", None)
            self._tainted.discard(f"{pkey}/{sid}/{sub}")
        else:
            prefix = f"{pkey}/{sid}/"
            for cache_key in [x for x in self._seen if x.startswith(prefix)]:
                self._seen.pop(cache_key, None)
            self._tainted = {x for x in self._tainted if not x.startswith(prefix)}
        try:
            await self._purge(pkey, sid, sub)
            self._note_success()
        except Exception as err:  # noqa: BLE001
            self._note_failure("delete", err)
