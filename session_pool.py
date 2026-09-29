"""Warm Claude sessions for operator turns (2026-09-29; OpenClaw liveSession
"claude-stdio", Hermes gateway _agent_cache).

relay.run_agent spawns a CLI subprocess, connects MCP and reloads the
session on EVERY turn. For channels in `defaults.warm_sessions.agents`, the
operator's turns instead reuse one connected ClaudeSDKClient per channel.

Design constraints:
- One owner task per client. ClaudeSDKClient can't be shared across task
  groups, so every call on it happens inside the owner (turns are submitted
  as jobs), plus one child reader task.
- The reader drains the CLI's message stream continuously into an inbox, so
  nothing stalls between turns (the SDK buffer holds ~100 messages) and any
  stray between-turn output is discarded before the next query instead of
  being mistaken for that turn's reply.
- Only operator turns use it. Routed/heartbeat/flush/cron/task runs keep
  per-turn clients, so the per-turn agent_comms context stays correct.
- A client is recycled when its options fingerprint changes (model, effort,
  prompt, tools), when the channel's session moves on (rotation), after
  `idle_seconds`, and after `max_lifetime_seconds` (store-backed resume
  can't refresh OAuth, so no client lives long). Any error on a warm client
  retires it; relay then falls back to a per-turn client.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Awaitable, Callable

from claude_agent_sdk import ClaudeSDKClient, ResultMessage

log = logging.getLogger("session-pool")


def fingerprint(options: Any) -> str:
    """Stable hash of what a live CLI process was started with."""
    sp = options.system_prompt
    sp_text = sp.get("prompt") if isinstance(sp, dict) else sp
    parts = {
        "model": options.model,
        "fallback": options.fallback_model,
        "effort": options.effort,
        "sp": hashlib.sha256((sp_text or "").encode()).hexdigest(),
        "mcp": sorted((options.mcp_servers or {}).keys()) if isinstance(options.mcp_servers, dict) else [],
        "tools": list(options.allowed_tools or []),
        "skills": options.skills,
        "cwd": str(options.cwd),
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


class _Adapter:
    """What relay's turn body sees: the per-turn client's interface, but
    replies come from the reader's inbox."""

    def __init__(self, client: ClaudeSDKClient, inbox: asyncio.Queue) -> None:
        self._client = client
        self._inbox = inbox

    async def query(self, prompt: str) -> None:
        await self._client.query(prompt)

    async def receive_response(self):
        while True:
            msg = await self._inbox.get()
            if isinstance(msg, BaseException):
                raise RuntimeError(f"warm session reader died: {msg}")
            yield msg
            if isinstance(msg, ResultMessage):
                return

    async def get_context_usage(self):
        return await self._client.get_context_usage()


class WarmClient:
    def __init__(self, key: str, fp: str, options: Any, idle_s: float, max_life_s: float) -> None:
        self.key, self.fp, self.options = key, fp, options
        self.idle_s, self.max_life_s = idle_s, max_life_s
        self.session_id: str | None = options.resume
        self.born = time.monotonic()
        self.dead = False
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._start_error: BaseException | None = None

    def expired(self) -> bool:
        return time.monotonic() - self.born > self.max_life_s

    async def start(self) -> None:
        self._task = asyncio.create_task(self._owner(), name=f"warm-{self.key}")
        await self._ready.wait()
        if self._start_error is not None:
            raise self._start_error

    async def _owner(self) -> None:
        inbox: asyncio.Queue = asyncio.Queue()
        reader: asyncio.Task | None = None
        try:
            async with ClaudeSDKClient(options=self.options) as client:
                async def _read() -> None:
                    try:
                        async for m in client.receive_messages():
                            await inbox.put(m)
                    except BaseException as e:  # surface to whoever is waiting
                        await inbox.put(e if isinstance(e, Exception) else RuntimeError("reader cancelled"))
                        raise

                reader = asyncio.create_task(_read())
                self._ready.set()
                adapter = _Adapter(client, inbox)
                while not self.expired():
                    try:
                        job = await asyncio.wait_for(self._jobs.get(), timeout=self.idle_s)
                    except asyncio.TimeoutError:
                        log.info("[%s] warm session idle %.0fs: closing", self.key, self.idle_s)
                        break
                    if job is None:
                        break
                    fn, fut = job
                    stale = 0
                    while not inbox.empty():  # between-turn output is not this turn's
                        inbox.get_nowait()
                        stale += 1
                    if stale:
                        log.info("[%s] discarded %d between-turn message(s)", self.key, stale)
                    try:
                        result = await fn(adapter)
                        if not fut.done():
                            fut.set_result(result)
                    except BaseException as e:
                        if not fut.done():
                            fut.set_exception(e)
                        log.warning("[%s] warm turn failed, retiring client: %s", self.key, e)
                        break
        except BaseException as e:  # noqa: BLE001 — start failures go to start()
            if not self._ready.is_set():
                self._start_error = e
            else:
                log.warning("[%s] warm session ended: %s", self.key, e)
        finally:
            self.dead = True
            if reader is not None:
                reader.cancel()
            self._ready.set()
            while not self._jobs.empty():  # never strand a waiter
                job = self._jobs.get_nowait()
                if job and not job[1].done():
                    job[1].set_exception(RuntimeError("warm session closed"))

    async def run(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        if self.dead:
            raise RuntimeError("warm session closed")
        fut = asyncio.get_running_loop().create_future()
        await self._jobs.put((fn, fut))
        return await fut

    async def close(self) -> None:
        if self._task and not self._task.done():
            await self._jobs.put(None)
            try:
                await asyncio.wait_for(self._task, timeout=15)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()


class WarmPool:
    def __init__(self) -> None:
        self._clients: dict[str, WarmClient] = {}
        self._ctx: dict[str, dict] = {}

    def ctx_for(self, key: str) -> dict:
        """Mutable per-channel holder shared with the warm session's
        agent_comms server (relay updates the batch id every turn)."""
        return self._ctx.setdefault(key, {})

    def _cfg(self) -> dict:
        try:
            from agent_loader import load_global
            return (load_global().get("defaults", {}) or {}).get("warm_sessions") or {}
        except Exception:
            return {}

    def enabled_for(self, agent_name: str) -> bool:
        cfg = self._cfg()
        return bool(cfg.get("enabled")) and agent_name in (cfg.get("agents") or [])

    async def run(self, key: str, options: Any, fn: Callable[[Any], Awaitable[Any]],
                  timing: dict | None = None) -> tuple[Any, bool]:
        """Run `fn(adapter)` on the channel's warm client. Returns (result,
        reused) where reused says whether the process was already warm;
        also written to timing["warm"] before the turn starts."""
        cfg = self._cfg()
        fp = fingerprint(options)
        wc = self._clients.get(key)
        if wc is not None and (
            wc.dead or wc.expired() or wc.fp != fp
            or (wc.session_id and options.resume != wc.session_id)
        ):
            await wc.close()
            wc = None
        reused = wc is not None
        if timing is not None:
            timing["warm"] = reused
        if wc is None:
            wc = WarmClient(key, fp, options,
                            idle_s=float(cfg.get("idle_seconds", 900)),
                            max_life_s=float(cfg.get("max_lifetime_seconds", 1800)))
            await wc.start()
            self._clients[key] = wc
        result = await wc.run(fn)
        # The turn reports the session it ran in; the next turn must match.
        if isinstance(result, tuple) and len(result) == 2 and result[1]:
            wc.session_id = result[1]
        return result, reused

    async def evict(self, key: str) -> None:
        wc = self._clients.pop(key, None)
        if wc is not None:
            await wc.close()


POOL = WarmPool()
