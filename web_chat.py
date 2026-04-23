"""web_chat.py — human-to-agent chat over HTTP, mounted into dashboard.py.

Architecture:
  Browser <-- SSE -- FastAPI /api/chat/<agent>/stream
                        |
                        v
                  run_agent(agent, prompt, WebSink, resume_session_id)
                        |                                         |
                        +----> trajectory JSONL                    +----> Claude SDK

WebSink pushes incremental updates into an asyncio.Queue which the SSE
endpoint drains. Per-agent locks prevent two simultaneous turns from
racing the same session.

Session key: "web:<agent>:<thread_id>" — separate from Discord's channel-id
keyed session. Each web chat can spawn multiple concurrent threads with
the same agent (tabs in the UI); each thread gets its own Claude session,
history file, and lock. Shared vault, memory files, identity — only the
conversation state is split.
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from agent_loader import load_all_agents
from relay import Sink, run_agent

ROOT = Path(__file__).resolve().parent
SESSIONS_FILE = ROOT / "logs" / "sessions.json"
CHAT_HISTORY_DIR = ROOT / "logs" / "web_chat"
CHAT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def esc(s) -> str:
    return html_lib.escape(str(s or ""))


# ---- session persistence ---------------------------------------------------

def _load_sessions() -> dict[str, str]:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_sessions(data: dict[str, str]) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(data, indent=2))


DEFAULT_THREAD = "default"


def _session_key(agent: str, thread_id: str = DEFAULT_THREAD) -> str:
    return f"web:{agent}:{thread_id}"


# ---- threads (multiple parallel conversations per agent) -------------------
#
# Storage layout:
#   logs/web_chat/<agent>/<thread_id>.jsonl    ← chat history per thread
#   logs/web_chat/<agent>/_threads.json        ← metadata (title, timestamps)
#   logs/web_chat/<agent>.jsonl                ← LEGACY flat per-agent file;
#                                                migrated into default thread
#                                                on first read.

def _agent_dir(agent: str) -> Path:
    d = CHAT_HISTORY_DIR / agent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _threads_file(agent: str) -> Path:
    return _agent_dir(agent) / "_threads.json"


def _thread_path(agent: str, thread_id: str) -> Path:
    # Guard against path traversal — allow only safe ids.
    safe = "".join(c for c in thread_id if c.isalnum() or c in "-_") or DEFAULT_THREAD
    return _agent_dir(agent) / f"{safe}.jsonl"


def _legacy_history_path(agent: str) -> Path:
    return CHAT_HISTORY_DIR / f"{agent}.jsonl"


def _load_threads(agent: str) -> list[dict]:
    # Migrate legacy flat file on first touch so old chats aren't orphaned.
    legacy = _legacy_history_path(agent)
    if legacy.exists() and legacy.is_file():
        dest = _thread_path(agent, DEFAULT_THREAD)
        if not dest.exists():
            dest.write_bytes(legacy.read_bytes())
        legacy.unlink()

    p = _threads_file(agent)
    threads: list[dict] = []
    if p.exists():
        try:
            threads = json.loads(p.read_text()) or []
        except json.JSONDecodeError:
            threads = []

    # Ensure every on-disk jsonl is represented (and a default exists).
    known_ids = {t.get("id") for t in threads}
    for f in _agent_dir(agent).glob("*.jsonl"):
        tid = f.stem
        if tid not in known_ids:
            threads.append({
                "id": tid,
                "title": "Default" if tid == DEFAULT_THREAD else tid,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "last_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            known_ids.add(tid)

    if not threads:
        threads = [{
            "id": DEFAULT_THREAD,
            "title": "Default",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }]
    return threads


def _save_threads(agent: str, threads: list[dict]) -> None:
    _threads_file(agent).write_text(json.dumps(threads, indent=2))


def _touch_thread(agent: str, thread_id: str) -> None:
    threads = _load_threads(agent)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    found = False
    for t in threads:
        if t["id"] == thread_id:
            t["last_at"] = now
            found = True
            break
    if not found:
        threads.append({"id": thread_id, "title": thread_id,
                        "created_at": now, "last_at": now})
    _save_threads(agent, threads)


def _create_thread(agent: str, title: str | None = None) -> dict:
    threads = _load_threads(agent)
    tid = uuid.uuid4().hex[:10]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {
        "id": tid,
        "title": title or f"Thread {len(threads) + 1}",
        "created_at": now,
        "last_at": now,
    }
    threads.append(entry)
    _save_threads(agent, threads)
    # Create empty file so the thread is persisted even before first message.
    _thread_path(agent, tid).touch()
    return entry


def _delete_thread(agent: str, thread_id: str) -> bool:
    threads = _load_threads(agent)
    remaining = [t for t in threads if t["id"] != thread_id]
    if len(remaining) == len(threads):
        return False
    _save_threads(agent, remaining)
    p = _thread_path(agent, thread_id)
    if p.exists():
        p.unlink()
    # Drop session mapping too.
    sessions = _load_sessions()
    sessions.pop(_session_key(agent, thread_id), None)
    _save_sessions(sessions)
    return True


def _rename_thread(agent: str, thread_id: str, title: str) -> bool:
    threads = _load_threads(agent)
    for t in threads:
        if t["id"] == thread_id:
            t["title"] = title[:120]
            _save_threads(agent, threads)
            return True
    return False


# ---- chat history (operator-visible, survives restarts) --------------------

def _history_path(agent: str, thread_id: str = DEFAULT_THREAD) -> Path:
    return _thread_path(agent, thread_id)


def _append_history(agent: str, role: str, content: str,
                    meta: dict | None = None,
                    thread_id: str = DEFAULT_THREAD) -> None:
    p = _history_path(agent, thread_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "role": role,
        "content": content,
    }
    if meta:
        rec["meta"] = meta
    with p.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    _touch_thread(agent, thread_id)
    # Fire a live event for browser subscribers. `role` drives `from`:
    # operator prompts and assistant replies both surface as activity in
    # this agent's thread, but only non-operator messages count as unread.
    try:
        from events import publish as publish_event
        # Who sent it? user = operator; assistant = this agent's own reply;
        # routed = another agent (meta["from"] is authoritative).
        if role == "user":
            sender = "operator"
        elif role == "routed":
            sender = (meta or {}).get("from") or "unknown"
        else:
            sender = agent  # assistant replying to itself's channel
        publish_event({
            "type": role,
            "agent": agent,
            "thread": thread_id,
            "from": sender,
            "preview": content[:140],
        })
    except Exception:
        # Event bus is best-effort; never break history writes over it.
        pass


def _read_history(agent: str, limit: int = 200,
                  thread_id: str = DEFAULT_THREAD) -> list[dict]:
    p = _history_path(agent, thread_id)
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open() as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out[-limit:]


def _clear_history(agent: str, thread_id: str = DEFAULT_THREAD) -> int:
    p = _history_path(agent, thread_id)
    if not p.exists():
        return 0
    n = sum(1 for _ in p.open())
    p.unlink()
    # Re-create empty so the thread still exists.
    p.touch()
    return n


def _history_line_count(agent: str, thread_id: str = DEFAULT_THREAD) -> int:
    p = _history_path(agent, thread_id)
    if not p.exists():
        return 0
    return sum(1 for _ in p.open())


# ---- SSE sink --------------------------------------------------------------

class WebSink(Sink):
    """A Sink that pushes {type, ...} events into an asyncio.Queue. The
    SSE endpoint drains the queue and formats each event as SSE `data:` lines."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.last_text = ""
        self._closed = False

    async def update(self, text: str) -> None:
        self.last_text = text
        await self.queue.put({"type": "update", "text": text})

    async def finalize(self, text: str) -> None:
        self.last_text = text
        await self.queue.put({"type": "final", "text": text})
        self._closed = True
        await self.queue.put({"type": "_done"})

    async def error(self, message: str) -> None:
        await self.queue.put({"type": "error", "message": message})
        self._closed = True
        await self.queue.put({"type": "_done"})

    async def tool_event(self, ev: dict) -> None:
        """Optional channel for tool-use/tool-result signals. Currently
        not called by run_agent (which only uses update/finalize), but
        TrajectoryLogger wraps them — future enhancement."""
        await self.queue.put({"type": "tool", **ev})


# ---- per-thread locks (one turn at a time per (agent, thread)) -------------

_locks: dict[str, asyncio.Lock] = {}


def _lock(agent: str, thread_id: str = DEFAULT_THREAD) -> asyncio.Lock:
    key = f"{agent}:{thread_id}"
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


# ---- background runner -----------------------------------------------------

async def _run_turn(agent_cfg, prompt: str, sink: WebSink,
                    thread_id: str = DEFAULT_THREAD) -> None:
    """Drive run_agent and push results into the sink. Session persists
    under the web:<agent>:<thread_id> key so web history stays distinct
    from Discord and each tab has its own conversation."""
    sessions = _load_sessions()
    key = _session_key(agent_cfg.name, thread_id)
    resume = sessions.get(key)

    lock = _lock(agent_cfg.name, thread_id)
    async with lock:
        try:
            final, session_id = await run_agent(
                agent_cfg, prompt, sink,
                resume_session_id=resume,
                current_hop=0,
                max_hops=3,
            )
            if session_id:
                sessions[key] = session_id
                _save_sessions(sessions)
            _append_history(agent_cfg.name, "assistant", final,
                            meta={"session_id": session_id},
                            thread_id=thread_id)
        except Exception as e:
            msg = f"agent error: {e}"
            await sink.error(msg)
            _append_history(agent_cfg.name, "assistant", f"⚠️ {msg}",
                            meta={"error": True},
                            thread_id=thread_id)


# ---- agent info (tools + prompt files for the chat side panel) -------------
#
# Surfaces what the agent is connected to AND what lives in its layered
# system prompt. Lets the chat UI show "here's what this agent knows and
# can do" without the operator having to grep the filesystem.

AGENTS_DIR = ROOT / "agents"
SHARED_DIR = ROOT / "shared"

# Safe roots that `/api/chat/<agent>/file` is allowed to serve. Any resolved
# path must live under one of these to escape 403.
def _allowed_file_roots(agent: str) -> list[Path]:
    return [
        AGENTS_DIR / agent,
        SHARED_DIR,
    ]


def _agent_info(agent: str) -> dict:
    from agent_loader import (
        LAYERED_FILES, SHARED_FILES, load_agent, _resolve_skill_ref,
    )
    cfg = load_agent(agent)
    agent_dir = AGENTS_DIR / agent

    # Merge global defaults + agent overrides the same way agent_loader does.
    import yaml as _yaml
    raw = {}
    cfg_path = agent_dir / "agent.yaml"
    if cfg_path.exists():
        raw = _yaml.safe_load(cfg_path.read_text()) or {}
    global_cfg = _yaml.safe_load((ROOT / "config.yaml").read_text()) or {}
    defaults = global_cfg.get("defaults", {}) or {}

    def _merged(key, fallback=None):
        return raw.get(key, defaults.get(key, fallback))

    allowed = _merged("allowed_tools", []) or []
    disallowed = _merged("disallowed_tools", []) or []
    model = _merged("model") or "inherited"
    permission_mode = _merged("permission_mode") or "default"

    # mcp_servers — split into SDK-wired / mcp notes / env-key HTTP integrations.
    raw_mcp = raw.get("mcp_servers") or {}
    mcp_wired: list[str] = []
    mcp_notes: list[dict] = []
    env_integrations: list[dict] = []

    # Registry fallback for older entries without api_* metadata.
    reg_by_id: dict[str, dict] = {}
    reg_path = ROOT / "connectors" / "registry.yaml"
    if reg_path.exists():
        reg = _yaml.safe_load(reg_path.read_text()) or {}
        for c in reg.get("connectors") or []:
            if c.get("id"):
                reg_by_id[c["id"]] = c

    for key, val in raw_mcp.items():
        if not isinstance(val, dict):
            mcp_wired.append(key)
            continue
        t = val.get("type")
        if t == "mcp":
            mcp_notes.append({"name": key, "note": val.get("note", "")})
        elif t == "env":
            enriched = reg_by_id.get(key, {})
            env_integrations.append({
                "name": key,
                "note": val.get("note") or enriched.get("note", ""),
                "env_vars": val.get("env_vars") or enriched.get("env_vars") or [],
                "api_base": val.get("api_base") or enriched.get("api_base"),
                "docs_url": val.get("docs_url") or enriched.get("docs_url"),
            })
        else:
            # Real MCP server (command/url/instance-based).
            mcp_wired.append(key)

    # Skills — resolve each ref into a path + existence flag.
    skills_out: list[dict] = []
    for ref in raw.get("skills") or []:
        p = _resolve_skill_ref(agent_dir, ref)
        skills_out.append({
            "ref": ref,
            "path": str(p.relative_to(ROOT)) if p else None,
            "exists": bool(p and p.exists()),
            "size": p.stat().st_size if p and p.exists() else 0,
        })

    # Layered + shared prompt files.
    def _manifest(root: Path, names: list[str]) -> list[dict]:
        out = []
        for n in names:
            p = root / n
            out.append({
                "name": n,
                "path": str(p.relative_to(ROOT)) if p.exists() else None,
                "exists": p.exists(),
                "size": p.stat().st_size if p.exists() else 0,
            })
        return out

    return {
        "agent": agent,
        "model": model,
        "permission_mode": permission_mode,
        "allowed_tools": allowed,
        "disallowed_tools": disallowed,
        "mcp_wired": sorted(set(mcp_wired)),
        "mcp_notes": mcp_notes,
        "env_integrations": env_integrations,
        "skills": skills_out,
        "layered_files": _manifest(agent_dir, LAYERED_FILES),
        "shared_files": _manifest(SHARED_DIR, SHARED_FILES),
        "prompt_total_chars": len(cfg.system_prompt or ""),
        "channel_id": cfg.channel_ids[0] if cfg.channel_ids else None,
    }


def _safe_read_file(agent: str, rel: str) -> dict:
    """Read a file under an allowed root. Reject traversal or anything
    that resolves outside agents/<agent>/ or shared/."""
    target = (ROOT / rel).resolve()
    roots = [r.resolve() for r in _allowed_file_roots(agent)]
    if not any(str(target).startswith(str(r) + "/") or str(target) == str(r)
               for r in roots):
        raise HTTPException(403, f"not in allowed roots: {rel}")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"file not found: {rel}")
    # Cap at 1MB so a huge file can't nuke the UI.
    size = target.stat().st_size
    if size > 1_000_000:
        raise HTTPException(413, f"file too large ({size} bytes)")
    return {
        "path": str(target.relative_to(ROOT)),
        "size": size,
        "content": target.read_text(errors="replace"),
    }


# ---- FastAPI router --------------------------------------------------------

router = APIRouter()


def _agent_map():
    # load_all_agents() is keyed by channel_id — rekey by agent name.
    by_channel = load_all_agents()
    return {cfg.name: cfg for cfg in by_channel.values()}


@router.get("/api/chat/agents")
def list_chat_agents() -> JSONResponse:
    agents = _agent_map()
    out = []
    for name in sorted(agents.keys()):
        threads = _load_threads(name)
        total_lines = sum(_history_line_count(name, t["id"]) for t in threads)
        out.append({
            "name": name,
            "thread_count": len(threads),
            "history_lines": total_lines,
        })
    return JSONResponse(out)


# ---- live events (SSE) + unread tracking -----------------------------------
#
# The Bus in events.py fans out every activity into a single feed. The
# browser subscribes once and updates badges live without polling. Unread
# counts per (browser, agent) are computed against a "last seen" timestamp
# the browser sends back up on mark-as-read.

@router.get("/api/chat/events")
async def chat_events() -> StreamingResponse:
    """SSE feed of every chat event across all agents. Used by the UI to
    paint red-dot badges when another agent replies or the operator gets
    a routed message. Survives restarts via events.jsonl."""
    from events import bus

    async def stream() -> AsyncIterator[bytes]:
        queue = bus.subscribe()
        # Initial comment so the browser's EventSource reports "open" quickly.
        yield b": hello\n\n"
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    # Keep-alive comment — Cloudflare tunnels drop idle streams.
                    yield b": ka\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n".encode()
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/api/chat/unread")
def chat_unread(since: str | None = None) -> JSONResponse:
    """Per-agent unread count since `since` (ISO-8601). If `since` is
    None, returns 0 for everything (browser needs to send its last-seen)."""
    from events import unread_by_agent
    counts = unread_by_agent(since) if since else {}
    return JSONResponse({"since": since, "counts": counts})


@router.get("/api/chat/{agent}/info")
def chat_agent_info(agent: str) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    return JSONResponse(_agent_info(agent))


@router.get("/api/chat/{agent}/file")
def chat_agent_file(agent: str, path: str) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    return JSONResponse(_safe_read_file(agent, path))


@router.get("/api/chat/{agent}/threads")
def list_threads(agent: str) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    sessions = _load_sessions()
    threads = _load_threads(agent)
    # Sort: most recently active first, but keep default pinned at top.
    threads.sort(key=lambda t: (t["id"] != DEFAULT_THREAD, -_ts_key(t.get("last_at", ""))))
    return JSONResponse({
        "agent": agent,
        "threads": [
            {
                **t,
                "session_id": sessions.get(_session_key(agent, t["id"])),
                "history_lines": _history_line_count(agent, t["id"]),
            }
            for t in threads
        ],
    })


def _ts_key(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


@router.post("/api/chat/{agent}/threads")
async def create_thread(agent: str, request: Request) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    title = (body.get("title") or "").strip() or None
    entry = _create_thread(agent, title=title)
    return JSONResponse({"agent": agent, "thread": entry})


@router.patch("/api/chat/{agent}/threads/{thread_id}")
async def rename_thread(agent: str, thread_id: str, request: Request) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "empty title")
    ok = _rename_thread(agent, thread_id, title)
    if not ok:
        raise HTTPException(404, "no such thread")
    return JSONResponse({"agent": agent, "thread_id": thread_id, "title": title})


@router.delete("/api/chat/{agent}/threads/{thread_id}")
def delete_thread(agent: str, thread_id: str) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    if thread_id == DEFAULT_THREAD:
        # Don't remove the default — just clear it so the tab stays.
        removed = _clear_history(agent, DEFAULT_THREAD)
        sessions = _load_sessions()
        sessions.pop(_session_key(agent, DEFAULT_THREAD), None)
        _save_sessions(sessions)
        return JSONResponse({"agent": agent, "thread_id": thread_id,
                             "cleared_lines": removed, "deleted": False})
    ok = _delete_thread(agent, thread_id)
    if not ok:
        raise HTTPException(404, "no such thread")
    return JSONResponse({"agent": agent, "thread_id": thread_id, "deleted": True})


@router.get("/api/chat/{agent}/history")
def chat_history(agent: str, limit: int = 200,
                 thread: str = DEFAULT_THREAD) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    return JSONResponse({
        "agent": agent,
        "thread_id": thread,
        "history": _read_history(agent, limit, thread_id=thread),
    })


@router.post("/api/chat/{agent}/clear")
def chat_clear(agent: str, thread: str = DEFAULT_THREAD) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    sessions = _load_sessions()
    sessions.pop(_session_key(agent, thread), None)
    _save_sessions(sessions)
    removed = _clear_history(agent, thread_id=thread)
    return JSONResponse({"agent": agent, "thread_id": thread,
                         "cleared_lines": removed, "session_reset": True})


# ---- slash commands --------------------------------------------------------

SLASH_HANDLERS: dict[str, callable] = {}


def slash(name: str):
    def deco(fn):
        SLASH_HANDLERS[name] = fn
        return fn
    return deco


@slash("status")
def _slash_status(agent: str, rest: str) -> dict:
    import subprocess
    out = subprocess.check_output(
        ["./.venv/bin/python", "scripts/status.py"],
        cwd=ROOT, text=True, timeout=15,
    )
    return {"reply": f"```\n{out}\n```"}


@slash("doctor")
def _slash_doctor(agent: str, rest: str) -> dict:
    import subprocess
    target = rest.strip() or agent
    cmd = ["./.venv/bin/python", "scripts/doctor.py"]
    if target:
        cmd += ["--agent", target]
    try:
        out = subprocess.check_output(
            cmd, cwd=ROOT, text=True, timeout=45,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        out = e.output
    return {"reply": f"```\n{out}\n```"}


@slash("route")
def _slash_route(agent: str, rest: str) -> dict:
    # Format: /route <target> <message>
    parts = rest.strip().split(None, 1)
    if len(parts) < 2:
        return {"reply": "Usage: `/route <agent> <message>`"}
    target, message = parts
    return {"reply": f"📡 Queued route: from @{agent} → @{target}\n> {message}\n\n_(not yet wired to send — use chat directly with @{target} for now)_"}


@slash("save")
def _slash_save(agent: str, rest: str) -> dict:
    return {"reply": "💾 Save slash command placeholder — will persist this turn to `Sessions/` once wired."}


@slash("help")
def _slash_help(agent: str, rest: str) -> dict:
    return {"reply": "**Slash commands:**\n- `/status` — system-wide rollup\n- `/doctor [agent]` — health check\n- `/route <agent> <msg>` — route to another agent\n- `/save` — pin this turn to the vault\n- `/clear` — reset session + chat\n- `/help` — this message"}


def _try_slash(agent: str, prompt: str) -> dict | None:
    if not prompt.startswith("/"):
        return None
    name, _, rest = prompt[1:].partition(" ")
    handler = SLASH_HANDLERS.get(name)
    if not handler:
        return None
    try:
        return handler(agent, rest)
    except Exception as e:
        return {"reply": f"⚠️ slash `/{name}` failed: {e}"}


# ---- trajectory tail (live tool-use events) --------------------------------

async def _tail_trajectory_live(agent: str, sink: WebSink, stop_event: asyncio.Event) -> None:
    """While a turn runs, tail the latest trajectory JSONL and push each
    new tool_use / thinking event to the sink as a 'tool' event. Lets the
    chat UI show live tool activity instead of a blank spinner."""
    traj_dir = ROOT / "logs" / "trajectories" / agent
    # Wait briefly for the trajectory file to appear.
    start = time.time()
    path = None
    while time.time() - start < 10.0:
        if traj_dir.exists():
            candidates = list(traj_dir.glob("*.jsonl"))
            if candidates:
                path = max(candidates, key=lambda p: p.stat().st_mtime)
                # Only if it was modified since we started waiting.
                if path.stat().st_mtime > start - 5:
                    break
        await asyncio.sleep(0.3)
    if not path:
        return

    # Start from end (seek only the tail — we want new events this turn).
    offset = path.stat().st_size
    while not stop_event.is_set():
        try:
            size = path.stat().st_size
            if size > offset:
                with path.open() as f:
                    f.seek(offset)
                    for line in f:
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        t = ev.get("type")
                        if t == "tool_use":
                            await sink.queue.put({
                                "type": "tool_use",
                                "name": ev.get("name"),
                                "input": ev.get("input") or {},
                            })
                        elif t == "tool_result":
                            await sink.queue.put({
                                "type": "tool_result",
                                "is_error": ev.get("is_error"),
                                "preview": str(ev.get("content") or "")[:200],
                            })
                        elif t == "thinking":
                            await sink.queue.put({
                                "type": "thinking",
                                "text": str(ev.get("content") or "")[:400],
                            })
                    offset = f.tell()
        except Exception:
            pass
        await asyncio.sleep(0.5)


@router.post("/api/chat/{agent}/stream")
async def chat_stream(agent: str, request: Request,
                      thread: str = DEFAULT_THREAD) -> StreamingResponse:
    agents = _agent_map()
    if agent not in agents:
        raise HTTPException(404, "no such agent")

    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    attachments = body.get("attachments") or []
    # Allow thread id in body too (makes the JS call site simpler).
    thread = (body.get("thread") or thread or DEFAULT_THREAD).strip() or DEFAULT_THREAD
    if not prompt and not attachments:
        raise HTTPException(400, "empty prompt")

    # Auto-title a freshly-created thread from its first user prompt.
    threads = _load_threads(agent)
    matched = next((t for t in threads if t["id"] == thread), None)
    is_new_thread = matched and matched.get("title", "").startswith("Thread ") \
                    and _history_line_count(agent, thread) == 0
    if is_new_thread and prompt:
        snippet = prompt.splitlines()[0][:48].strip()
        if snippet:
            _rename_thread(agent, thread, snippet)

    # Inject attachment paths into the prompt so the agent can Read them.
    if attachments:
        lines = [f"- `{p}`" for p in attachments if isinstance(p, str)]
        if lines:
            prompt = (prompt + ("\n\n" if prompt else "")
                      + "**Attached files** (saved locally, you can `Read` them):\n"
                      + "\n".join(lines))

    agent_cfg = agents[agent]
    turn_id = uuid.uuid4().hex[:12]

    # Short-circuit slash commands — return a canned reply without invoking the SDK.
    slash_result = _try_slash(agent, prompt)
    if slash_result:
        _append_history(agent, "user", prompt,
                        meta={"turn_id": turn_id, "slash": True}, thread_id=thread)
        _append_history(agent, "assistant", slash_result["reply"],
                        meta={"slash": True}, thread_id=thread)

        async def slash_stream() -> AsyncIterator[bytes]:
            yield f"data: {json.dumps({'type': 'start', 'turn_id': turn_id})}\n\n".encode()
            yield f"data: {json.dumps({'type': 'final', 'text': slash_result['reply']})}\n\n".encode()
            yield b"data: {\"type\":\"end\"}\n\n"

        return StreamingResponse(slash_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Log the user prompt before kicking the turn so it survives crashes.
    _append_history(agent, "user", prompt, meta={"turn_id": turn_id}, thread_id=thread)

    sink = WebSink()
    task = asyncio.create_task(_run_turn(agent_cfg, prompt, sink, thread_id=thread))
    stop_tail = asyncio.Event()
    tail_task = asyncio.create_task(_tail_trajectory_live(agent_cfg.name, sink, stop_tail))

    async def event_stream() -> AsyncIterator[bytes]:
        yield f"data: {json.dumps({'type': 'start', 'turn_id': turn_id})}\n\n".encode()
        while True:
            try:
                ev = await asyncio.wait_for(sink.queue.get(), timeout=600.0)
            except asyncio.TimeoutError:
                yield b"data: {\"type\":\"error\",\"message\":\"turn timed out (10m)\"}\n\n"
                break
            if ev.get("type") == "_done":
                break
            yield f"data: {json.dumps(ev)}\n\n".encode()
        stop_tail.set()
        # Ensure the background task is collected.
        if not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                pass
        if not tail_task.done():
            tail_task.cancel()
        yield b"data: {\"type\":\"end\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


# ---- attachment upload -----------------------------------------------------

UPLOADS_DIR = ROOT / "logs" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ---- push notification subscriptions ---------------------------------------
# Future-ready: stores webpush subscriptions keyed by endpoint so a later
# server-side push can fire desktop alerts when the tab is closed. For the
# in-tab case, the browser's Notification API in chat.js already covers it.

PUSH_SUBS_FILE = ROOT / "logs" / "push_subs.json"


def _load_push_subs() -> list[dict]:
    if PUSH_SUBS_FILE.exists():
        try:
            return json.loads(PUSH_SUBS_FILE.read_text())
        except json.JSONDecodeError:
            return []
    return []


def _save_push_subs(subs: list[dict]) -> None:
    PUSH_SUBS_FILE.write_text(json.dumps(subs, indent=2))


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request) -> JSONResponse:
    sub = await request.json()
    if not sub.get("endpoint"):
        raise HTTPException(400, "missing endpoint")
    subs = _load_push_subs()
    if not any(s["endpoint"] == sub["endpoint"] for s in subs):
        subs.append(sub)
        _save_push_subs(subs)
    return JSONResponse({"ok": True, "count": len(subs)})


@router.post("/api/chat/{agent}/upload")
async def chat_upload(agent: str, request: Request) -> JSONResponse:
    """Accepts multipart/form-data with one or more files. Saves under
    logs/uploads/<agent>/<timestamp>-<name>. Returns the resolved paths
    so the browser can include them in the next prompt."""
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    form = await request.form()
    out_dir = UPLOADS_DIR / agent
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for _, item in form.multi_items():
        # Duck-type rather than isinstance — starlette's UploadFile class can
        # fail isinstance() checks when fastapi/starlette versions drift.
        if not (hasattr(item, "filename") and hasattr(item, "read") and callable(getattr(item, "read", None))):
            continue
        filename = getattr(item, "filename", None) or "upload"
        if not filename or filename == "upload" and not hasattr(item, "content_type"):
            continue
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        safe = "".join(c for c in filename if c.isalnum() or c in "._-") or "upload"
        local = out_dir / f"{ts}-{safe}"
        data = await item.read()
        if not isinstance(data, (bytes, bytearray)):
            # String fields slip through — skip them.
            continue
        local.write_bytes(data)
        entry = {"path": str(local), "name": filename, "bytes": len(data),
                 "type": getattr(item, "content_type", None)}
        # If audio, transcribe immediately so the prompt carries the transcript.
        try:
            from transcribe import is_audio, transcribe
            if is_audio(local):
                text = await transcribe(local)
                if text:
                    entry["transcript"] = text
        except Exception:
            pass
        saved.append(entry)
    return JSONResponse({"agent": agent, "files": saved})


# ---- HTML pages ------------------------------------------------------------

CHAT_CSS = """
  .chat-layout{display:grid;grid-template-columns:220px 1fr 320px;gap:1em;height:calc(100vh - 120px);min-height:500px;}
  .chat-layout.info-collapsed{grid-template-columns:220px 1fr;}
  .chat-sidebar{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:0.5em;overflow-y:auto;}
  .chat-sidebar h3{font-size:0.7em;text-transform:uppercase;letter-spacing:0.08em;color:var(--fg-mute);padding:0.5em 0.6em;margin:0;}
  .chat-sidebar a{display:block;padding:0.5em 0.6em;border-radius:6px;color:var(--fg-dim);text-decoration:none;font-size:0.9em;}
  .chat-sidebar a:hover{background:var(--panel-2);color:var(--fg);}
  .chat-sidebar a.active{background:var(--panel-2);color:var(--accent);font-weight:500;}
  .chat-sidebar a{position:relative;}
  .chat-sidebar .agent-meta{font-size:0.7em;color:var(--fg-mute);margin-left:0.8em;}
  /* Unread badge — red dot with optional count. */
  .badge{display:inline-block;min-width:0.5em;height:0.5em;border-radius:50%;background:var(--fail,#e66);vertical-align:middle;margin-left:0.4em;box-shadow:0 0 0 2px var(--panel);}
  .badge.count{min-width:1.4em;height:1.1em;line-height:1.1em;border-radius:0.7em;padding:0 0.4em;font-size:0.7em;color:#fff;font-weight:600;text-align:center;}
  .chat-tab .badge{margin-left:0;margin-right:0.2em;}
  .chat-main{display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden;}
  .chat-header{padding:0.8em 1.2em;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:1em;}
  .chat-header h2{margin:0;text-transform:none;letter-spacing:0;font-size:1.1em;color:var(--fg);}
  .chat-header .meta{flex:1;}
  .chat-header button{background:var(--panel-2);border:1px solid var(--border);color:var(--fg-dim);padding:0.3em 0.8em;border-radius:6px;cursor:pointer;font-size:0.8em;}
  .chat-header button:hover{color:var(--fg);border-color:var(--accent);}
  /* Tab bar — one row of thread tabs + "+ new" button. */
  .chat-tabs{display:flex;align-items:stretch;gap:0;border-bottom:1px solid var(--border);background:var(--panel);overflow-x:auto;scrollbar-width:thin;}
  .chat-tabs::-webkit-scrollbar{height:4px;}
  .chat-tabs::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
  .chat-tab{display:inline-flex;align-items:center;gap:0.4em;padding:0.55em 0.8em 0.55em 0.9em;border-right:1px solid var(--border);background:transparent;color:var(--fg-dim);cursor:pointer;font-size:0.85em;max-width:220px;white-space:nowrap;user-select:none;}
  .chat-tab:hover{background:var(--panel-2);color:var(--fg);}
  .chat-tab.active{background:var(--panel-2);color:var(--fg);border-bottom:2px solid var(--accent);margin-bottom:-1px;}
  .chat-tab .title{overflow:hidden;text-overflow:ellipsis;max-width:180px;}
  .chat-tab .close{background:none;border:none;color:var(--fg-mute);padding:0 0.2em;margin-left:0.2em;cursor:pointer;border-radius:4px;font-size:1em;line-height:1;}
  .chat-tab .close:hover{background:var(--panel);color:var(--fail);}
  .chat-tab-new{display:inline-flex;align-items:center;justify-content:center;width:40px;border:none;border-right:1px solid var(--border);background:transparent;color:var(--fg-mute);cursor:pointer;font-size:1.1em;font-weight:500;}
  .chat-tab-new:hover{background:var(--panel-2);color:var(--accent);}
  .chat-messages{flex:1;overflow-y:auto;padding:1em 1.2em;scroll-behavior:smooth;}
  .msg{margin-bottom:1.2em;max-width:90%;}
  .msg.user{margin-left:auto;}
  .msg .role{font-size:0.7em;text-transform:uppercase;letter-spacing:0.08em;color:var(--fg-mute);margin-bottom:0.3em;}
  .msg.user .role{text-align:right;color:var(--accent);}
  .msg.assistant .role{color:var(--accent-2);}
  .msg .bubble{padding:0.8em 1em;border-radius:12px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word;font-size:0.94em;}
  .msg.user .bubble{background:rgba(111,168,255,0.12);border:1px solid rgba(111,168,255,0.3);}
  .msg.assistant .bubble{background:var(--panel-2);border:1px solid var(--border);}
  /* Routed-in message from another agent (mirrored from send_to_agent). */
  .msg.routed .role{color:var(--accent-2);}
  .msg.routed .bubble{background:rgba(231,184,76,0.08);border:1px solid rgba(231,184,76,0.3);}
  .msg.routed .bubble::before{content:"📡 from @" attr(data-from);display:block;font-size:0.78em;color:var(--fg-mute);margin-bottom:0.3em;letter-spacing:0.02em;}
  .msg .ts{font-size:0.7em;color:var(--fg-mute);margin-top:0.3em;}
  .msg.user .ts{text-align:right;}
  .chat-input{border-top:1px solid var(--border);padding:0.9em 1.1em calc(0.9em + env(safe-area-inset-bottom)) 1.1em;display:flex;gap:0.6em;align-items:flex-end;background:var(--panel);}
  .chat-input textarea{flex:1;min-width:0;background:var(--panel-2);border:1px solid var(--border);color:var(--fg);padding:0.7em 1em;border-radius:10px;font-family:inherit;font-size:0.95em;resize:none;min-height:44px;max-height:10em;box-sizing:border-box;}
  .chat-input textarea:focus{outline:none;border-color:var(--fg);}
  /* Send button — primary action, theme-aware */
  .chat-input button#chat-send{background:var(--accent);color:var(--accent-ink);border:none;padding:0 1.2em;min-height:44px;min-width:64px;border-radius:10px;cursor:pointer;font-weight:600;font-size:0.92em;font-family:inherit;flex-shrink:0;transition:opacity 0.14s;}
  .chat-input button#chat-send:disabled{opacity:0.35;cursor:not-allowed;}
  .chat-input button#chat-send:hover:not(:disabled){opacity:0.9;}
  .chat-empty{display:flex;align-items:center;justify-content:center;flex:1;color:var(--fg-mute);font-size:0.9em;}
  .typing{display:inline-block;padding:0.3em 0.8em;background:var(--panel-2);border-radius:8px;color:var(--fg-mute);font-size:0.85em;}
  .typing .dot{display:inline-block;animation:blink 1.4s infinite;}
  .typing .dot:nth-child(2){animation-delay:0.2s;}
  .typing .dot:nth-child(3){animation-delay:0.4s;}
  @keyframes blink{0%,80%,100%{opacity:0.3;}40%{opacity:1;}}
  .tool-log{margin:0.3em 0;font-size:0.8em;}
  .tool-event{padding:0.25em 0.7em;margin-bottom:0.2em;border-left:2px solid var(--border);color:var(--fg-dim);background:var(--panel-2);border-radius:0 6px 6px 0;}
  .tool-event code{background:transparent;padding:0;color:var(--accent);}
  .chat-attach-chips{display:none;gap:0.4em;flex-wrap:wrap;padding:0.4em 1.2em 0;}
  .chip{background:var(--panel-2);border:1px solid var(--border);padding:0.2em 0.5em;border-radius:6px;font-size:0.8em;display:inline-flex;align-items:center;gap:0.3em;}
  .chip-x{background:none;border:none;color:var(--fg-mute);cursor:pointer;font-size:1.1em;padding:0 0.2em;}
  .chip-x:hover{color:var(--fail);}
  .chat-messages.dragging{background:rgba(111,168,255,0.05);outline:2px dashed var(--accent);outline-offset:-8px;}
  .chat-main.dragging{outline:2px dashed var(--accent);outline-offset:-4px;position:relative;}
  .chat-main.dragging::after{content:"drop files to attach";position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--panel-2);border:1px solid var(--accent);padding:0.6em 1.2em;border-radius:8px;color:var(--accent);font-size:0.95em;pointer-events:none;z-index:10;}
  .tool-event.thinking{border-left:2px solid var(--accent-2);background:rgba(111,168,255,0.04);}
  /* Attach button — secondary, readable icon, 44px tap target */
  .chat-input button.chat-attach-btn{background:var(--panel-2);border:1px solid var(--border);color:var(--fg);padding:0;width:44px;height:44px;min-height:44px;border-radius:10px;cursor:pointer;font-size:1.2em;line-height:1;flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;font-family:inherit;}
  .chat-input button.chat-attach-btn:hover{color:var(--fg);border-color:var(--fg);background:var(--panel-3);}
  /* Right info panel — tools + system prompt files */
  .chat-info{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:0.6em 0.8em;overflow-y:auto;font-size:0.85em;}
  .chat-info h3{font-size:0.7em;text-transform:uppercase;letter-spacing:0.08em;color:var(--fg-mute);margin:0.8em 0 0.4em 0;padding:0;}
  .chat-info h3:first-child{margin-top:0;}
  .chat-info .pill{display:inline-block;background:var(--panel-2);border:1px solid var(--border);color:var(--fg-dim);padding:0.1em 0.5em;border-radius:10px;font-size:0.78em;margin:0 0.2em 0.3em 0;}
  .chat-info .pill.enabled{color:var(--accent);border-color:var(--accent);}
  .chat-info .pill.disabled{color:var(--fg-mute);opacity:0.5;text-decoration:line-through;}
  .chat-info .row{display:flex;align-items:center;gap:0.4em;font-size:0.82em;padding:0.25em 0;border-radius:4px;}
  .chat-info .row.clickable{cursor:pointer;}
  .chat-info .row.clickable:hover{background:var(--panel-2);}
  .chat-info .row.missing{opacity:0.45;}
  .chat-info .row code{background:transparent;color:var(--accent);padding:0;font-size:0.9em;}
  .chat-info .size{color:var(--fg-mute);font-size:0.7em;margin-left:auto;}
  .chat-info .integration{padding:0.3em 0.5em;margin:0.2em 0;background:var(--panel-2);border-radius:6px;}
  .chat-info .integration .name{font-weight:500;color:var(--fg);}
  .chat-info .integration .vars{color:var(--fg-mute);font-size:0.75em;font-family:monospace;margin-top:0.15em;}
  .chat-info .meta-line{color:var(--fg-mute);font-size:0.78em;margin-bottom:0.3em;}
  /* File viewer modal */
  .file-viewer{position:fixed;inset:0;background:rgba(0,0,0,0.6);display:none;align-items:center;justify-content:center;z-index:1000;padding:1em;}
  .file-viewer.open{display:flex;}
  .file-viewer .fv-body{background:var(--panel);border:1px solid var(--border);border-radius:10px;max-width:900px;width:100%;max-height:90vh;display:flex;flex-direction:column;overflow:hidden;}
  .file-viewer .fv-head{padding:0.7em 1em;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:1em;}
  .file-viewer .fv-head h3{margin:0;font-size:0.95em;color:var(--fg);text-transform:none;letter-spacing:0;flex:1;}
  .file-viewer .fv-head code{color:var(--accent);font-size:0.8em;}
  .file-viewer .fv-head button{background:var(--panel-2);border:1px solid var(--border);color:var(--fg-dim);padding:0.3em 0.8em;border-radius:6px;cursor:pointer;font-size:0.8em;}
  .file-viewer pre{flex:1;overflow:auto;margin:0;padding:1em 1.2em;background:var(--panel-2);font-size:0.82em;line-height:1.5;white-space:pre-wrap;word-break:break-word;}
  .chat-header .info-toggle{font-size:1.1em;}
  @media (max-width:1100px){
    .chat-layout{grid-template-columns:220px 1fr;}
    .chat-info{display:none;}
    .chat-layout.info-open .chat-info{display:block;position:fixed;top:70px;right:1em;bottom:1em;width:320px;z-index:100;box-shadow:0 10px 40px rgba(0,0,0,0.3);}
  }
  @media (max-width:780px){
    .chat-layout{grid-template-columns:1fr;grid-template-rows:auto 1fr;}
    .chat-sidebar{max-height:120px;}
    .chat-sidebar a{display:inline-block;margin-right:0.3em;}
    .chat-info{display:none;}
    .chat-layout.info-open .chat-info{display:block;position:fixed;top:60px;left:0.5em;right:0.5em;bottom:0.5em;width:auto;z-index:100;}
  }
"""


CHAT_PAGE_JS = r"""
(function(){
  const agentName = window.__AGENT_NAME__;
  const messages = document.getElementById('chat-messages');
  const input = document.getElementById('chat-input');
  const send = document.getElementById('chat-send');
  const clearBtn = document.getElementById('chat-clear');
  const fileInput = document.getElementById('chat-file');
  const fileBtn = document.getElementById('chat-attach');
  const attachChips = document.getElementById('chat-attach-chips');
  const tabsBar = document.getElementById('chat-tabs');
  const threadMeta = document.getElementById('chat-thread-meta');
  let pendingAttachments = [];  // paths of uploaded files
  let threads = [];             // [{id, title, last_at, ...}]
  let activeThread = 'default';

  // Register PWA service worker (silent fail in dev).
  if ('serviceWorker' in navigator){
    navigator.serviceWorker.register('/sw.js').catch(()=>{});
  }
  // Ask for notification permission once per session.
  if ('Notification' in window && Notification.permission === 'default'){
    // Defer until first send so we don't prompt on page load.
  }

  function el(tag, cls, html){
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  function escapeHtml(s){
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // Very light markdown: code blocks, inline code, bold, italic, links.
  function renderMd(s){
    if(!s) return '';
    s = escapeHtml(s);
    // fenced code
    s = s.replace(/```([\s\S]*?)```/g, (_, c) => `<pre><code>${c}</code></pre>`);
    // inline code
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    // bold
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // italic
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    // links
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    // bare urls
    s = s.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank">$2</a>');
    return s;
  }

  function addMessage(role, content, ts, meta){
    const wrap = el('div', 'msg ' + role);
    const label = role === 'routed' ? ('routed @' + (meta && meta.from ? meta.from : '?')) : role;
    wrap.appendChild(el('div', 'role', label));
    const bubble = el('div', 'bubble');
    if (role === 'routed' && meta && meta.from){
      bubble.setAttribute('data-from', meta.from);
    }
    bubble.innerHTML = renderMd(content);
    wrap.appendChild(bubble);
    if (ts) wrap.appendChild(el('div', 'ts', escapeHtml(ts)));
    messages.appendChild(wrap);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  function updateAssistantBubble(bubble, text){
    bubble.innerHTML = renderMd(text);
    messages.scrollTop = messages.scrollHeight;
  }

  async function loadHistory(){
    try{
      const r = await fetch(`/api/chat/${agentName}/history?limit=200&thread=${encodeURIComponent(activeThread)}`);
      const data = await r.json();
      messages.innerHTML = '';
      for (const m of data.history){
        addMessage(m.role, m.content, m.ts, m.meta);
      }
      if (data.history.length === 0){
        messages.innerHTML = '<div class="chat-empty">Start a conversation with @' + escapeHtml(agentName) + '.</div>';
      }
    } catch(e){
      messages.innerHTML = '<div class="chat-empty">Failed to load history: ' + escapeHtml(e.message) + '</div>';
    }
  }

  // ---- tabs ----
  function renderTabs(){
    tabsBar.innerHTML = '';
    for (const t of threads){
      const tab = document.createElement('div');
      tab.className = 'chat-tab' + (t.id === activeThread ? ' active' : '');
      tab.dataset.tid = t.id;
      const title = el('span', 'title', escapeHtml(t.title || t.id));
      title.title = (t.history_lines || 0) + ' msgs · last ' + (t.last_at || '—');
      tab.appendChild(title);
      const x = el('button', 'close');
      x.textContent = '×';
      x.title = t.id === 'default' ? 'Clear this thread' : 'Close thread';
      x.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const label = t.id === 'default' ? 'Clear the default thread?' : 'Close "' + (t.title || t.id) + '"?';
        if (!confirm(label)) return;
        await fetch(`/api/chat/${agentName}/threads/${encodeURIComponent(t.id)}`, {method: 'DELETE'});
        if (t.id === activeThread && t.id !== 'default'){
          // Fall back to the default thread.
          activeThread = 'default';
          setHash(activeThread);
        }
        await loadThreads();
      });
      tab.appendChild(x);
      tab.addEventListener('click', () => switchThread(t.id));
      // Double-click title to rename.
      title.addEventListener('dblclick', async (ev) => {
        ev.stopPropagation();
        const next = prompt('Rename thread:', t.title || '');
        if (next && next.trim()){
          await fetch(`/api/chat/${agentName}/threads/${encodeURIComponent(t.id)}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title: next.trim()}),
          });
          await loadThreads();
        }
      });
      tabsBar.appendChild(tab);
    }
    const plus = el('button', 'chat-tab-new');
    plus.textContent = '+';
    plus.title = 'New thread (Cmd/Ctrl+T)';
    plus.addEventListener('click', () => newThread());
    tabsBar.appendChild(plus);
    updateThreadMeta();
  }

  function updateThreadMeta(){
    const active = threads.find(t => t.id === activeThread);
    if (!active){
      threadMeta.textContent = 'thread ' + activeThread;
      return;
    }
    const n = active.history_lines || 0;
    threadMeta.textContent = (active.title || active.id) + ' · ' + n + ' msg' + (n === 1 ? '' : 's');
  }

  async function loadThreads(preserveActive){
    try {
      const r = await fetch(`/api/chat/${agentName}/threads`);
      const data = await r.json();
      threads = data.threads || [];
      if (!threads.length){
        threads = [{id: 'default', title: 'Default'}];
      }
      const want = preserveActive || activeThread;
      if (!threads.find(t => t.id === want)){
        activeThread = threads[0].id;
        setHash(activeThread);
      } else {
        activeThread = want;
      }
      renderTabs();
      await loadHistory();
    } catch(e){
      threadMeta.textContent = 'thread load failed';
    }
  }

  async function newThread(){
    try {
      const r = await fetch(`/api/chat/${agentName}/threads`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({}),
      });
      const data = await r.json();
      activeThread = data.thread.id;
      setHash(activeThread);
      await loadThreads(activeThread);
      input.focus();
    } catch(e){
      alert('Failed to create thread: ' + e.message);
    }
  }

  function switchThread(tid){
    if (tid === activeThread) return;
    activeThread = tid;
    setHash(tid);
    renderTabs();
    loadHistory();
    input.focus();
  }

  function setHash(tid){
    const target = '#t=' + encodeURIComponent(tid);
    if (location.hash !== target){
      history.replaceState(null, '', target);
    }
  }

  function readHash(){
    const m = /^#t=([^&]+)/.exec(location.hash);
    return m ? decodeURIComponent(m[1]) : null;
  }

  function renderChips(){
    if (pendingAttachments.length === 0){
      attachChips.innerHTML = '';
      attachChips.style.display = 'none';
      return;
    }
    attachChips.style.display = 'flex';
    attachChips.innerHTML = pendingAttachments.map((a, i) =>
      `<span class="chip">📎 ${escapeHtml(a.name || 'file')} <button data-i="${i}" class="chip-x">×</button></span>`
    ).join('');
    attachChips.querySelectorAll('.chip-x').forEach(b => {
      b.addEventListener('click', () => {
        pendingAttachments.splice(parseInt(b.dataset.i), 1);
        renderChips();
      });
    });
  }

  async function uploadFiles(fileList){
    const fd = new FormData();
    for (const f of fileList) fd.append('file', f);
    const r = await fetch(`/api/chat/${agentName}/upload`, {method:'POST', body: fd});
    if (!r.ok) { alert('Upload failed: ' + r.status); return; }
    const data = await r.json();
    for (const f of data.files){
      pendingAttachments.push(f);
    }
    renderChips();
  }

  async function sendPrompt(){
    const prompt = input.value.trim();
    if (!prompt && pendingAttachments.length === 0) return;
    send.disabled = true;
    input.value = '';
    input.style.height = 'auto';
    const attachments = pendingAttachments.map(a => a.path);
    pendingAttachments = [];
    renderChips();

    // Ask for notification permission on first send (best UX moment).
    if ('Notification' in window && Notification.permission === 'default'){
      Notification.requestPermission().catch(()=>{});
    }

    // clear empty state if present
    const empty = messages.querySelector('.chat-empty');
    if (empty) empty.remove();

    addMessage('user', prompt || '(attachment only)', new Date().toISOString().slice(0, 19));
    const assistantBubble = addMessage('assistant', '');
    assistantBubble.innerHTML = '<span class="typing"><span class="dot">•</span><span class="dot">•</span><span class="dot">•</span></span>';
    const toolLog = document.createElement('div');
    toolLog.className = 'tool-log';
    assistantBubble.parentElement.insertBefore(toolLog, assistantBubble);

    let currentText = '';
    let sawFinal = false;
    const startTime = Date.now();
    // Dispatch a single SSE event onto the UI. Shared between streaming and
    // blocking-text paths so both render identically.
    function handleEvent(ev){
      if (ev.type === 'update' || ev.type === 'final'){
        currentText = ev.text || '';
        updateAssistantBubble(assistantBubble, currentText);
        if (ev.type === 'final') sawFinal = true;
      } else if (ev.type === 'tool_use'){
        const div = document.createElement('div');
        div.className = 'tool-event';
        const inp = JSON.stringify(ev.input || {}).slice(0, 100);
        div.innerHTML = '🔧 <code>' + escapeHtml(ev.name) + '</code> <span class="meta">' + escapeHtml(inp) + '</span>';
        toolLog.appendChild(div);
        messages.scrollTop = messages.scrollHeight;
      } else if (ev.type === 'tool_result'){
        const div = document.createElement('div');
        div.className = 'tool-event';
        const icon = ev.is_error ? '❌' : '✅';
        div.innerHTML = icon + ' <span class="meta">' + escapeHtml(ev.preview || '') + '</span>';
        toolLog.appendChild(div);
        messages.scrollTop = messages.scrollHeight;
      } else if (ev.type === 'thinking'){
        const div = document.createElement('div');
        div.className = 'tool-event thinking';
        div.innerHTML = '🧠 <em>' + escapeHtml(ev.text || '') + '</em>';
        toolLog.appendChild(div);
        messages.scrollTop = messages.scrollHeight;
      } else if (ev.type === 'error'){
        assistantBubble.innerHTML = '<span style="color:var(--fail)">⚠️ ' + escapeHtml(ev.message) + '</span>';
      }
    }

    function parseChunk(chunk){
      if (!chunk || !chunk.startsWith('data:')) return;
      const json = chunk.slice(5).trim();
      if (!json) return;
      let ev; try { ev = JSON.parse(json); } catch(_){ return; }
      handleEvent(ev);
    }
    // iOS Safari + Vercel-edge + CF-tunnel mangle ReadableStream bodies.
    // Detect iOS/Safari and skip the streaming path. Desktop Chrome/Firefox/Safari
    // still get live token streaming.
    const ua = navigator.userAgent || '';
    const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const hasStreams = !isIOS && typeof ReadableStream !== 'undefined' && typeof TextDecoder !== 'undefined';

    try {
      const resp = await fetch(`/api/chat/${agentName}/stream?thread=${encodeURIComponent(activeThread)}`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({prompt, attachments, thread: activeThread}),
      });
      if (!resp.ok){
        throw new Error('HTTP ' + resp.status);
      }
      if (hasStreams && resp.body && resp.body.getReader){
        const reader = resp.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        while (true){
          const {value, done} = await reader.read();
          if (done) break;
          buf += dec.decode(value, {stream:true});
          const events = buf.split('\n\n');
          buf = events.pop() || '';
          for (const chunk of events) parseChunk(chunk);
        }
        if (buf.trim()) parseChunk(buf);
      } else {
        // Blocking path: wait for the full body, then replay all SSE events.
        const text = await resp.text();
        for (const chunk of text.split('\n\n')) parseChunk(chunk);
      }
      // Fire a local desktop notification if the turn took long and the tab isn't focused.
      const elapsed = (Date.now() - startTime) / 1000;
      if (sawFinal && elapsed > 15 && document.visibilityState !== 'visible'){
        if ('Notification' in window && Notification.permission === 'granted'){
          new Notification('@' + agentName + ' replied', {
            body: currentText.slice(0, 140),
            icon: '/static/icon-192.png',
            tag: 'agentos-' + agentName + ':' + activeThread,
          });
        }
      }
      // Refresh tab metadata (message counts, timestamps, auto-titled threads).
      loadThreads(activeThread).catch(()=>{});
    } catch(e){
      assistantBubble.innerHTML = '<span style="color:var(--fail)">⚠️ ' + escapeHtml(e.message) + '</span>';
    } finally {
      send.disabled = false;
      input.focus();
    }
  }

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey){
      e.preventDefault();
      sendPrompt();
    }
  });
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  });
  send.addEventListener('click', sendPrompt);

  clearBtn.addEventListener('click', async () => {
    const label = 'Reset session and clear the current thread for @' + agentName + '?';
    if (!confirm(label)) return;
    await fetch(`/api/chat/${agentName}/clear?thread=${encodeURIComponent(activeThread)}`, {method: 'POST'});
    await loadThreads(activeThread);
  });

  // Keyboard shortcuts: Cmd/Ctrl+T = new thread; Cmd/Ctrl+W = close current tab.
  document.addEventListener('keydown', (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (!mod || e.altKey) return;
    if (e.key === 't' || e.key === 'T'){
      e.preventDefault();
      newThread();
    } else if ((e.key === 'w' || e.key === 'W') && activeThread !== 'default'){
      e.preventDefault();
      const active = threads.find(t => t.id === activeThread);
      if (active && confirm('Close "' + (active.title || active.id) + '"?')){
        fetch(`/api/chat/${agentName}/threads/${encodeURIComponent(active.id)}`, {method: 'DELETE'})
          .then(() => { activeThread = 'default'; setHash('default'); return loadThreads(); });
      }
    }
  });
  window.addEventListener('hashchange', () => {
    const hashTid = readHash();
    if (hashTid && hashTid !== activeThread && threads.find(t => t.id === hashTid)){
      switchThread(hashTid);
    }
  });

  fileBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    if (fileInput.files && fileInput.files.length){
      await uploadFiles(fileInput.files);
      fileInput.value = '';
    }
  });

  // Paste-to-attach (images from clipboard).
  document.addEventListener('paste', (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    const files = [];
    for (const it of items){
      if (it.kind === 'file'){
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) uploadFiles(files);
  });

  // Drag-and-drop: capture at document/window level so drops on textarea,
  // buttons, or any nested child still route to uploadFiles(). This is the
  // pattern Discord/Slack use — without it, <textarea> swallows the drop.
  const dropZone = document.getElementById('chat-pane') || messages;
  function isFileDrag(e){
    return e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
  }
  // Block browser default (navigating to the file) everywhere on the page.
  window.addEventListener('dragover', (e) => {
    if (isFileDrag(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; }
  });
  window.addEventListener('drop', (e) => {
    if (isFileDrag(e)) e.preventDefault();
  });
  // Overlay lifecycle driven by enter/leave on the document.
  let dragDepth = 0;
  document.addEventListener('dragenter', (e) => {
    if (!isFileDrag(e)) return;
    dragDepth++;
    dropZone.classList.add('dragging');
  });
  document.addEventListener('dragleave', (e) => {
    if (!isFileDrag(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropZone.classList.remove('dragging');
  });
  document.addEventListener('drop', (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    dragDepth = 0;
    dropZone.classList.remove('dragging');
    if (e.dataTransfer.files && e.dataTransfer.files.length){
      uploadFiles(e.dataTransfer.files);
    }
  });

  // ---- info panel (tools + system prompt files) ----
  const infoBody = document.getElementById('chat-info-body');
  const infoToggle = document.getElementById('chat-info-toggle');
  const layout = document.getElementById('chat-layout');

  function fmtBytes(n){
    if (!n) return '';
    if (n < 1024) return n + 'B';
    if (n < 1024*1024) return (n/1024).toFixed(1) + 'KB';
    return (n/1024/1024).toFixed(1) + 'MB';
  }

  async function loadInfo(){
    try {
      const r = await fetch(`/api/chat/${agentName}/info`);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      renderInfo(d);
    } catch(e){
      infoBody.innerHTML = '<div class="meta-line">failed to load info: ' + escapeHtml(e.message) + '</div>';
    }
  }

  function renderInfo(d){
    const parts = [];
    parts.push(`<div class="meta-line">model <code>${escapeHtml(d.model)}</code> · ${escapeHtml(d.permission_mode)} · ${d.prompt_total_chars.toLocaleString()} prompt chars</div>`);

    // ---- allowed / disallowed tools ----
    parts.push('<h3>Tools allowed</h3>');
    if (d.allowed_tools.length === 0){
      parts.push('<div class="meta-line">— all tools allowed (no allowlist) —</div>');
    } else {
      parts.push('<div>' + d.allowed_tools.map(t => `<span class="pill enabled">${escapeHtml(t)}</span>`).join('') + '</div>');
    }
    if (d.disallowed_tools.length){
      parts.push('<h3>Tools blocked</h3>');
      parts.push('<div>' + d.disallowed_tools.map(t => `<span class="pill disabled">${escapeHtml(t)}</span>`).join('') + '</div>');
    }

    // ---- MCP (real SDK servers) ----
    if (d.mcp_wired.length){
      parts.push('<h3>MCP servers (wired)</h3>');
      parts.push('<div>' + d.mcp_wired.map(s => `<span class="pill enabled">${escapeHtml(s)}</span>`).join('') + '</div>');
    }

    // ---- parent MCP notes ----
    if (d.mcp_notes.length){
      parts.push('<h3>Parent MCP (available)</h3>');
      parts.push(d.mcp_notes.map(m => `
        <div class="integration">
          <div class="name">${escapeHtml(m.name)}</div>
          <div class="vars">${escapeHtml((m.note || '').slice(0, 140))}</div>
        </div>`).join(''));
    }

    // ---- env-key HTTP integrations ----
    if (d.env_integrations.length){
      parts.push('<h3>HTTP API (env-key)</h3>');
      parts.push(d.env_integrations.map(m => `
        <div class="integration">
          <div class="name">${escapeHtml(m.name)}${m.api_base ? ' <span class="size">' + escapeHtml(m.api_base) + '</span>' : ''}</div>
          <div class="vars">${(m.env_vars || []).map(v => '$' + escapeHtml(v)).join(' · ')}</div>
        </div>`).join(''));
    }

    // ---- skills ----
    if (d.skills.length){
      parts.push('<h3>Skills</h3>');
      parts.push(d.skills.map(s => `
        <div class="row ${s.exists ? 'clickable' : 'missing'}" data-path="${s.exists ? escapeHtml(s.path) : ''}" data-title="${escapeHtml(s.ref)}">
          📎 <code>${escapeHtml(s.ref)}</code>
          <span class="size">${fmtBytes(s.size)}</span>
        </div>`).join(''));
    }

    // ---- layered per-agent files ----
    parts.push('<h3>System prompt — per-agent</h3>');
    parts.push(d.layered_files.map(f => `
      <div class="row ${f.exists ? 'clickable' : 'missing'}" data-path="${f.exists ? escapeHtml(f.path) : ''}" data-title="${escapeHtml(f.name)}">
        ${f.exists ? '📄' : '·'} <code>${escapeHtml(f.name)}</code>
        <span class="size">${fmtBytes(f.size)}</span>
      </div>`).join(''));

    // ---- shared files ----
    parts.push('<h3>System prompt — shared</h3>');
    parts.push(d.shared_files.map(f => `
      <div class="row ${f.exists ? 'clickable' : 'missing'}" data-path="${f.exists ? escapeHtml(f.path) : ''}" data-title="${escapeHtml(f.name)}">
        ${f.exists ? '📄' : '·'} <code>${escapeHtml(f.name)}</code>
        <span class="size">${fmtBytes(f.size)}</span>
      </div>`).join(''));

    infoBody.innerHTML = parts.join('');

    // Wire click handlers on every row that has a path.
    infoBody.querySelectorAll('.row.clickable').forEach(row => {
      row.addEventListener('click', () => {
        const p = row.dataset.path;
        const t = row.dataset.title;
        if (p) openFile(p, t);
      });
    });
  }

  // ---- file viewer ----
  const fv = document.getElementById('file-viewer');
  const fvTitle = document.getElementById('fv-title');
  const fvPath = document.getElementById('fv-path');
  const fvContent = document.getElementById('fv-content');
  const fvClose = document.getElementById('fv-close');

  async function openFile(path, title){
    fvTitle.textContent = title || path;
    fvPath.textContent = path;
    fvContent.textContent = 'loading…';
    fv.classList.add('open');
    try {
      const r = await fetch(`/api/chat/${agentName}/file?path=${encodeURIComponent(path)}`);
      if (!r.ok){
        const err = await r.json().catch(() => ({detail: r.statusText}));
        throw new Error(err.detail || ('HTTP ' + r.status));
      }
      const d = await r.json();
      fvContent.textContent = d.content || '(empty)';
    } catch(e){
      fvContent.textContent = '⚠️  ' + e.message;
    }
  }

  fvClose.addEventListener('click', () => fv.classList.remove('open'));
  fv.addEventListener('click', (e) => { if (e.target === fv) fv.classList.remove('open'); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && fv.classList.contains('open')){
      fv.classList.remove('open');
    }
  });

  // Info panel collapse (persist via localStorage).
  const INFO_KEY = 'agentos-info-collapsed:' + agentName;
  if (localStorage.getItem(INFO_KEY) === '1'){
    layout.classList.add('info-collapsed');
  }
  infoToggle.addEventListener('click', () => {
    const wide = window.innerWidth > 1100;
    if (wide){
      layout.classList.toggle('info-collapsed');
      localStorage.setItem(INFO_KEY, layout.classList.contains('info-collapsed') ? '1' : '0');
    } else {
      // Narrow screens: toggle the overlay mode instead.
      layout.classList.toggle('info-open');
    }
  });

  // ---- live events (SSE) + unread badges ----
  // Per-agent "last seen" lives in localStorage so badges persist across
  // reloads. Opening an agent's chat auto-marks it read.
  const LAST_SEEN_KEY = 'agentos-last-seen';
  function getLastSeen(){
    try { return JSON.parse(localStorage.getItem(LAST_SEEN_KEY) || '{}'); }
    catch(_){ return {}; }
  }
  function saveLastSeen(map){
    localStorage.setItem(LAST_SEEN_KEY, JSON.stringify(map));
  }
  function markRead(a){
    const map = getLastSeen();
    map[a] = new Date().toISOString();
    saveLastSeen(map);
    paintBadge(a, 0);
  }
  function paintBadge(name, count){
    document.querySelectorAll('.sb-badge[data-for="' + CSS.escape(name) + '"]').forEach(b => {
      if (count > 0){
        b.className = 'badge count';
        b.textContent = count > 99 ? '99+' : String(count);
      } else {
        b.className = 'sb-badge';
        b.textContent = '';
      }
    });
  }
  async function refreshBadges(){
    const map = getLastSeen();
    // Find the OLDEST "last seen" across all agents — that's our floor.
    // Then bucket server-side.
    const seens = Object.values(map);
    const floor = seens.length ? seens.sort()[0] : null;
    const url = '/api/chat/unread' + (floor ? ('?since=' + encodeURIComponent(floor)) : '');
    try {
      const r = await fetch(url);
      const d = await r.json();
      // For each agent, compute per-agent unread — events only count if
      // they're newer than THAT agent's last-seen.
      const sinceMap = {};
      for (const a in d.counts) sinceMap[a] = d.counts[a];
      // Server's counts are for the global floor; we need per-agent so
      // re-fetch per agent when an agent has non-zero and its last_seen
      // is newer than floor. Simpler: trust the global count as an upper
      // bound and let the SSE stream correct in real time as new events arrive.
      document.querySelectorAll('.sb-badge').forEach(b => {
        const a = b.dataset.for;
        // If this agent has been opened since floor, zero it out.
        if (map[a] && floor && map[a] > floor){
          paintBadge(a, 0);
        } else {
          paintBadge(a, sinceMap[a] || 0);
        }
      });
    } catch(_){}
  }
  function handleLiveEvent(ev){
    // Ignore our own operator prompts — they're not "unread" for us.
    if (ev.from === 'operator') return;
    const targetAgent = ev.agent;
    if (!targetAgent) return;
    // An agent replying in its own channel isn't unread either.
    if (ev.from === targetAgent && ev.type !== 'routed'){
      // But if THIS tab is viewing that agent, live-append the reply.
      if (targetAgent === agentName && ev.thread === activeThread){
        addMessage('assistant', ev.preview, ev.ts);
      }
      return;
    }
    // If THIS tab is looking at that agent's active thread, it's already "seen".
    if (targetAgent === agentName){
      // Auto-mark read so tabs don't keep incrementing while you watch.
      markRead(agentName);
      // Live-append any inbound traffic on the active thread: routed from
      // another agent, or a Discord-mirrored user message from the operator.
      if (ev.thread === activeThread){
        if (ev.type === 'routed'){
          addMessage('routed', ev.preview, ev.ts, {from: ev.from});
        } else if (ev.type === 'user'){
          addMessage('user', ev.preview, ev.ts);
        }
      }
      return;
    }
    // Bump the sidebar badge for the OTHER agent.
    const badge = document.querySelector('.sb-badge[data-for="' + CSS.escape(targetAgent) + '"]');
    const cur = badge ? parseInt(badge.textContent || '0', 10) : 0;
    paintBadge(targetAgent, (cur || 0) + 1);
    // Desktop notification when tab is hidden.
    if (document.visibilityState !== 'visible'
        && 'Notification' in window
        && Notification.permission === 'granted'){
      new Notification('@' + targetAgent + ' got a message', {
        body: (ev.from ? '📡 from @' + ev.from + ' · ' : '') + (ev.preview || ''),
        tag: 'agentos-route-' + targetAgent,
        icon: '/static/icon-192.png',
      });
    }
  }
  function connectSSE(){
    const es = new EventSource('/api/chat/events');
    es.onmessage = (m) => {
      try { handleLiveEvent(JSON.parse(m.data)); } catch(_){}
    };
    es.onerror = () => {
      // Auto-reconnect after a short backoff.
      es.close();
      setTimeout(connectSSE, 3000);
    };
  }

  // Opening this agent's chat = all caught up.
  markRead(agentName);
  refreshBadges();
  connectSSE();

  // Bootstrap: honour #t=<id> in the URL if present.
  const initial = readHash();
  if (initial) activeThread = initial;
  loadThreads(activeThread);
  loadInfo();
  input.focus();
})();
"""


def chat_page_html(agent: str, agents_list: list[dict], nav_html: str, css: str) -> str:
    sidebar_items = []
    for a in agents_list:
        cls = "active" if a["name"] == agent else ""
        hist_note = (f'<span class="agent-meta">{a["history_lines"]}</span>'
                     if a.get("history_lines") else '')
        sidebar_items.append(
            f'<a class="{cls}" data-agent="{esc(a["name"])}" href="/chat/{esc(a["name"])}">'
            f'@{esc(a["name"])}<span class="sb-badge" data-for="{esc(a["name"])}"></span>{hist_note}</a>'
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>@{esc(agent)} — Chat</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{css}{CHAT_CSS}</style></head>
<body>{nav_html}
<div class="wrap">
  <div class="chat-layout" id="chat-layout">
    <div class="chat-sidebar">
      <h3>Agents</h3>
      {''.join(sidebar_items)}
    </div>
    <div class="chat-main" id="chat-pane">
      <div class="chat-header">
        <h2>@{esc(agent)}</h2>
        <span class="meta" id="chat-thread-meta">loading threads…</span>
        <button id="chat-info-toggle" class="info-toggle" title="Toggle tools &amp; prompt files panel">ⓘ</button>
        <button id="chat-clear" title="Reset the current thread">reset</button>
      </div>
      <div id="chat-tabs" class="chat-tabs"></div>
      <div id="chat-messages" class="chat-messages"></div>
      <div id="chat-attach-chips" class="chat-attach-chips"></div>
      <div class="chat-input">
        <input type="file" id="chat-file" multiple style="display:none">
        <button id="chat-attach" class="chat-attach-btn" title="Attach file (images, audio, PDFs, code)">📎</button>
        <textarea id="chat-input" placeholder="Message @{esc(agent)}…  (Enter to send, Shift+Enter for newline, /help for commands)"></textarea>
        <button id="chat-send">Send</button>
      </div>
    </div>
    <aside class="chat-info" id="chat-info">
      <div id="chat-info-body">loading…</div>
    </aside>
  </div>
</div>
<div class="file-viewer" id="file-viewer">
  <div class="fv-body">
    <div class="fv-head">
      <h3 id="fv-title"></h3>
      <code id="fv-path"></code>
      <button id="fv-close">close</button>
    </div>
    <pre id="fv-content"></pre>
  </div>
</div>
<script>window.__AGENT_NAME__ = {json.dumps(agent)};</script>
<script>{CHAT_PAGE_JS}</script>
</body></html>"""


def chat_index_html(agents_list: list[dict], nav_html: str, css: str) -> str:
    if not agents_list:
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>Chat</title><style>{css}</style></head>
<body>{nav_html}<div class="wrap"><h1>Chat</h1><div class="panel meta">No agents configured.</div></div></body></html>"""
    cards = []
    for a in agents_list:
        cards.append(f"""
        <a class="stat" style="text-decoration:none;display:block;position:relative;" href="/chat/{esc(a['name'])}">
          <div class="label">agent</div>
          <div class="value" style="font-size:1.3em;">@{esc(a['name'])}<span class="sb-badge" data-for="{esc(a['name'])}" style="position:absolute;top:0.8em;right:0.8em;"></span></div>
          <div class="sub">{a['history_lines']} msgs · {a.get('thread_count', 1)} thread{'s' if a.get('thread_count',1)!=1 else ''}</div>
        </a>""")
    # Small inline script — same badge painting as the full chat page, but
    # without the per-agent marking (index is neutral, you haven't opened anything).
    js = r"""
    (function(){
      const LAST_SEEN_KEY = 'agentos-last-seen';
      function getMap(){ try { return JSON.parse(localStorage.getItem(LAST_SEEN_KEY)||'{}'); } catch(_){ return {}; } }
      function paint(name, count){
        document.querySelectorAll('.sb-badge[data-for="' + CSS.escape(name) + '"]').forEach(b => {
          if (count > 0){ b.className = 'badge count'; b.textContent = count > 99 ? '99+' : String(count); }
          else { b.className = 'sb-badge'; b.textContent = ''; }
        });
      }
      async function refresh(){
        const map = getMap();
        const seens = Object.values(map);
        const floor = seens.length ? seens.sort()[0] : null;
        const r = await fetch('/api/chat/unread' + (floor ? '?since=' + encodeURIComponent(floor) : ''));
        const d = await r.json();
        document.querySelectorAll('.sb-badge').forEach(b => {
          const a = b.dataset.for;
          if (map[a] && floor && map[a] > floor) paint(a, 0);
          else paint(a, (d.counts||{})[a] || 0);
        });
      }
      function connectSSE(){
        const es = new EventSource('/api/chat/events');
        es.onmessage = (m) => {
          try {
            const ev = JSON.parse(m.data);
            if (ev.from === 'operator') return;
            if (!ev.agent) return;
            // Agent replying in its own channel isn't unread either.
            if (ev.from === ev.agent && ev.type !== 'routed') return;
            const b = document.querySelector('.sb-badge[data-for="' + CSS.escape(ev.agent) + '"]');
            const cur = b ? parseInt(b.textContent || '0', 10) : 0;
            paint(ev.agent, (cur || 0) + 1);
          } catch(_){}
        };
        es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };
      }
      refresh();
      connectSSE();
    })();
    """
    css_inline = """
    .sb-badge{display:inline-block;}
    .badge{display:inline-block;min-width:0.55em;height:0.55em;border-radius:50%;background:var(--fail,#e66);vertical-align:middle;}
    .badge.count{min-width:1.4em;height:1.2em;line-height:1.2em;border-radius:0.7em;padding:0 0.45em;font-size:0.72em;color:#fff;font-weight:600;text-align:center;}
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Chat</title>
<style>{css}{css_inline}</style></head>
<body>{nav_html}
<div class="wrap">
  <h1>Chat</h1>
  <div class="meta">Pick an agent to talk to. Separate session from Discord — same memory, same identity. Red dot = new activity since you last opened that agent.</div>
  <br>
  <div class="grid cols-3">{''.join(cards)}</div>
</div>
<script>{js}</script>
</body></html>"""
