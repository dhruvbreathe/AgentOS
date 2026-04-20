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

Session key: "web:<agent>" — separate from Discord's channel-id keyed
session. Keeps web history clean from Discord history. Shared vault,
shared memory files, shared identity — just a distinct conversation
thread.
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


def _session_key(agent: str) -> str:
    return f"web:{agent}"


# ---- chat history (operator-visible, survives restarts) --------------------

def _history_path(agent: str) -> Path:
    return CHAT_HISTORY_DIR / f"{agent}.jsonl"


def _append_history(agent: str, role: str, content: str, meta: dict | None = None) -> None:
    p = _history_path(agent)
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


def _read_history(agent: str, limit: int = 200) -> list[dict]:
    p = _history_path(agent)
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


def _clear_history(agent: str) -> int:
    p = _history_path(agent)
    if not p.exists():
        return 0
    n = sum(1 for _ in p.open())
    p.unlink()
    return n


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


# ---- per-agent locks (one turn at a time per agent) ------------------------

_locks: dict[str, asyncio.Lock] = {}


def _lock(agent: str) -> asyncio.Lock:
    if agent not in _locks:
        _locks[agent] = asyncio.Lock()
    return _locks[agent]


# ---- background runner -----------------------------------------------------

async def _run_turn(agent_cfg, prompt: str, sink: WebSink) -> None:
    """Drive run_agent and push results into the sink. Session persists
    under the web:<agent> key so web history stays distinct from Discord."""
    sessions = _load_sessions()
    key = _session_key(agent_cfg.name)
    resume = sessions.get(key)

    lock = _lock(agent_cfg.name)
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
                            meta={"session_id": session_id})
        except Exception as e:
            msg = f"agent error: {e}"
            await sink.error(msg)
            _append_history(agent_cfg.name, "assistant", f"⚠️ {msg}",
                            meta={"error": True})


# ---- FastAPI router --------------------------------------------------------

router = APIRouter()


def _agent_map():
    # load_all_agents() is keyed by channel_id — rekey by agent name.
    by_channel = load_all_agents()
    return {cfg.name: cfg for cfg in by_channel.values()}


@router.get("/api/chat/agents")
def list_chat_agents() -> JSONResponse:
    agents = _agent_map()
    sessions = _load_sessions()
    return JSONResponse([
        {
            "name": name,
            "session_id": sessions.get(_session_key(name)),
            "history_lines": sum(1 for _ in _history_path(name).open())
                             if _history_path(name).exists() else 0,
        }
        for name in sorted(agents.keys())
    ])


@router.get("/api/chat/{agent}/history")
def chat_history(agent: str, limit: int = 200) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    return JSONResponse({"agent": agent, "history": _read_history(agent, limit)})


@router.post("/api/chat/{agent}/clear")
def chat_clear(agent: str) -> JSONResponse:
    if agent not in _agent_map():
        raise HTTPException(404, "no such agent")
    sessions = _load_sessions()
    sessions.pop(_session_key(agent), None)
    _save_sessions(sessions)
    removed = _clear_history(agent)
    return JSONResponse({"agent": agent, "cleared_lines": removed, "session_reset": True})


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
async def chat_stream(agent: str, request: Request) -> StreamingResponse:
    agents = _agent_map()
    if agent not in agents:
        raise HTTPException(404, "no such agent")

    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    attachments = body.get("attachments") or []
    if not prompt and not attachments:
        raise HTTPException(400, "empty prompt")

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
        _append_history(agent, "user", prompt, meta={"turn_id": turn_id, "slash": True})
        _append_history(agent, "assistant", slash_result["reply"], meta={"slash": True})

        async def slash_stream() -> AsyncIterator[bytes]:
            yield f"data: {json.dumps({'type': 'start', 'turn_id': turn_id})}\n\n".encode()
            yield f"data: {json.dumps({'type': 'final', 'text': slash_result['reply']})}\n\n".encode()
            yield b"data: {\"type\":\"end\"}\n\n"

        return StreamingResponse(slash_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    # Log the user prompt before kicking the turn so it survives crashes.
    _append_history(agent, "user", prompt, meta={"turn_id": turn_id})

    sink = WebSink()
    task = asyncio.create_task(_run_turn(agent_cfg, prompt, sink))
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
  .chat-layout{display:grid;grid-template-columns:240px 1fr;gap:1em;height:calc(100vh - 120px);min-height:500px;}
  .chat-sidebar{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:0.5em;overflow-y:auto;}
  .chat-sidebar h3{font-size:0.7em;text-transform:uppercase;letter-spacing:0.08em;color:var(--fg-mute);padding:0.5em 0.6em;margin:0;}
  .chat-sidebar a{display:block;padding:0.5em 0.6em;border-radius:6px;color:var(--fg-dim);text-decoration:none;font-size:0.9em;}
  .chat-sidebar a:hover{background:var(--panel-2);color:var(--fg);}
  .chat-sidebar a.active{background:var(--panel-2);color:var(--accent);font-weight:500;}
  .chat-sidebar .agent-meta{font-size:0.7em;color:var(--fg-mute);margin-left:0.8em;}
  .chat-main{display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden;}
  .chat-header{padding:0.8em 1.2em;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:1em;}
  .chat-header h2{margin:0;text-transform:none;letter-spacing:0;font-size:1.1em;color:var(--fg);}
  .chat-header .meta{flex:1;}
  .chat-header button{background:var(--panel-2);border:1px solid var(--border);color:var(--fg-dim);padding:0.3em 0.8em;border-radius:6px;cursor:pointer;font-size:0.8em;}
  .chat-header button:hover{color:var(--fg);border-color:var(--accent);}
  .chat-messages{flex:1;overflow-y:auto;padding:1em 1.2em;scroll-behavior:smooth;}
  .msg{margin-bottom:1.2em;max-width:90%;}
  .msg.user{margin-left:auto;}
  .msg .role{font-size:0.7em;text-transform:uppercase;letter-spacing:0.08em;color:var(--fg-mute);margin-bottom:0.3em;}
  .msg.user .role{text-align:right;color:var(--accent);}
  .msg.assistant .role{color:var(--accent-2);}
  .msg .bubble{padding:0.8em 1em;border-radius:12px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word;font-size:0.94em;}
  .msg.user .bubble{background:rgba(111,168,255,0.12);border:1px solid rgba(111,168,255,0.3);}
  .msg.assistant .bubble{background:var(--panel-2);border:1px solid var(--border);}
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
  @media (max-width:780px){
    .chat-layout{grid-template-columns:1fr;grid-template-rows:auto 1fr;}
    .chat-sidebar{max-height:120px;}
    .chat-sidebar a{display:inline-block;margin-right:0.3em;}
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
  let pendingAttachments = [];  // paths of uploaded files

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

  function addMessage(role, content, ts){
    const wrap = el('div', 'msg ' + role);
    wrap.appendChild(el('div', 'role', role));
    const bubble = el('div', 'bubble');
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
      const r = await fetch(`/api/chat/${agentName}/history?limit=200`);
      const data = await r.json();
      messages.innerHTML = '';
      for (const m of data.history){
        addMessage(m.role, m.content, m.ts);
      }
      if (data.history.length === 0){
        messages.innerHTML = '<div class="chat-empty">Start a conversation with @' + escapeHtml(agentName) + '.</div>';
      }
    } catch(e){
      messages.innerHTML = '<div class="chat-empty">Failed to load history: ' + escapeHtml(e.message) + '</div>';
    }
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
      const resp = await fetch(`/api/chat/${agentName}/stream`, {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({prompt, attachments}),
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
            tag: 'agentos-' + agentName,
          });
        }
      }
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
    if (!confirm('Reset session and clear chat history for @' + agentName + '?')) return;
    await fetch(`/api/chat/${agentName}/clear`, {method: 'POST'});
    await loadHistory();
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

  loadHistory();
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
            f'<a class="{cls}" href="/chat/{esc(a["name"])}">@{esc(a["name"])}{hist_note}</a>'
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>@{esc(agent)} — Chat</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{css}{CHAT_CSS}</style></head>
<body>{nav_html}
<div class="wrap">
  <div class="chat-layout">
    <div class="chat-sidebar">
      <h3>Agents</h3>
      {''.join(sidebar_items)}
    </div>
    <div class="chat-main" id="chat-pane">
      <div class="chat-header">
        <h2>@{esc(agent)}</h2>
        <span class="meta">direct chat</span>
        <button id="chat-clear">reset</button>
      </div>
      <div id="chat-messages" class="chat-messages"></div>
      <div id="chat-attach-chips" class="chat-attach-chips"></div>
      <div class="chat-input">
        <input type="file" id="chat-file" multiple style="display:none">
        <button id="chat-attach" class="chat-attach-btn" title="Attach file (images, audio, PDFs, code)">📎</button>
        <textarea id="chat-input" placeholder="Message @{esc(agent)}…  (Enter to send, Shift+Enter for newline, /help for commands)"></textarea>
        <button id="chat-send">Send</button>
      </div>
    </div>
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
        <a class="stat" style="text-decoration:none;display:block;" href="/chat/{esc(a['name'])}">
          <div class="label">agent</div>
          <div class="value" style="font-size:1.3em;">@{esc(a['name'])}</div>
          <div class="sub">{a['history_lines']} msgs in web history</div>
        </a>""")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Chat</title>
<style>{css}</style></head>
<body>{nav_html}
<div class="wrap">
  <h1>Chat</h1>
  <div class="meta">Pick an agent to talk to. Separate session from Discord — same memory, same identity.</div>
  <br>
  <div class="grid cols-3">{''.join(cards)}</div>
</div>
</body></html>"""
