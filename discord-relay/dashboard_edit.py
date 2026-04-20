"""dashboard_edit.py — agent config editor mounted into dashboard.py.

Surfaces every agent.yaml field as a form at /agents/<name>/edit, plus task
cron/kind editor + clone-agent + restart signal. Writes preserve comments
via ruamel.yaml round-trip.

Routes:
    GET  /agents/{name}/edit          — full editor HTML
    POST /api/agents/{name}/save      — write agent.yaml (JSON body)
    POST /api/agents/{name}/clone     — clone agent directory
    GET  /api/agents/{name}/tasks/{t} — task frontmatter + body
    POST /api/agents/{name}/tasks/{t} — update task (or create if new)
    POST /api/agents/{name}/tasks     — create new task
    DELETE /api/agents/{name}/tasks/{t} — delete task
    POST /api/restart                 — touch logs/.restart-requested

Gated fields — changes require {"confirm": true} in the save payload:
    name, channel_id, webhook_url_env, bot_token_env, extra_channel_ids
"""
from __future__ import annotations

import html as html_lib
import io
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

ROOT = Path(__file__).resolve().parent
AGENTS_DIR = ROOT / "agents"
SHARED_SKILLS = ROOT / "shared" / "skills"
LOGS_DIR = ROOT / "logs"
RESTART_FILE = LOGS_DIR / ".restart-requested"

router = APIRouter()

# ---- option catalogs (surfaced to the UI) ----------------------------------

KNOWN_MODELS = [
    ("", "inherit from config.yaml"),
    ("claude-opus-4-7", "opus 4.7 (latest, strategic)"),
    ("claude-opus-4-6", "opus 4.6"),
    ("claude-sonnet-4-6", "sonnet 4.6 (balanced)"),
    ("claude-haiku-4-5-20251001", "haiku 4.5 (fast, cheap)"),
    ("opus", "opus (alias)"),
    ("sonnet", "sonnet (alias)"),
    ("haiku", "haiku (alias)"),
    ("inherit", "inherit (alias)"),
]

KNOWN_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "Bash", "WebFetch", "WebSearch",
    "Task", "TodoWrite", "NotebookEdit", "BashOutput", "ExitPlanMode",
    "KillBash", "mcp__agent_comms__send_to_agent",
]

KNOWN_EFFORT = ["", "low", "medium", "high"]
KNOWN_PERMISSION_MODES = ["", "default", "acceptEdits", "bypassPermissions", "plan"]
KNOWN_THINKING = ["", "off", "adaptive", "enabled", "disabled"]
KNOWN_SETTING_SOURCES = ["user", "project", "local"]
KNOWN_TASK_KINDS = ["", "posted", "systemEvent"]

# Gated fields — require explicit confirm=true in save payload
GATED_FIELDS = {
    "name", "channel_id", "webhook_url_env", "bot_token_env",
    "extra_channel_ids",
}


# ---- yaml round-trip -------------------------------------------------------

def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 200
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _load_agent_yaml(path: Path) -> CommentedMap:
    if not path.exists():
        return CommentedMap()
    with path.open() as f:
        data = _yaml().load(f)
    return data if isinstance(data, CommentedMap) else CommentedMap(data or {})


def _dump_agent_yaml(data: Any, path: Path) -> None:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    # Prepend a "last edited" marker as a comment on the first line if not present
    text = buf.getvalue()
    marker = f"# edited via dashboard at {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
    # Only prepend if not already followed by an edit marker
    if text.startswith("# edited via dashboard"):
        # Replace the existing marker line
        lines = text.split("\n", 1)
        text = marker + (lines[1] if len(lines) > 1 else "")
    else:
        text = marker + text
    path.write_text(text)


# ---- option-list helpers ---------------------------------------------------

def _list_shared_skills() -> list[dict]:
    out: list[dict] = []
    if not SHARED_SKILLS.exists():
        return out
    for d in sorted(SHARED_SKILLS.iterdir()):
        if d.is_dir() and (d / "SKILL.md").exists():
            desc = _first_line_desc(d / "SKILL.md")
            out.append({"ref": f"skill:{d.name}", "kind": "shared", "desc": desc})
    return out


def _list_local_skills(agent: str) -> list[dict]:
    skills_dir = AGENTS_DIR / agent / "skills"
    out: list[dict] = []
    if not skills_dir.exists():
        return out
    for d in sorted(skills_dir.iterdir()):
        if d.is_dir() and (d / "SKILL.md").exists():
            desc = _first_line_desc(d / "SKILL.md")
            out.append({"ref": f"local:{d.name}", "kind": "local", "desc": desc})
    return out


def _first_line_desc(path: Path) -> str:
    try:
        txt = path.read_text()
        m = re.search(r"^description:\s*(.+)$", txt, re.MULTILINE)
        if m:
            return m.group(1).strip().strip("'\"")[:140]
        body = re.sub(r"^---.*?---\s*", "", txt, count=1, flags=re.DOTALL)
        body = re.sub(r"^#\s+.+\n+", "", body, count=1)
        return body.strip().split("\n", 1)[0][:140]
    except Exception:
        return ""


def _list_agents() -> list[str]:
    if not AGENTS_DIR.exists():
        return []
    return sorted(
        d.name for d in AGENTS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_"))
        and (d / "agent.yaml").exists()
    )


# ---- task frontmatter helpers ----------------------------------------------

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _parse_task_file(path: Path) -> tuple[dict, str]:
    if not path.exists():
        return {}, ""
    text = path.read_text()
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text
    meta_src, body = m.group(1), m.group(2)
    try:
        meta = _yaml().load(io.StringIO(meta_src)) or {}
    except Exception:
        meta = {}
    return dict(meta), body


def _write_task_file(path: Path, meta: dict, body: str) -> None:
    # Clean meta: drop empty-string values
    meta = {k: v for k, v in meta.items() if v not in (None, "", [])}
    buf = io.StringIO()
    _yaml().dump(meta, buf)
    front = buf.getvalue().rstrip()
    out = f"---\n{front}\n---\n{body.lstrip()}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(out)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower()).strip("_")
    return s[:60] or "task"


# ---- signal restart --------------------------------------------------------

def _signal_restart() -> str:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    RESTART_FILE.touch()
    return str(RESTART_FILE)


# ---- gated-field diff ------------------------------------------------------

def _find_gated_changes(before: dict, after: dict) -> list[str]:
    changed: list[str] = []
    for k in GATED_FIELDS:
        b, a = before.get(k), after.get(k)
        if b != a:
            changed.append(k)
    return changed


# ---- routes ---------------------------------------------------------------

@router.get("/agents/{name}/edit", response_class=HTMLResponse)
def edit_agent_page(name: str):
    if name not in _list_agents():
        raise HTTPException(404, f"no agent named {name}")
    return HTMLResponse(_edit_page_html(name))


@router.get("/api/agents/{name}/raw")
def api_agent_raw(name: str):
    path = AGENTS_DIR / name / "agent.yaml"
    if not path.exists():
        raise HTTPException(404, "no such agent")
    cfg = _load_agent_yaml(path)
    # Convert ruamel CommentedMap → plain dict for JSON
    return JSONResponse(json.loads(json.dumps(_to_plain(cfg))))


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, CommentedMap):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(i) for i in obj]
    return obj


class SavePayload(BaseModel):
    config: dict
    confirm: bool = False
    restart: bool = True


@router.post("/api/agents/{name}/save")
def api_agent_save(name: str, payload: SavePayload):
    path = AGENTS_DIR / name / "agent.yaml"
    if not path.exists():
        raise HTTPException(404, "no such agent")

    before = _to_plain(_load_agent_yaml(path))
    after = payload.config or {}

    # Validate types
    errors = _validate_config(after)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)

    gated = _find_gated_changes(before, after)
    if gated and not payload.confirm:
        return JSONResponse({
            "ok": False,
            "needs_confirm": True,
            "gated_changes": gated,
            "message": (
                f"Changes to {', '.join(gated)} are gated. One typo can brick the agent's "
                f"Discord routing. Re-submit with confirm=true to apply."
            ),
        }, status_code=409)

    # Round-trip: load, overwrite top-level keys, dump.
    doc = _load_agent_yaml(path)
    # Reset mapping by removing keys no longer in `after`, then set all from `after`.
    for key in list(doc.keys()):
        if key not in after:
            del doc[key]
    for k, v in after.items():
        doc[k] = _to_commented(v)

    _dump_agent_yaml(doc, path)

    signaled = False
    if payload.restart:
        _signal_restart()
        signaled = True

    return {
        "ok": True,
        "path": str(path),
        "gated_changes": gated,
        "restart_signaled": signaled,
    }


def _to_commented(v: Any) -> Any:
    """Convert plain JSON → ruamel-friendly for embedding in a CommentedMap."""
    if isinstance(v, dict):
        cm = CommentedMap()
        for k, vv in v.items():
            cm[k] = _to_commented(vv)
        return cm
    if isinstance(v, list):
        return [_to_commented(i) for i in v]
    return v


def _validate_config(cfg: dict) -> list[str]:
    errors: list[str] = []
    if "name" in cfg and not re.match(r"^[a-z][a-z0-9_-]{1,40}$", str(cfg["name"] or "")):
        errors.append("name must be lowercase alphanumeric + dashes, 2-41 chars")
    if "channel_id" in cfg and cfg["channel_id"] and not str(cfg["channel_id"]).isdigit():
        errors.append("channel_id must be a numeric Discord snowflake string")
    if "max_turns" in cfg and cfg["max_turns"] not in (None, ""):
        try:
            n = int(cfg["max_turns"])
            if n < 1 or n > 200:
                errors.append("max_turns must be 1..200")
        except (TypeError, ValueError):
            errors.append("max_turns must be an integer")
    if "max_hops" in cfg and cfg["max_hops"] not in (None, ""):
        try:
            n = int(cfg["max_hops"])
            if n < 1 or n > 10:
                errors.append("max_hops must be 1..10")
        except (TypeError, ValueError):
            errors.append("max_hops must be an integer")
    for listkey in ("allowed_tools", "disallowed_tools", "skills", "add_dirs",
                    "env_passthrough", "setting_sources", "extra_channel_ids"):
        if listkey in cfg and cfg[listkey] is not None and not isinstance(cfg[listkey], list):
            errors.append(f"{listkey} must be a list")
    for dictkey in ("subagents", "mcp_servers", "approval", "env", "sandbox"):
        if dictkey in cfg and cfg[dictkey] is not None and not isinstance(cfg[dictkey], dict):
            errors.append(f"{dictkey} must be an object")
    return errors


class ClonePayload(BaseModel):
    new_name: str
    new_channel_id: str = ""
    new_webhook_env: str = ""
    new_bot_token_env: str = ""


@router.post("/api/agents/{name}/clone")
def api_agent_clone(name: str, payload: ClonePayload):
    src = AGENTS_DIR / name
    if not src.exists():
        raise HTTPException(404, "no such agent")
    nn = payload.new_name.strip()
    if not re.match(r"^[a-z][a-z0-9_-]{1,40}$", nn):
        raise HTTPException(400, "new_name must be lowercase alphanumeric + dashes, 2-41 chars")
    dst = AGENTS_DIR / nn
    if dst.exists():
        raise HTTPException(409, f"agent {nn} already exists")

    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        "ActiveTasks", "memory", "__pycache__"
    ))
    # Reset the new agent.yaml with overrides
    cfg_path = dst / "agent.yaml"
    cfg = _load_agent_yaml(cfg_path)
    cfg["name"] = nn
    cfg["channel_id"] = payload.new_channel_id or "REPLACE_WITH_CHANNEL_ID"
    if payload.new_webhook_env:
        cfg["webhook_url_env"] = payload.new_webhook_env
    if payload.new_bot_token_env:
        cfg["bot_token_env"] = payload.new_bot_token_env
    # Strip subagents — they're almost always parent-specific
    if "subagents" in cfg:
        del cfg["subagents"]
    _dump_agent_yaml(cfg, cfg_path)
    # Scaffold memory + ActiveTasks dirs
    (dst / "memory").mkdir(exist_ok=True)
    (dst / "ActiveTasks").mkdir(exist_ok=True)

    return {"ok": True, "name": nn, "path": str(dst),
            "needs_webhook": not payload.new_webhook_env,
            "needs_channel": not payload.new_channel_id}


@router.get("/api/agents/{name}/tasks/{task}")
def api_task_get(name: str, task: str):
    path = AGENTS_DIR / name / "tasks" / f"{task}.md"
    if not path.exists():
        raise HTTPException(404, "no such task")
    meta, body = _parse_task_file(path)
    return {"name": task, "meta": meta, "body": body, "path": str(path)}


class TaskPayload(BaseModel):
    meta: dict = {}
    body: str = ""


@router.post("/api/agents/{name}/tasks/{task}")
def api_task_save(name: str, task: str, payload: TaskPayload):
    task_safe = _slugify(task)
    path = AGENTS_DIR / name / "tasks" / f"{task_safe}.md"
    _write_task_file(path, payload.meta, payload.body)
    return {"ok": True, "path": str(path), "name": task_safe}


@router.delete("/api/agents/{name}/tasks/{task}")
def api_task_delete(name: str, task: str):
    path = AGENTS_DIR / name / "tasks" / f"{task}.md"
    if not path.exists():
        raise HTTPException(404, "no such task")
    path.unlink()
    return {"ok": True, "deleted": str(path)}


@router.get("/api/agents/{name}/tasks")
def api_tasks_list(name: str):
    tdir = AGENTS_DIR / name / "tasks"
    if not tdir.exists():
        return {"tasks": []}
    out: list[dict] = []
    for p in sorted(tdir.glob("*.md")):
        meta, _ = _parse_task_file(p)
        out.append({"name": p.stem, "meta": meta})
    return {"tasks": out}


@router.get("/api/agents/{name}/options")
def api_options(name: str):
    """Skills, tools, models, everything needed to populate selects."""
    if name not in _list_agents():
        raise HTTPException(404, "no such agent")
    return {
        "models": [{"value": v, "label": l} for v, l in KNOWN_MODELS],
        "tools": KNOWN_TOOLS,
        "effort": KNOWN_EFFORT,
        "permission_modes": KNOWN_PERMISSION_MODES,
        "thinking": KNOWN_THINKING,
        "setting_sources": KNOWN_SETTING_SOURCES,
        "task_kinds": KNOWN_TASK_KINDS,
        "skills": _list_shared_skills() + _list_local_skills(name),
        "agents": _list_agents(),
    }


@router.post("/api/restart")
def api_restart():
    path = _signal_restart()
    return {"ok": True, "signaled": path}


# ---- HTML page ------------------------------------------------------------

def _edit_page_html(name: str) -> str:
    esc = html_lib.escape
    # Borrow the parent app's CSS / nav to keep visual continuity.
    css = EDIT_CSS
    js = EDIT_JS
    return f"""<!doctype html>
<html><head><title>edit {esc(name)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{css}</style></head>
<body>
<nav>
  <a href="/">← overview</a>
  <a href="/agents/{esc(name)}">detail</a>
  <a href="/chat/{esc(name)}">chat</a>
  <span style="flex:1"></span>
  <span class="dim">editing <code>agents/{esc(name)}/agent.yaml</code></span>
</nav>
<div class="wrap">
  <div class="header">
    <h1>{esc(name)} <span class="dim">config</span></h1>
    <div class="btn-row">
      <button id="reloadBtn" class="btn">Reload</button>
      <button id="cloneBtn" class="btn">Clone agent…</button>
      <button id="restartBtn" class="btn">Queue restart</button>
      <button id="saveBtn" class="btn primary">Save &amp; restart</button>
    </div>
  </div>
  <div id="toast" class="toast"></div>

  <details open><summary>Identity &amp; routing <span class="dim">(gated)</span></summary>
    <div class="grid">
      <label>name<input id="f-name"></label>
      <label>channel_id<input id="f-channel_id" placeholder="discord snowflake"></label>
      <label>webhook_url_env<input id="f-webhook_url_env"></label>
      <label>bot_token_env<input id="f-bot_token_env" placeholder="(blank → shared bot)"></label>
      <label class="col-2">extra_channel_ids (comma-separated)<input id="f-extra_channel_ids"></label>
      <label>system_prompt_file<input id="f-system_prompt_file" placeholder="(blank → use layered files)"></label>
      <label>allow_bots <select id="f-allow_bots"><option value="true">true</option><option value="false">false</option></select></label>
    </div>
  </details>

  <details open><summary>Model &amp; behaviour</summary>
    <div class="grid">
      <label>model<select id="f-model"></select></label>
      <label>fallback_model<select id="f-fallback_model"></select></label>
      <label>effort<select id="f-effort"></select></label>
      <label>thinking<select id="f-thinking"></select></label>
      <label>permission_mode<select id="f-permission_mode"></select></label>
      <label>max_turns<input id="f-max_turns" type="number" min="1" max="200"></label>
      <label>max_hops<input id="f-max_hops" type="number" min="1" max="10"></label>
      <label>setting_sources<input id="f-setting_sources" placeholder="comma-separated: project,user,local"></label>
    </div>
  </details>

  <details open><summary>Tools</summary>
    <div class="panel">
      <div class="dim">allowed_tools — extras on top of config.yaml defaults</div>
      <div id="f-allowed_tools" class="tool-grid"></div>
      <div class="dim" style="margin-top:1em;">disallowed_tools</div>
      <div id="f-disallowed_tools" class="tool-grid"></div>
    </div>
  </details>

  <details open><summary>Skills</summary>
    <div class="panel">
      <div class="dim" style="margin-bottom:0.6em;">Toggle to enable a skill. Shared come from <code>shared/skills/</code>; local from <code>agents/{esc(name)}/skills/</code>.</div>
      <div id="f-skills" class="tool-grid"></div>
    </div>
  </details>

  <details><summary>add_dirs &amp; env</summary>
    <div class="grid">
      <label class="col-2">add_dirs (one per line)
        <textarea id="f-add_dirs" rows="3"></textarea>
      </label>
      <label class="col-2">env_passthrough (comma-separated env var names)
        <input id="f-env_passthrough">
      </label>
      <label class="col-2">env (JSON object — literal key/value pairs)
        <textarea id="f-env" rows="3" placeholder='{{}}'></textarea>
      </label>
    </div>
  </details>

  <details><summary>Approval gate</summary>
    <div class="grid">
      <label>enabled<select id="f-approval_enabled"><option value="inherit">inherit</option><option value="true">true</option><option value="false">false</option></select></label>
      <label>timeout_seconds<input id="f-approval_timeout" type="number" placeholder="inherit"></label>
      <label>poll_interval_seconds<input id="f-approval_poll" type="number" step="0.1" placeholder="inherit"></label>
      <label class="col-2">extra dangerous_patterns (one per line — appended to defaults)
        <textarea id="f-approval_patterns" rows="3"></textarea>
      </label>
    </div>
  </details>

  <details><summary>Subagents</summary>
    <div id="subagents-wrap"></div>
    <button id="addSubBtn" class="btn">+ Add subagent</button>
  </details>

  <details><summary>MCP servers</summary>
    <div class="panel">
      <div class="dim" style="margin-bottom:0.6em;">Edit as JSON. Note-only entries (no command/url/instance) surface as "integrations available" hints in the system prompt.</div>
      <textarea id="f-mcp_servers" rows="6" style="width:100%" placeholder='{{}}'></textarea>
    </div>
  </details>

  <details><summary>Tasks (scheduled)</summary>
    <div id="tasks-wrap"></div>
    <button id="addTaskBtn" class="btn">+ New task</button>
  </details>

  <details><summary>Raw YAML (read-only preview)</summary>
    <pre id="raw-preview" class="panel">loading…</pre>
  </details>
</div>

<div id="cloneModal" class="modal hidden">
  <div class="modal-body">
    <h2>Clone <code>{esc(name)}</code> → new agent</h2>
    <label>new_name<input id="clone-name" placeholder="e.g. marketing-v2"></label>
    <label>channel_id<input id="clone-channel" placeholder="(optional — can fill later)"></label>
    <label>webhook_url_env<input id="clone-webhook" placeholder="e.g. MARKETING_V2_WEBHOOK_URL"></label>
    <label>bot_token_env<input id="clone-token" placeholder="(optional)"></label>
    <div class="btn-row">
      <button id="cloneCancel" class="btn">Cancel</button>
      <button id="cloneSubmit" class="btn primary">Clone</button>
    </div>
  </div>
</div>

<script>const AGENT_NAME = {json.dumps(name)};</script>
<script>{js}</script>
</body></html>
"""


# ---- CSS + JS (kept at bottom to stay out of the way) ---------------------

EDIT_CSS = """
  :root {
    --bg:#0e0e10; --panel:#18181b; --panel-2:#1f1f23;
    --fg:#e4e4e7; --fg-dim:#a1a1aa; --fg-mute:#71717a;
    --accent:#6fa8ff; --accent-2:#9d7cff; --ok:#4ade80;
    --warn:#fbbf24; --fail:#f87171; --border:#27272a;
  }
  *{box-sizing:border-box;}
  body{font-family:-apple-system,"SF Pro Text","Inter",sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:0;}
  nav{display:flex;gap:1.5em;padding:1em 1.5em;border-bottom:1px solid var(--border);background:var(--panel);align-items:center;}
  nav a{color:var(--fg-dim);font-size:0.9em;text-decoration:none;}
  nav a:hover{color:var(--accent);}
  .wrap{max-width:1100px;margin:0 auto;padding:1.5em;}
  h1{font-size:1.4em;margin:0;}
  h2{font-size:1em;margin:0 0 0.8em;}
  code,pre{font-family:"SF Mono","JetBrains Mono",monospace;font-size:0.85em;}
  code{background:var(--panel-2);padding:0.1em 0.4em;border-radius:4px;}
  .header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1em;}
  .dim{color:var(--fg-mute);font-size:0.85em;}
  .btn-row{display:flex;gap:0.5em;flex-wrap:wrap;}
  .btn{background:var(--panel-2);color:var(--fg);border:1px solid var(--border);border-radius:6px;padding:0.55em 1em;cursor:pointer;font-size:0.88em;}
  .btn:hover{border-color:var(--accent);}
  .btn.primary{background:var(--accent);color:#0e0e10;border-color:var(--accent);font-weight:600;}
  .btn.danger{background:var(--fail);color:#0e0e10;border-color:var(--fail);}
  details{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:0.8em 1.2em;margin-bottom:0.8em;}
  details summary{cursor:pointer;font-size:0.95em;font-weight:600;color:var(--fg-dim);padding:0.2em 0;}
  details[open] summary{color:var(--fg);}
  details>summary+*{margin-top:0.8em;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:0.8em;}
  .grid .col-2{grid-column:span 2;}
  @media (max-width:700px){.grid{grid-template-columns:1fr;}.grid .col-2{grid-column:span 1;}}
  label{display:flex;flex-direction:column;gap:0.3em;font-size:0.82em;color:var(--fg-dim);}
  input,select,textarea{background:var(--panel-2);color:var(--fg);border:1px solid var(--border);border-radius:5px;padding:0.5em 0.7em;font-size:0.9em;font-family:inherit;}
  input:focus,select:focus,textarea:focus{border-color:var(--accent);outline:none;}
  textarea{font-family:"SF Mono","JetBrains Mono",monospace;font-size:0.85em;}
  .panel{background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:0.8em 1em;}
  .tool-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:0.3em;}
  .tool-grid label{flex-direction:row;align-items:center;gap:0.5em;color:var(--fg);font-size:0.85em;background:var(--panel);padding:0.4em 0.7em;border-radius:5px;border:1px solid var(--border);cursor:pointer;}
  .tool-grid label:hover{border-color:var(--accent);}
  .tool-grid input[type=checkbox]{margin:0;}
  .sub-row{background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:0.8em;margin-bottom:0.6em;display:grid;grid-template-columns:1fr 1fr auto;gap:0.5em;}
  .sub-row input,.sub-row select,.sub-row textarea{width:100%;}
  .sub-row .sub-name{font-weight:600;}
  .sub-row textarea{grid-column:span 3;}
  .sub-row .sub-full{grid-column:span 3;display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:0.5em;}
  .task-row{background:var(--panel-2);border:1px solid var(--border);border-radius:6px;padding:0.8em;margin-bottom:0.6em;display:grid;grid-template-columns:2fr 1.5fr 1fr auto;gap:0.5em;align-items:center;}
  .task-row .task-body{grid-column:span 4;display:none;margin-top:0.5em;}
  .task-row.open .task-body{display:block;}
  .task-row textarea{width:100%;}
  .toast{position:fixed;top:1em;right:1em;background:var(--panel);border:1px solid var(--border);padding:0.8em 1.2em;border-radius:6px;z-index:1000;display:none;max-width:420px;}
  .toast.ok{border-color:var(--ok);color:var(--ok);}
  .toast.warn{border-color:var(--warn);color:var(--warn);}
  .toast.fail{border-color:var(--fail);color:var(--fail);}
  .modal{position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:2000;}
  .modal.hidden{display:none;}
  .modal-body{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:1.5em;width:min(500px,90vw);}
  .modal label{margin-top:0.8em;}
  .modal .btn-row{margin-top:1.2em;justify-content:flex-end;}
"""

EDIT_JS = r"""
(() => {
  const $ = (id) => document.getElementById(id);
  const state = { cfg: null, options: null };

  function toast(msg, kind='ok', ms=3500) {
    const t = $('toast');
    t.textContent = msg;
    t.className = 'toast ' + kind;
    t.style.display = 'block';
    clearTimeout(t._tmr);
    t._tmr = setTimeout(() => t.style.display = 'none', ms);
  }

  async function loadOptions() {
    const r = await fetch(`/api/agents/${AGENT_NAME}/options`);
    state.options = await r.json();
    // model dropdowns
    for (const id of ['f-model', 'f-fallback_model']) {
      const sel = $(id);
      sel.innerHTML = '';
      for (const m of state.options.models) {
        const o = document.createElement('option');
        o.value = m.value; o.textContent = m.label;
        sel.appendChild(o);
      }
    }
    // effort / thinking / permission_mode
    for (const [id, list] of [
      ['f-effort', state.options.effort],
      ['f-thinking', state.options.thinking],
      ['f-permission_mode', state.options.permission_modes],
    ]) {
      const sel = $(id);
      sel.innerHTML = '';
      for (const v of list) {
        const o = document.createElement('option');
        o.value = v; o.textContent = v || '(inherit)';
        sel.appendChild(o);
      }
    }
    // tool grids
    for (const [id] of [['f-allowed_tools'], ['f-disallowed_tools']]) {
      const g = $(id); g.innerHTML = '';
      for (const t of state.options.tools) {
        const l = document.createElement('label');
        l.innerHTML = `<input type="checkbox" data-tool="${t}"><span>${t}</span>`;
        g.appendChild(l);
      }
    }
    // skills grid
    const sg = $('f-skills'); sg.innerHTML = '';
    for (const sk of state.options.skills) {
      const l = document.createElement('label');
      l.title = sk.desc || '';
      l.innerHTML = `<input type="checkbox" data-skill="${sk.ref}"><span>${sk.ref} <span class="dim">(${sk.kind})</span></span>`;
      sg.appendChild(l);
    }
  }

  async function loadConfig() {
    const r = await fetch(`/api/agents/${AGENT_NAME}/raw`);
    state.cfg = await r.json();
    fillForm(state.cfg);
    loadRaw();
    loadTasks();
  }

  function fillForm(c) {
    $('f-name').value = c.name || '';
    $('f-channel_id').value = c.channel_id || '';
    $('f-webhook_url_env').value = c.webhook_url_env || '';
    $('f-bot_token_env').value = c.bot_token_env || '';
    $('f-extra_channel_ids').value = (c.extra_channel_ids || []).join(', ');
    $('f-system_prompt_file').value = c.system_prompt_file ?? '';
    $('f-allow_bots').value = c.allow_bots === false ? 'false' : 'true';
    setSelect('f-model', c.model || '');
    setSelect('f-fallback_model', c.fallback_model || '');
    setSelect('f-effort', c.effort || '');
    setSelect('f-thinking', typeof c.thinking === 'object' ? (c.thinking?.type||'') : (c.thinking||''));
    setSelect('f-permission_mode', c.permission_mode || '');
    $('f-max_turns').value = c.max_turns ?? '';
    $('f-max_hops').value = c.max_hops ?? '';
    $('f-setting_sources').value = (c.setting_sources || []).join(', ');
    // tool checkboxes
    for (const cb of document.querySelectorAll('#f-allowed_tools input')) {
      cb.checked = (c.allowed_tools || []).includes(cb.dataset.tool);
    }
    for (const cb of document.querySelectorAll('#f-disallowed_tools input')) {
      cb.checked = (c.disallowed_tools || []).includes(cb.dataset.tool);
    }
    // skills
    for (const cb of document.querySelectorAll('#f-skills input')) {
      cb.checked = (c.skills || []).includes(cb.dataset.skill);
    }
    // textareas
    $('f-add_dirs').value = (c.add_dirs || []).join('\n');
    $('f-env_passthrough').value = (c.env_passthrough || []).join(', ');
    $('f-env').value = c.env ? JSON.stringify(c.env, null, 2) : '';
    // approval
    const ap = c.approval || {};
    $('f-approval_enabled').value = ap.enabled === undefined ? 'inherit' : String(ap.enabled);
    $('f-approval_timeout').value = ap.timeout_seconds ?? '';
    $('f-approval_poll').value = ap.poll_interval_seconds ?? '';
    $('f-approval_patterns').value = (ap.dangerous_patterns || []).join('\n');
    // mcp
    $('f-mcp_servers').value = c.mcp_servers && Object.keys(c.mcp_servers).length
      ? JSON.stringify(c.mcp_servers, null, 2) : '';
    // subagents
    renderSubs(c.subagents || {});
  }

  function setSelect(id, val) {
    const sel = $(id);
    const has = Array.from(sel.options).some(o => o.value === val);
    if (!has && val) {
      const o = document.createElement('option');
      o.value = val; o.textContent = val + ' (custom)';
      sel.appendChild(o);
    }
    sel.value = val;
  }

  function renderSubs(subs) {
    const wrap = $('subagents-wrap');
    wrap.innerHTML = '';
    for (const [name, body] of Object.entries(subs)) {
      wrap.appendChild(buildSubRow(name, body || {}));
    }
  }

  function buildSubRow(name, body) {
    const row = document.createElement('div');
    row.className = 'sub-row';
    row.innerHTML = `
      <div class="sub-full">
        <label>name<input class="sub-name" value="${escAttr(name)}"></label>
        <label>model<input class="sub-model" value="${escAttr(body.model||'')}" placeholder="inherit"></label>
        <label>max_turns<input class="sub-turns" type="number" value="${body.max_turns ?? ''}"></label>
        <button class="btn danger sub-del">×</button>
      </div>
      <label class="col-2">description<input class="sub-desc" value="${escAttr(body.description||'')}"></label>
      <label class="col-2">tools (comma-separated)<input class="sub-tools" value="${escAttr((body.tools||[]).join(', '))}"></label>
      <textarea class="sub-prompt" rows="3" placeholder="prompt…">${escHtml(body.prompt||'')}</textarea>
    `;
    row.querySelector('.sub-del').onclick = () => row.remove();
    return row;
  }

  function escAttr(s){ return String(s ?? '').replace(/"/g,'&quot;'); }
  function escHtml(s){ return String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

  function collectSubs() {
    const out = {};
    for (const row of document.querySelectorAll('.sub-row')) {
      const name = row.querySelector('.sub-name').value.trim();
      if (!name) continue;
      const body = {};
      const desc = row.querySelector('.sub-desc').value.trim();
      if (desc) body.description = desc;
      const model = row.querySelector('.sub-model').value.trim();
      if (model) body.model = model;
      const tools = row.querySelector('.sub-tools').value.split(',').map(s=>s.trim()).filter(Boolean);
      if (tools.length) body.tools = tools;
      const mt = row.querySelector('.sub-turns').value.trim();
      if (mt) body.max_turns = parseInt(mt, 10);
      const prompt = row.querySelector('.sub-prompt').value;
      if (prompt.trim()) body.prompt = prompt;
      out[name] = body;
    }
    return out;
  }

  function collectForm() {
    const c = {};
    const s = id => $(id).value.trim();
    c.name = s('f-name');
    c.channel_id = s('f-channel_id');
    if (s('f-webhook_url_env')) c.webhook_url_env = s('f-webhook_url_env');
    if (s('f-bot_token_env')) c.bot_token_env = s('f-bot_token_env');
    const extras = s('f-extra_channel_ids').split(',').map(x=>x.trim()).filter(Boolean);
    if (extras.length) c.extra_channel_ids = extras;
    c.system_prompt_file = s('f-system_prompt_file');
    c.allow_bots = $('f-allow_bots').value === 'true';

    if (s('f-model')) c.model = s('f-model');
    if (s('f-fallback_model')) c.fallback_model = s('f-fallback_model');
    if (s('f-effort')) c.effort = s('f-effort');
    if (s('f-thinking')) c.thinking = s('f-thinking');
    if (s('f-permission_mode')) c.permission_mode = s('f-permission_mode');
    if (s('f-max_turns')) c.max_turns = parseInt(s('f-max_turns'), 10);
    if (s('f-max_hops')) c.max_hops = parseInt(s('f-max_hops'), 10);
    const ss = s('f-setting_sources').split(',').map(x=>x.trim()).filter(Boolean);
    if (ss.length) c.setting_sources = ss;

    c.allowed_tools = Array.from(document.querySelectorAll('#f-allowed_tools input:checked')).map(cb=>cb.dataset.tool);
    c.disallowed_tools = Array.from(document.querySelectorAll('#f-disallowed_tools input:checked')).map(cb=>cb.dataset.tool);
    c.skills = Array.from(document.querySelectorAll('#f-skills input:checked')).map(cb=>cb.dataset.skill);

    const dirs = s('f-add_dirs').split('\n').map(x=>x.trim()).filter(Boolean);
    if (dirs.length) c.add_dirs = dirs;
    const envp = s('f-env_passthrough').split(',').map(x=>x.trim()).filter(Boolean);
    if (envp.length) c.env_passthrough = envp;
    const envRaw = s('f-env');
    if (envRaw) {
      try { c.env = JSON.parse(envRaw); }
      catch(e) { throw new Error('env must be valid JSON: '+e.message); }
    }

    const ap = {};
    const apEn = $('f-approval_enabled').value;
    if (apEn !== 'inherit') ap.enabled = apEn === 'true';
    if (s('f-approval_timeout')) ap.timeout_seconds = parseFloat(s('f-approval_timeout'));
    if (s('f-approval_poll')) ap.poll_interval_seconds = parseFloat(s('f-approval_poll'));
    const pats = s('f-approval_patterns').split('\n').map(x=>x.trim()).filter(Boolean);
    if (pats.length) ap.dangerous_patterns = pats;
    if (Object.keys(ap).length) c.approval = ap;

    const mcpRaw = s('f-mcp_servers');
    if (mcpRaw) {
      try { c.mcp_servers = JSON.parse(mcpRaw); }
      catch(e) { throw new Error('mcp_servers must be valid JSON: '+e.message); }
    } else {
      c.mcp_servers = {};
    }

    const subs = collectSubs();
    if (Object.keys(subs).length) c.subagents = subs;

    return c;
  }

  async function save(confirmOverride=false) {
    let cfg;
    try { cfg = collectForm(); } catch(e) { toast(e.message, 'fail', 6000); return; }
    const res = await fetch(`/api/agents/${AGENT_NAME}/save`, {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({config: cfg, confirm: confirmOverride, restart: true}),
    });
    const j = await res.json();
    if (res.status === 409 && j.needs_confirm) {
      if (confirm(`Gated change to: ${j.gated_changes.join(', ')}\n\n${j.message}\n\nProceed?`)) {
        return save(true);
      }
      return;
    }
    if (!res.ok) {
      toast('save failed: ' + (j.errors ? j.errors.join('; ') : (j.detail || res.status)), 'fail', 7000);
      return;
    }
    const msg = j.gated_changes?.length
      ? `saved + restart signaled. Gated: ${j.gated_changes.join(', ')}`
      : 'saved + restart signaled';
    toast(msg, 'ok');
    await loadConfig();
  }

  async function loadRaw() {
    const r = await fetch(`/api/agents/${AGENT_NAME}/raw`);
    const j = await r.json();
    $('raw-preview').textContent = JSON.stringify(j, null, 2);
  }

  // ---- tasks ----
  async function loadTasks() {
    const r = await fetch(`/api/agents/${AGENT_NAME}/tasks`);
    const j = await r.json();
    const wrap = $('tasks-wrap');
    wrap.innerHTML = '';
    for (const t of j.tasks) {
      wrap.appendChild(buildTaskRow(t.name, t.meta));
    }
  }

  function buildTaskRow(name, meta) {
    const row = document.createElement('div');
    row.className = 'task-row';
    const kind = meta.kind || 'posted';
    row.innerHTML = `
      <label>name<input class="t-name" value="${escAttr(name)}"></label>
      <label>cron<input class="t-cron" value="${escAttr(meta.cron||'')}" placeholder="0 8 * * *"></label>
      <label>kind<select class="t-kind"><option value="">posted</option><option value="systemEvent" ${kind==='systemEvent'?'selected':''}>systemEvent</option></select></label>
      <div class="btn-row">
        <button class="btn t-expand">edit</button>
        <button class="btn t-save">save</button>
        <button class="btn danger t-del">×</button>
      </div>
      <div class="task-body">
        <textarea class="t-body" rows="6" placeholder="task prompt…">loading…</textarea>
      </div>
    `;
    row.querySelector('.t-expand').onclick = async () => {
      if (row.classList.contains('open')) { row.classList.remove('open'); return; }
      const r = await fetch(`/api/agents/${AGENT_NAME}/tasks/${encodeURIComponent(name)}`);
      if (r.ok) {
        const j = await r.json();
        row.querySelector('.t-body').value = j.body;
      } else {
        row.querySelector('.t-body').value = '';
      }
      row.classList.add('open');
    };
    row.querySelector('.t-save').onclick = async () => {
      const newName = row.querySelector('.t-name').value.trim();
      const cron = row.querySelector('.t-cron').value.trim();
      const kind = row.querySelector('.t-kind').value.trim();
      const body = row.querySelector('.t-body').value;
      const meta = {};
      if (cron) meta.cron = cron;
      if (kind) meta.kind = kind;
      // if renamed, delete old then save
      if (newName !== name) {
        await fetch(`/api/agents/${AGENT_NAME}/tasks/${encodeURIComponent(name)}`, {method:'DELETE'});
      }
      const r = await fetch(`/api/agents/${AGENT_NAME}/tasks/${encodeURIComponent(newName)}`, {
        method: 'POST',
        headers: {'content-type':'application/json'},
        body: JSON.stringify({meta, body}),
      });
      const j = await r.json();
      if (r.ok) { toast(`task saved → ${j.name}`, 'ok'); loadTasks(); }
      else toast('save failed: '+(j.detail||r.status),'fail');
    };
    row.querySelector('.t-del').onclick = async () => {
      if (!confirm(`delete task ${name}? (does not unload launchd plist — run scheduler install --apply)`)) return;
      const r = await fetch(`/api/agents/${AGENT_NAME}/tasks/${encodeURIComponent(name)}`, {method:'DELETE'});
      if (r.ok) { toast('deleted','warn'); loadTasks(); }
    };
    return row;
  }

  $('addTaskBtn').onclick = () => {
    const wrap = $('tasks-wrap');
    const row = buildTaskRow('new_task_' + Date.now().toString(36).slice(-4), {cron: '0 9 * * *'});
    row.classList.add('open');
    row.querySelector('.t-body').value = '';
    wrap.appendChild(row);
  };

  $('addSubBtn').onclick = () => {
    $('subagents-wrap').appendChild(buildSubRow('', {}));
  };

  $('saveBtn').onclick = () => save(false);
  $('reloadBtn').onclick = () => loadConfig();
  $('restartBtn').onclick = async () => {
    const r = await fetch('/api/restart', {method:'POST'});
    const j = await r.json();
    toast(j.ok ? 'restart signaled' : 'failed', j.ok ? 'ok':'fail');
  };

  // clone modal
  $('cloneBtn').onclick = () => $('cloneModal').classList.remove('hidden');
  $('cloneCancel').onclick = () => $('cloneModal').classList.add('hidden');
  $('cloneSubmit').onclick = async () => {
    const payload = {
      new_name: $('clone-name').value.trim(),
      new_channel_id: $('clone-channel').value.trim(),
      new_webhook_env: $('clone-webhook').value.trim(),
      new_bot_token_env: $('clone-token').value.trim(),
    };
    if (!payload.new_name) { toast('new_name required','fail'); return; }
    const r = await fetch(`/api/agents/${AGENT_NAME}/clone`, {
      method:'POST', headers:{'content-type':'application/json'},
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    if (r.ok) {
      toast(`cloned → ${j.name}. ${j.needs_channel ? 'Fill channel + webhook before starting.' : ''}`, 'ok', 6000);
      $('cloneModal').classList.add('hidden');
      setTimeout(() => { location.href = `/agents/${j.name}/edit`; }, 800);
    } else {
      toast('clone failed: '+(j.detail||r.status),'fail');
    }
  };

  // initial load
  loadOptions().then(loadConfig);
})();
"""
