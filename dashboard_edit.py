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

from fastapi import APIRouter, Body, HTTPException, Request
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
KNOWN_PERMISSION_MODES = ["", "default", "acceptEdits", "bypassPermissions", "plan", "dontAsk", "auto"]
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
    for dictkey in ("subagents", "mcp_servers", "approval", "env", "sandbox",
                    "output_format", "task_budget"):
        if dictkey in cfg and cfg[dictkey] is not None and not isinstance(cfg[dictkey], dict):
            errors.append(f"{dictkey} must be an object")
    if "max_thinking_tokens" in cfg and cfg["max_thinking_tokens"] not in (None, ""):
        try:
            n = int(cfg["max_thinking_tokens"])
            if n < 0:
                errors.append("max_thinking_tokens must be >= 0")
        except (TypeError, ValueError):
            errors.append("max_thinking_tokens must be an integer")
    if "fork_session" in cfg and cfg["fork_session"] is not None and not isinstance(cfg["fork_session"], bool):
        errors.append("fork_session must be a boolean")
    if "enable_file_checkpointing" in cfg and cfg["enable_file_checkpointing"] is not None and not isinstance(cfg["enable_file_checkpointing"], bool):
        errors.append("enable_file_checkpointing must be a boolean")
    if "task_budget" in cfg and isinstance(cfg["task_budget"], dict):
        if "total" in cfg["task_budget"]:
            try:
                n = int(cfg["task_budget"]["total"])
                if n < 0:
                    errors.append("task_budget.total must be >= 0")
            except (TypeError, ValueError):
                errors.append("task_budget.total must be an integer")
    if "permission_mode" in cfg and cfg["permission_mode"]:
        if cfg["permission_mode"] not in KNOWN_PERMISSION_MODES:
            errors.append(f"permission_mode must be one of: {', '.join(x for x in KNOWN_PERMISSION_MODES if x)}")
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


def _require_agent(name: str) -> str:
    """Validate a {name} path param against real agent dirs. Blocks path
    traversal (`name=../..`) on every task route."""
    if name not in _list_agents():
        raise HTTPException(404, f"no such agent: {name!r}")
    return name


@router.get("/api/agents/{name}/tasks/{task}")
def api_task_get(name: str, task: str):
    _require_agent(name)
    path = AGENTS_DIR / name / "tasks" / f"{_slugify(task)}.md"
    if not path.exists():
        raise HTTPException(404, "no such task")
    meta, body = _parse_task_file(path)
    return {"name": task, "meta": meta, "body": body, "path": str(path)}


class TaskPayload(BaseModel):
    meta: dict = {}
    body: str = ""


@router.post("/api/agents/{name}/tasks/{task}")
def api_task_save(name: str, task: str, payload: TaskPayload):
    _require_agent(name)
    task_safe = _slugify(task)
    path = AGENTS_DIR / name / "tasks" / f"{task_safe}.md"
    _write_task_file(path, payload.meta, payload.body)
    return {"ok": True, "path": str(path), "name": task_safe}


@router.delete("/api/agents/{name}/tasks/{task}")
def api_task_delete(name: str, task: str):
    _require_agent(name)
    path = AGENTS_DIR / name / "tasks" / f"{_slugify(task)}.md"
    if not path.exists():
        raise HTTPException(404, "no such task")
    path.unlink()
    return {"ok": True, "deleted": str(path)}


@router.get("/api/agents/{name}/tasks")
def api_tasks_list(name: str):
    _require_agent(name)
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
    """Skills, tools, models, everything needed to populate selects + defaults
    so the UI can show what's inherited vs. explicitly overridden."""
    if name not in _list_agents():
        raise HTTPException(404, "no such agent")
    # Load defaults from config.yaml so the UI can render "(default: X)"
    defaults: dict = {}
    cfg_path = ROOT / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml as _yaml_mod
            raw = _yaml_mod.safe_load(cfg_path.read_text()) or {}
            defaults = raw.get("defaults") or {}
        except Exception:
            defaults = {}
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
        "defaults": defaults,
    }


@router.post("/api/restart")
def api_restart():
    path = _signal_restart()
    return {"ok": True, "signaled": path}


# ---- Connectors -----------------------------------------------------------

CONNECTOR_REGISTRY_PATH = ROOT / "connectors" / "registry.yaml"


def _load_connector_registry() -> list[dict]:
    if not CONNECTOR_REGISTRY_PATH.exists():
        return []
    try:
        import yaml as _yaml_mod
        raw = _yaml_mod.safe_load(CONNECTOR_REGISTRY_PATH.read_text()) or {}
        return raw.get("connectors") or []
    except Exception:
        return []


def _env_vars_set() -> set[str]:
    """Which env vars are actually defined in .env or os.environ?"""
    present: set[str] = set()
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            val = line.split("=", 1)[1].strip()
            if key and val:
                present.add(key)
    for k, v in os.environ.items():
        if v:
            present.add(k)
    return present


@router.get("/api/connectors")
def api_connectors_list(agent: str | None = None):
    """List connector registry. If ?agent=<name> supplied, annotate each with
    current enabled state for that agent."""
    registry = _load_connector_registry()
    env_present = _env_vars_set()
    enabled_mcp: set[str] = set()
    enabled_env_passthrough: set[str] = set()
    agent_env_literal: dict[str, str] = {}
    if agent:
        if agent not in _list_agents():
            raise HTTPException(404, "no such agent")
        cfg = _load_agent_yaml(AGENTS_DIR / agent / "agent.yaml")
        mcp = cfg.get("mcp_servers") or {}
        enabled_mcp = set(mcp.keys()) if isinstance(mcp, dict) else set()
        enabled_env_passthrough = set(cfg.get("env_passthrough") or [])
        env_lit = cfg.get("env") or {}
        if isinstance(env_lit, dict):
            agent_env_literal = {str(k): str(v) for k, v in env_lit.items()}

    result = []
    for c in registry:
        item = dict(c)
        item["enabled"] = False
        item["credentials_ready"] = True
        if c.get("kind") == "mcp_note":
            item["enabled"] = c["id"] in enabled_mcp
        elif c.get("kind") == "env_key":
            required = c.get("env_vars") or []
            per_agent = (c.get("per_agent_env") or {}) if isinstance(c.get("per_agent_env"), dict) else {}
            # Global vars = required vars NOT marked per-agent. Those live in .env.
            global_vars = [v for v in required if v not in per_agent]
            per_agent_vars = [v for v in required if v in per_agent]
            item["credentials_ready"] = all(v in env_present for v in global_vars)
            # Enabled = env_passthrough covers global vars AND every per-agent var
            # has a value set in this agent's env: literal block.
            has_global = bool(global_vars) and all(v in enabled_env_passthrough for v in global_vars)
            has_per_agent = all(v in agent_env_literal and agent_env_literal[v] for v in per_agent_vars)
            # Backward compat: if the legacy flow had per-agent vars in
            # env_passthrough (so they fell back to the global .env), treat as
            # enabled-via-legacy. User can Edit to migrate to per-agent yaml.
            legacy_per_agent = all(
                v in enabled_env_passthrough and v in env_present
                for v in per_agent_vars
            )
            item["legacy_per_agent"] = (
                bool(per_agent_vars) and not has_per_agent and legacy_per_agent
            )
            if not global_vars and per_agent_vars:
                item["enabled"] = has_per_agent or legacy_per_agent
            else:
                item["enabled"] = has_global and (has_per_agent or legacy_per_agent)
            item["missing_env_vars"] = [v for v in global_vars if v not in env_present]
            # Per-agent value currently set for this agent (empty if not yet).
            item["per_agent_values"] = {v: agent_env_literal.get(v, "") for v in per_agent_vars}
        result.append(item)
    return {"connectors": result}


class ConnectorToggle(BaseModel):
    enable: bool
    # Per-agent env overrides (e.g. SENTRY_PROJECT). Written to agent.yaml
    # `env:` block instead of the global .env so each agent can hold a
    # different value.
    per_agent_values: dict[str, str] | None = None


@router.post("/api/agents/{name}/connectors/{connector_id}")
def api_connector_toggle(name: str, connector_id: str, payload: ConnectorToggle):
    """Toggle a connector on/off for an agent. Writes agent.yaml + signals restart."""
    if name not in _list_agents():
        raise HTTPException(404, "no such agent")
    registry = _load_connector_registry()
    connector = next((c for c in registry if c["id"] == connector_id), None)
    if not connector:
        raise HTTPException(404, f"unknown connector: {connector_id}")

    path = AGENTS_DIR / name / "agent.yaml"
    doc = _load_agent_yaml(path)

    kind = connector.get("kind")
    if kind == "mcp_note":
        mcp = doc.get("mcp_servers")
        if not isinstance(mcp, (CommentedMap, dict)):
            mcp = CommentedMap()
            doc["mcp_servers"] = mcp
        if payload.enable:
            entry = CommentedMap()
            entry["type"] = "mcp"
            entry["note"] = connector.get("note") or connector.get("description") or connector["name"]
            mcp[connector_id] = entry
        else:
            if connector_id in mcp:
                del mcp[connector_id]

    elif kind == "env_key":
        required = connector.get("env_vars") or []
        per_agent = connector.get("per_agent_env") or {}
        if not isinstance(per_agent, dict):
            per_agent = {}
        # Split: global vars live in .env; per_agent vars live in agent.yaml `env:`.
        global_vars = [v for v in required if v not in per_agent]
        per_agent_vars = [v for v in required if v in per_agent]

        env_pass = doc.get("env_passthrough")
        if not isinstance(env_pass, list):
            env_pass = []
        env_pass_set = set(env_pass)

        # Load existing env: literal block
        env_literal = doc.get("env")
        if not isinstance(env_literal, (CommentedMap, dict)):
            env_literal = CommentedMap()

        if payload.enable:
            env_present = _env_vars_set()
            missing = [v for v in global_vars if v not in env_present]
            if missing:
                raise HTTPException(400, {
                    "error": "missing_credentials",
                    "missing_env_vars": missing,
                    "message": f"Set these in .env first: {', '.join(missing)}. Secrets never flow through chat.",
                })
            # Per-agent values must be provided if the connector defines any.
            provided = payload.per_agent_values or {}
            missing_per_agent = [v for v in per_agent_vars if not provided.get(v, "").strip()
                                 and not str(env_literal.get(v, "")).strip()]
            if missing_per_agent:
                raise HTTPException(400, {
                    "error": "missing_per_agent_values",
                    "missing_per_agent": missing_per_agent,
                    "message": (
                        f"Per-agent value required: {', '.join(missing_per_agent)}. "
                        f"Each agent targets a different project — provide the slug for this agent."
                    ),
                })
            # env_passthrough carries the global vars only
            for v in global_vars:
                env_pass_set.add(v)
            # env: literal holds per-agent values (overrides env_passthrough at load time)
            for v in per_agent_vars:
                val = provided.get(v, "").strip()
                if val:
                    env_literal[v] = val
            # Mirror as an mcp_servers note too, so the agent's system prompt mentions it.
            # For env_key connectors we also stamp the HTTP API recipe — env var
            # names, endpoint base, and example curl calls — so the agent doesn't
            # waste turns searching for a non-existent MCP tool.
            mcp = doc.get("mcp_servers")
            if not isinstance(mcp, (CommentedMap, dict)):
                mcp = CommentedMap()
                doc["mcp_servers"] = mcp
            entry = CommentedMap()
            entry["type"] = "env"
            entry["note"] = connector.get("note") or connector.get("description") or connector["name"]
            entry["env_vars"] = list(required)
            if connector.get("api_base"):
                entry["api_base"] = connector["api_base"]
            if connector.get("api_auth"):
                entry["api_auth"] = connector["api_auth"]
            if connector.get("api_hints"):
                entry["api_hints"] = list(connector["api_hints"])
            if connector.get("docs_url"):
                entry["docs_url"] = connector["docs_url"]
            mcp[connector_id] = entry
        else:
            for v in global_vars:
                env_pass_set.discard(v)
            for v in per_agent_vars:
                if v in env_literal:
                    del env_literal[v]
            mcp = doc.get("mcp_servers")
            if isinstance(mcp, (CommentedMap, dict)) and connector_id in mcp:
                del mcp[connector_id]

        doc["env_passthrough"] = sorted(env_pass_set) if env_pass_set else []
        if not doc["env_passthrough"]:
            del doc["env_passthrough"]
        # Keep env: literal around only if it has values
        if env_literal:
            doc["env"] = env_literal
        elif "env" in doc:
            del doc["env"]
    else:
        raise HTTPException(400, f"unsupported connector kind: {kind}")

    _dump_agent_yaml(doc, path)
    _signal_restart()
    return {"ok": True, "connector": connector_id, "enabled": payload.enable, "restart_signaled": True}


# ---- Local credential write (.env) ---------------------------------------
# Accepts credential writes ONLY from localhost to avoid exposing .env writes
# over the Cloudflare tunnel / Vercel proxy. The dashboard is meant to be
# operated from the same box the bot runs on; tunnel access stays read-ish.

_LOCALHOST_HOSTS = {"127.0.0.1", "localhost", "::1"}
# Tailscale CGNAT range — treat tailnet peers as trusted (same as localhost).
# Cloudflare tunnel requests come from Cloudflare IPs, so those stay blocked.
import ipaddress as _ipaddress
_TAILSCALE_CIDR = _ipaddress.ip_network("100.64.0.0/10")


def _is_localhost_request(request: Request) -> bool:
    # Uvicorn sets request.client.host; prefer that over Host header (spoofable).
    client = request.client.host if request.client else ""
    if client in _LOCALHOST_HOSTS:
        return True
    # Tailscale peer — trusted tunnel back to Mac Studio.
    try:
        if _ipaddress.ip_address(client) in _TAILSCALE_CIDR:
            return True
    except (ValueError, TypeError):
        pass
    # No Host-header fallback: the Host header is client-controlled and a
    # tunnel request with "Host: localhost" must NOT unlock .env writes.
    return False


# Conservative var-name regex: uppercase letters, digits, underscores.
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class EnvWrite(BaseModel):
    # {VAR_NAME: value}
    values: dict[str, str]
    connector_id: str | None = None  # optional, used to cross-check against registry


@router.post("/api/env/write")
def api_env_write(payload: EnvWrite, request: Request):
    """Append/update credentials in the relay's .env file. Localhost only."""
    if not _is_localhost_request(request):
        raise HTTPException(403, "env write requires localhost access (not available over tunnel)")

    if not payload.values:
        raise HTTPException(400, "no values provided")

    # Scope the allowed var names: must match registry if connector_id supplied,
    # otherwise still require the strict env-var regex.
    allowed: set[str] | None = None
    if payload.connector_id:
        registry = _load_connector_registry()
        connector = next((c for c in registry if c["id"] == payload.connector_id), None)
        if not connector:
            raise HTTPException(404, f"unknown connector: {payload.connector_id}")
        allowed = set(connector.get("env_vars") or [])
        if not allowed:
            raise HTTPException(400, "connector has no env_vars defined")

    written: list[str] = []
    for k, v in payload.values.items():
        if not _ENV_VAR_RE.match(k):
            raise HTTPException(400, f"invalid env var name: {k!r}")
        if allowed is not None and k not in allowed:
            raise HTTPException(400, f"{k} is not part of connector {payload.connector_id}")
        if v is None or v == "":
            raise HTTPException(400, f"empty value for {k}")
        written.append(k)

    env_path = ROOT / ".env"
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    # Build a map of existing keys → line index.
    key_to_idx: dict[str, int] = {}
    for i, line in enumerate(existing_lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if k:
            key_to_idx[k] = i

    # Apply writes — replace in place if key exists, append otherwise.
    for k in written:
        raw_val = payload.values[k]
        # Quote if the value contains whitespace or shell-ish characters.
        if any(ch in raw_val for ch in [" ", "\t", "#", "$", "`", "\"", "'"]):
            val = '"' + raw_val.replace('"', '\\"') + '"'
        else:
            val = raw_val
        new_line = f"{k}={val}"
        if k in key_to_idx:
            existing_lines[key_to_idx[k]] = new_line
        else:
            # Ensure a trailing blank-line separation if file didn't end with newline group.
            if existing_lines and existing_lines[-1].strip():
                existing_lines.append("")
            existing_lines.append(new_line)
            key_to_idx[k] = len(existing_lines) - 1

    content = "\n".join(existing_lines).rstrip() + "\n"
    # Restrictive perms — .env holds secrets.
    env_path.write_text(content)
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass

    # Also populate the live process environ so subsequent /api/connectors
    # reflects credentials_ready immediately (no dashboard restart required).
    for k in written:
        os.environ[k] = payload.values[k]

    return {"ok": True, "written": written, "count": len(written)}


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

  <div id="effective" class="effective">
    <h3>Effective config — what this agent will actually run</h3>
    <div id="eff-grid" class="eff-grid">loading…</div>
  </div>

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
      <div class="dim" style="margin-bottom:0.8em;">
        Checked = tool is <b>active</b> for this agent. Uncheck a <span class="chip-default">default</span> tool to block it.
        Check a tool that isn't a default to add it as an <span class="chip-override">override</span>.
      </div>
      <div id="f-allowed_tools" class="tool-grid"></div>
      <div id="tools-summary" class="dim" style="margin-top:0.8em;font-size:0.82em;"></div>
      <div id="f-disallowed_tools" style="display:none;"></div>
    </div>
  </details>

  <details open><summary>Skills</summary>
    <div class="panel">
      <div class="dim" style="margin-bottom:0.6em;">Toggle to enable a skill. Shared come from <code>shared/skills/</code>; local from <code>agents/{esc(name)}/skills/</code>.</div>
      <div id="f-skills" class="tool-grid"></div>
    </div>
  </details>

  <details open><summary>Connectors <span class="dim" id="conn-count"></span></summary>
    <div class="panel">
      <div class="dim" style="margin-bottom:0.8em;">
        One-click third-party integrations. <span class="chip-default">MCP</span> connectors toggle instantly.
        <span class="chip-override">API key</span> connectors need credentials in <code>.env</code> first — never paste secrets in chat.
      </div>
      <div id="conn-grid" class="conn-grid">loading…</div>
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

  <details><summary>Advanced (Claude Agent SDK)</summary>
    <div class="panel">
      <div class="dim" style="margin-bottom:0.8em;">Low-level options passed straight to <code>ClaudeAgentOptions</code>. Blank = inherit from <code>config.yaml</code> defaults or SDK default.</div>
      <div class="grid">
        <label class="col-2">cwd (working directory)
          <input id="f-cwd" placeholder="(blank → vault path from config.yaml)">
        </label>
        <label>max_thinking_tokens<input id="f-max_thinking_tokens" type="number" min="0" placeholder="inherit"></label>
        <label>fork_session<select id="f-fork_session"><option value="inherit">inherit</option><option value="true">true</option><option value="false">false</option></select></label>
        <label>task_budget (total tokens)<input id="f-task_budget" type="number" min="0" placeholder="inherit"></label>
        <label>enable_file_checkpointing<select id="f-enable_file_checkpointing"><option value="inherit">inherit</option><option value="true">true</option><option value="false">false</option></select></label>
        <label class="col-2">output_format (JSON — custom output schema)
          <textarea id="f-output_format" rows="3" placeholder='inherit — or e.g. {{"type":"json"}}'></textarea>
        </label>
        <label class="col-2">sandbox (JSON — SandboxSettings)
          <textarea id="f-sandbox" rows="4" placeholder='inherit — or e.g. {{"enabled":true,"network":{{"allowedHosts":["api.anthropic.com"]}}}}'></textarea>
        </label>
      </div>
    </div>
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

<div id="connModal" class="modal hidden">
  <div class="modal-body">
    <h2 id="conn-modal-title">Connect</h2>
    <div class="dim" id="conn-modal-help" style="margin-bottom:0.6em;"></div>
    <div id="conn-modal-fields"></div>
    <div class="dim" style="margin-top:0.8em;font-size:0.78em;">
      Writes to <code>.env</code> on this machine (localhost-only). File perms set to 600. Restarts relay after save.
    </div>
    <div class="btn-row">
      <button id="connCancel" class="btn">Cancel</button>
      <button id="connSubmit" class="btn primary">Save &amp; connect</button>
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
    --bg:#f7f5ef; --panel:#ffffff; --panel-2:#efece3; --panel-3:#f2efe6;
    --fg:#1a1f2e; --fg-dim:#4b5563; --fg-mute:#8a8a7b;
    --accent:#1a1f2e; --accent-2:#7cc9a8; --accent-ink:#ffffff;
    --ok:#3d8f58; --warn:#b5791f; --fail:#b94a33;
    --border:#e5e1d6; --border-strong:#d6d1c2;
    --shadow-sm:0 1px 2px rgba(26,31,46,0.04), 0 0 0 1px rgba(26,31,46,0.04);
    --shadow-md:0 4px 16px rgba(26,31,46,0.06), 0 0 0 1px rgba(26,31,46,0.04);
    --radius:12px;
    --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,monospace;
    --serif:"Charter","Iowan Old Style","Georgia",serif;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#15161c; --panel:#1c1d25; --panel-2:#252731; --panel-3:#1f2029;
      --fg:#f1eee4; --fg-dim:#c4bfae; --fg-mute:#827d6e;
      --accent:#f7f5ef; --accent-ink:#1a1f2e;
      --border:#2e2f3a; --border-strong:#3b3d4a;
      --shadow-sm:0 1px 2px rgba(0,0,0,0.3), 0 0 0 1px rgba(255,255,255,0.04);
    }
  }
  *{box-sizing:border-box;}
  body{font-family:-apple-system,BlinkMacSystemFont,"Inter","SF Pro Text",sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:0;font-size:15px;-webkit-font-smoothing:antialiased;padding-bottom:env(safe-area-inset-bottom);}
  @supports (-webkit-touch-callout: none) { body { font-size:16px; } }
  nav{display:flex;gap:1em;padding:0.9em 1.5em;border-bottom:1px solid var(--border);background:color-mix(in srgb, var(--bg) 86%, transparent);backdrop-filter:saturate(140%) blur(14px);-webkit-backdrop-filter:saturate(140%) blur(14px);position:sticky;top:0;z-index:30;align-items:center;}
  nav a{color:var(--fg-dim);font-size:0.9em;text-decoration:none;padding:0.35em 0.85em;border-radius:999px;}
  nav a:hover{color:var(--fg);background:var(--panel-2);}
  .wrap{max-width:1100px;margin:0 auto;padding:1.5em;}
  @media (max-width:768px){.wrap{padding:1em;}}
  h1{font-family:var(--serif);font-size:1.7em;margin:0;font-weight:500;letter-spacing:-0.02em;}
  h2{font-size:1em;margin:0 0 0.8em;font-weight:600;}
  code,pre{font-family:var(--mono);font-size:0.85em;}
  code{background:var(--panel-2);padding:0.1em 0.45em;border-radius:5px;word-break:break-word;}
  pre{white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;overflow-x:auto;max-width:100%;}
  textarea{width:100%;box-sizing:border-box;word-break:break-word;}
  input{max-width:100%;box-sizing:border-box;}
  .wrap,details,.panel{max-width:100%;box-sizing:border-box;overflow-wrap:anywhere;}
  html,body{overflow-x:hidden;}
  .chip-default{display:inline-block;font-size:0.72em;padding:0.05em 0.45em;border-radius:999px;background:color-mix(in srgb, var(--accent-2) 22%, transparent);color:var(--ok);font-weight:600;text-transform:uppercase;letter-spacing:0.04em;}
  .chip-override{display:inline-block;font-size:0.72em;padding:0.05em 0.45em;border-radius:999px;background:var(--panel-2);color:var(--fg);font-weight:600;text-transform:uppercase;letter-spacing:0.04em;}
  .header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1em;flex-wrap:wrap;gap:0.8em;}
  .dim{color:var(--fg-mute);font-size:0.85em;}
  .btn-row{display:flex;gap:0.5em;flex-wrap:wrap;}
  .btn{background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:999px;padding:0.55em 1.1em;cursor:pointer;font-size:0.9em;min-height:40px;font-weight:500;box-shadow:var(--shadow-sm);transition:all 0.14s;font-family:inherit;}
  .btn:hover{border-color:var(--border-strong);transform:translateY(-1px);box-shadow:var(--shadow-md);}
  .btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600;}
  .btn.primary:hover{opacity:0.92;}
  .btn.danger{background:var(--fail);color:#fff;border-color:var(--fail);}
  details{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:1em 1.3em;margin-bottom:0.8em;box-shadow:var(--shadow-sm);}
  details summary{cursor:pointer;font-size:0.95em;font-weight:600;color:var(--fg);padding:0.2em 0;list-style:none;display:flex;align-items:center;gap:0.5em;}
  details summary::-webkit-details-marker{display:none;}
  details summary::before{content:"";display:inline-block;width:0;height:0;border-left:5px solid var(--fg-mute);border-top:4px solid transparent;border-bottom:4px solid transparent;transition:transform 0.14s;}
  details[open] summary::before{transform:rotate(90deg);}
  details>summary+*{margin-top:0.9em;}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:0.9em;}
  .grid .col-2{grid-column:span 2;}
  @media (max-width:700px){.grid{grid-template-columns:1fr;}.grid .col-2{grid-column:span 1;}}
  label{display:flex;flex-direction:column;gap:0.35em;font-size:0.82em;color:var(--fg-dim);font-weight:500;}
  input,select,textarea{background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:0.6em 0.75em;font-size:0.95em;font-family:inherit;min-height:40px;}
  input:focus,select:focus,textarea:focus{border-color:var(--fg);outline:2px solid rgba(26,31,46,0.08);outline-offset:0;}
  textarea{font-family:var(--mono);font-size:0.85em;min-height:unset;}
  .panel{background:var(--panel-3);border:1px solid var(--border);border-radius:10px;padding:0.9em 1.1em;}
  .tool-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:0.4em;}
  .tool-grid label{display:flex;flex-direction:row;align-items:center;gap:0.55em;color:var(--fg);font-size:0.87em;background:var(--panel);padding:0.55em 0.85em;border-radius:8px;border:1px solid var(--border);cursor:pointer;min-height:40px;transition:all 0.14s;min-width:0;overflow:hidden;}
  .tool-grid .tool-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;}
  .tool-grid label:hover{border-color:var(--border-strong);background:var(--panel-3);}
  .tool-grid label:has(input:checked){border-color:var(--fg);background:var(--panel-2);}
  .tool-grid input[type=checkbox]{margin:0;width:16px;height:16px;accent-color:var(--fg);}
  /* default-state + override indicators */
  .tool-grid label.is-default{background:color-mix(in srgb, var(--accent-2) 14%, var(--panel));border-color:color-mix(in srgb, var(--accent-2) 40%, var(--border));}
  .tool-grid label.is-default::after{content:"default";margin-left:auto;font-size:0.66em;color:var(--ok);background:color-mix(in srgb, var(--accent-2) 22%, transparent);padding:0.15em 0.55em;border-radius:999px;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;}
  .tool-grid label.is-override::after{content:"override";margin-left:auto;font-size:0.66em;color:var(--fg);background:var(--panel-2);padding:0.15em 0.55em;border-radius:999px;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;}
  .tool-grid label.is-blocked{background:color-mix(in srgb, var(--fail) 10%, var(--panel));border-color:color-mix(in srgb, var(--fail) 30%, var(--border));}
  .tool-grid label.is-blocked::after{content:"blocked";margin-left:auto;font-size:0.66em;color:var(--fail);background:color-mix(in srgb, var(--fail) 14%, transparent);padding:0.15em 0.55em;border-radius:999px;text-transform:uppercase;letter-spacing:0.06em;font-weight:600;}
  /* Connectors grid */
  .conn-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:0.7em;}
  .conn-card{display:flex;flex-direction:column;gap:0.4em;padding:0.85em 0.95em;background:var(--panel);border:1px solid var(--border);border-radius:10px;min-height:120px;transition:all 0.14s;}
  .conn-card:hover{border-color:var(--border-strong);background:var(--panel-2);}
  .conn-card.enabled{border-color:var(--ok);background:color-mix(in srgb, var(--ok) 8%, var(--panel));}
  .conn-card.needs-setup{border-color:color-mix(in srgb, var(--warn, #e8b755) 40%, var(--border));background:color-mix(in srgb, var(--warn, #e8b755) 6%, var(--panel));}
  .conn-head{display:flex;align-items:center;gap:0.5em;font-weight:600;font-size:0.92em;color:var(--fg);}
  .conn-emoji{font-size:1.2em;}
  .conn-cat{font-size:0.7em;color:var(--fg-mute);text-transform:uppercase;letter-spacing:0.06em;margin-left:auto;}
  .conn-desc{font-size:0.82em;color:var(--fg-mute);flex:1;line-height:1.35;}
  .conn-btn-row{display:flex;align-items:center;gap:0.5em;margin-top:0.25em;}
  .conn-btn{padding:0.4em 0.9em;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--fg);cursor:pointer;font-size:0.82em;font-weight:600;transition:all 0.12s;}
  .conn-btn:hover{background:var(--panel-3);border-color:var(--border-strong);}
  .conn-btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);}
  .conn-btn.primary:hover{filter:brightness(1.1);}
  .conn-btn.danger{border-color:color-mix(in srgb, var(--fail) 40%, var(--border));color:var(--fail);}
  .conn-btn:disabled{opacity:0.5;cursor:not-allowed;}
  .conn-badge{font-size:0.7em;padding:0.12em 0.5em;border-radius:999px;background:var(--panel-2);color:var(--fg-mute);font-weight:600;text-transform:uppercase;letter-spacing:0.04em;}
  .conn-badge.enabled{background:color-mix(in srgb, var(--ok) 22%, transparent);color:var(--ok);}
  .conn-badge.needs-setup{background:color-mix(in srgb, var(--warn, #e8b755) 22%, transparent);color:var(--warn, #e8b755);}
  .conn-missing{font-size:0.72em;color:var(--warn, #e8b755);margin-top:0.15em;}
  .conn-missing code{background:var(--panel-2);padding:0.05em 0.35em;border-radius:4px;font-size:0.9em;}
  .inherit-hint{font-size:0.75em;color:var(--fg-mute);margin-top:0.2em;font-style:italic;}
  .effective{background:color-mix(in srgb, var(--accent-2) 10%, var(--panel));border:1px solid color-mix(in srgb, var(--accent-2) 30%, var(--border));border-radius:var(--radius);padding:1em 1.2em;margin-bottom:1.2em;}
  .effective h3{margin:0 0 0.5em;font-size:0.72em;color:var(--ok);text-transform:uppercase;letter-spacing:0.12em;font-weight:700;}
  .effective .eff-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:0.5em 1.2em;font-size:0.86em;}
  .effective .eff-key{color:var(--fg-mute);font-size:0.78em;text-transform:uppercase;letter-spacing:0.06em;}
  .effective .eff-val{color:var(--fg);font-weight:500;font-family:var(--mono);font-size:0.86em;word-break:break-word;}
  .effective .eff-val.dim{color:var(--fg-mute);font-style:italic;font-family:inherit;}
  .source-tag{display:inline-block;font-size:0.66em;padding:0.1em 0.5em;border-radius:999px;margin-left:0.4em;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;vertical-align:middle;}
  .source-tag.override{background:var(--panel-2);color:var(--fg);}
  .source-tag.default{background:color-mix(in srgb, var(--accent-2) 22%, transparent);color:var(--ok);}
  .sub-row{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:0.9em;margin-bottom:0.6em;display:grid;grid-template-columns:1fr 1fr auto;gap:0.6em;}
  .sub-row input,.sub-row select,.sub-row textarea{width:100%;}
  .sub-row .sub-name{font-weight:600;}
  .sub-row textarea{grid-column:span 3;}
  .sub-row .sub-full{grid-column:span 3;display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:0.5em;}
  @media (max-width:700px){.sub-row,.sub-row .sub-full{grid-template-columns:1fr;}.sub-row textarea{grid-column:span 1;}}
  .task-row{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:0.9em;margin-bottom:0.6em;display:grid;grid-template-columns:2fr 1.5fr 1fr auto;gap:0.5em;align-items:center;}
  .task-row .task-body{grid-column:span 4;display:none;margin-top:0.5em;}
  .task-row.open .task-body{display:block;}
  .task-row textarea{width:100%;}
  @media (max-width:700px){.task-row{grid-template-columns:1fr;}.task-row .task-body{grid-column:span 1;}}
  .toast{position:fixed;top:1em;right:1em;background:var(--panel);border:1px solid var(--border);padding:0.9em 1.3em;border-radius:10px;z-index:1000;display:none;max-width:420px;box-shadow:var(--shadow-md);}
  .toast.ok{border-color:var(--ok);color:var(--ok);}
  .toast.warn{border-color:var(--warn);color:var(--warn);}
  .toast.fail{border-color:var(--fail);color:var(--fail);}
  .modal{position:fixed;inset:0;background:rgba(26,31,46,0.5);display:flex;align-items:center;justify-content:center;z-index:2000;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);}
  .modal.hidden{display:none;}
  .modal-body{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:1.5em;width:min(500px,92vw);box-shadow:var(--shadow-md);}
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
    const defaults = state.options.defaults || {};
    const fmt = (v) => {
      if (v === undefined || v === null || v === '') return '∅';
      if (Array.isArray(v)) return v.length ? v.join(', ') : '∅';
      if (typeof v === 'object') return JSON.stringify(v);
      return String(v);
    };
    const inheritLabel = (key) => {
      const d = defaults[key];
      if (d === undefined || d === null || d === '') return '(inherit — no default)';
      return `(inherit → ${fmt(d)})`;
    };
    // model dropdowns — first option rewritten to show default
    for (const id of ['f-model', 'f-fallback_model']) {
      const sel = $(id);
      sel.innerHTML = '';
      for (const m of state.options.models) {
        const o = document.createElement('option');
        o.value = m.value;
        const key = id === 'f-model' ? 'model' : 'fallback_model';
        o.textContent = m.value === '' ? inheritLabel(key) : m.label;
        sel.appendChild(o);
      }
    }
    // effort / thinking / permission_mode
    const selMap = {
      'f-effort': ['effort', state.options.effort],
      'f-thinking': ['thinking', state.options.thinking],
      'f-permission_mode': ['permission_mode', state.options.permission_modes],
    };
    for (const [id, [key, list]] of Object.entries(selMap)) {
      const sel = $(id);
      sel.innerHTML = '';
      for (const v of list) {
        const o = document.createElement('option');
        o.value = v;
        o.textContent = v || inheritLabel(key);
        sel.appendChild(o);
      }
    }
    // placeholder-as-default for number/text inputs
    const placeholders = {
      'f-max_turns': 'max_turns',
      'f-max_hops': 'max_hops',
      'f-setting_sources': 'setting_sources',
      'f-add_dirs': 'add_dirs',
    };
    for (const [id, key] of Object.entries(placeholders)) {
      const el = $(id);
      if (!el) continue;
      const d = defaults[key];
      if (d !== undefined && d !== null && d !== '') {
        el.placeholder = `inherit → ${fmt(d)}`;
      }
    }
    // approval placeholders
    const apD = defaults.approval || {};
    if ($('f-approval_timeout') && apD.timeout_seconds != null)
      $('f-approval_timeout').placeholder = `inherit → ${apD.timeout_seconds}`;
    if ($('f-approval_poll') && apD.poll_interval_seconds != null)
      $('f-approval_poll').placeholder = `inherit → ${apD.poll_interval_seconds}`;
    // tool grid — single merged grid. Annotation + checked state set in fillForm().
    const defAllowed = defaults.allowed_tools || [];
    const g = $('f-allowed_tools'); g.innerHTML = '';
    // Sort: defaults first so the common "on" tools bubble to the top
    const allTools = [...state.options.tools].sort((a, b) => {
      const da = defAllowed.includes(a), db = defAllowed.includes(b);
      if (da !== db) return da ? -1 : 1;
      return a.localeCompare(b);
    });
    for (const t of allTools) {
      const l = document.createElement('label');
      l.dataset.tool = t;
      if (defAllowed.includes(t)) l.classList.add('is-default');
      l.innerHTML = `<input type="checkbox" data-tool="${t}"><span class="tool-name">${t}</span>`;
      g.appendChild(l);
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
    // tool checkboxes — checked = tool is ACTIVE (merged effective state).
    // Uncheck default = blocks it. Check non-default = override adds it.
    const defs = (state.options && state.options.defaults) || {};
    const defAllowed = defs.allowed_tools || [];
    const ovAllowed = c.allowed_tools || [];
    const ovDisallowed = c.disallowed_tools || [];
    let activeCount = 0, defaultCount = 0, overrideCount = 0, blockedCount = 0;
    for (const lab of document.querySelectorAll('#f-allowed_tools label')) {
      const t = lab.dataset.tool;
      const cb = lab.querySelector('input');
      const inDefault = defAllowed.includes(t);
      const inAllowed = ovAllowed.includes(t);
      const inBlocked = ovDisallowed.includes(t);
      // Effective = (default OR override-added) AND NOT blocked
      const active = (inDefault || inAllowed) && !inBlocked;
      cb.checked = active;
      lab.classList.toggle('is-default', inDefault && !inBlocked);
      lab.classList.toggle('is-override', inAllowed && !inDefault && !inBlocked);
      lab.classList.toggle('is-blocked', inBlocked);
      if (active) activeCount++;
      if (inDefault && !inBlocked) defaultCount++;
      if (inAllowed && !inDefault && !inBlocked) overrideCount++;
      if (inBlocked) blockedCount++;
    }
    const summary = $('tools-summary');
    if (summary) {
      summary.innerHTML = `<b>${activeCount}</b> active — ${defaultCount} from defaults, ${overrideCount} overrides. <b>${blockedCount}</b> blocked.`;
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
    // advanced (SDK) fields
    $('f-cwd').value = c.cwd || '';
    $('f-max_thinking_tokens').value = c.max_thinking_tokens ?? '';
    $('f-fork_session').value = c.fork_session === undefined || c.fork_session === null ? 'inherit' : String(c.fork_session);
    const tb = c.task_budget;
    $('f-task_budget').value = (tb && typeof tb === 'object') ? (tb.total ?? '') : (typeof tb === 'number' ? tb : '');
    $('f-enable_file_checkpointing').value = c.enable_file_checkpointing === undefined || c.enable_file_checkpointing === null ? 'inherit' : String(c.enable_file_checkpointing);
    $('f-output_format').value = c.output_format ? (typeof c.output_format === 'string' ? c.output_format : JSON.stringify(c.output_format, null, 2)) : '';
    $('f-sandbox').value = c.sandbox ? JSON.stringify(c.sandbox, null, 2) : '';
    // effective-config summary panel
    renderEffective(c);
  }

  function renderEffective(c) {
    const defs = (state.options && state.options.defaults) || {};
    const grid = $('eff-grid');
    if (!grid) return;
    // Merge: override → default → "∅"
    const pick = (k, fallback) => {
      if (c[k] !== undefined && c[k] !== null && c[k] !== '') return { value: c[k], source: 'override' };
      if (defs[k] !== undefined && defs[k] !== null && defs[k] !== '') return { value: defs[k], source: 'default' };
      return { value: fallback ?? null, source: 'default' };
    };
    const fmt = (v) => {
      if (v === undefined || v === null || v === '') return '(unset)';
      if (Array.isArray(v)) return v.length ? v.join(', ') : '(empty)';
      if (typeof v === 'object') return JSON.stringify(v);
      return String(v);
    };
    // derived: merged tool set
    const baseAllowed = (defs.allowed_tools || []).filter(t => !(c.disallowed_tools || []).includes(t));
    const extras = (c.allowed_tools || []).filter(t => !(defs.allowed_tools || []).includes(t));
    const activeTools = [...new Set([...baseAllowed, ...extras])];
    const blocked = [...(c.disallowed_tools || []), ...(defs.disallowed_tools || [])];
    // rows
    const rows = [];
    const row = (key, label) => {
      const { value, source } = pick(key);
      const v = fmt(value);
      const tag = source === 'override'
        ? '<span class="source-tag override">override</span>'
        : '<span class="source-tag default">default</span>';
      const vCls = (value === null || value === undefined || value === '') ? 'eff-val dim' : 'eff-val';
      rows.push(`<div><div class="eff-key">${label}${tag}</div><div class="${vCls}">${escHtml(v)}</div></div>`);
    };
    row('model', 'Model');
    row('fallback_model', 'Fallback');
    row('thinking', 'Thinking');
    row('effort', 'Effort');
    row('permission_mode', 'Permission');
    row('max_turns', 'Max turns');
    row('max_hops', 'Max hops');
    row('allow_bots', 'Allow bots');
    row('cwd', 'cwd');
    row('max_thinking_tokens', 'Max thinking tokens');
    row('fork_session', 'Fork session');
    row('enable_file_checkpointing', 'File checkpointing');
    // task_budget — show total
    const tbVal = c.task_budget ?? defs.task_budget;
    const tbSrc = c.task_budget !== undefined && c.task_budget !== null ? 'override' : 'default';
    const tbTotal = (tbVal && typeof tbVal === 'object') ? tbVal.total : tbVal;
    const tbTag = tbSrc === 'override' ? '<span class="source-tag override">override</span>' : '<span class="source-tag default">default</span>';
    const tbCls = (tbTotal === null || tbTotal === undefined || tbTotal === '') ? 'eff-val dim' : 'eff-val';
    rows.push(`<div><div class="eff-key">Task budget${tbTag}</div><div class="${tbCls}">${escHtml(tbTotal ?? '(unset)')}</div></div>`);
    // tools derived
    rows.push(`<div style="grid-column:1 / -1"><div class="eff-key">Active tools <span class="source-tag default">merged</span></div><div class="eff-val">${escHtml(activeTools.join(', ') || '(none)')}</div></div>`);
    if (blocked.length) {
      rows.push(`<div style="grid-column:1 / -1"><div class="eff-key">Blocked tools <span class="source-tag override">override</span></div><div class="eff-val">${escHtml(blocked.join(', '))}</div></div>`);
    }
    // skills
    const skills = c.skills || [];
    rows.push(`<div style="grid-column:1 / -1"><div class="eff-key">Skills <span class="source-tag ${skills.length?'override':'default'}">${skills.length?'override':'none'}</span></div><div class="${skills.length?'eff-val':'eff-val dim'}">${escHtml(skills.length ? skills.join(', ') : '(no skills enabled)')}</div></div>`);
    // channel
    rows.push(`<div><div class="eff-key">Channel</div><div class="eff-val">${escHtml(c.channel_id || '(unset)')}</div></div>`);
    // approval summary
    const ap = c.approval || {};
    const apDefs = defs.approval || {};
    const apEnabled = ap.enabled !== undefined ? ap.enabled : (apDefs.enabled !== undefined ? apDefs.enabled : true);
    const apSource = ap.enabled !== undefined ? 'override' : 'default';
    rows.push(`<div><div class="eff-key">Approval gate <span class="source-tag ${apSource}">${apSource}</span></div><div class="eff-val">${apEnabled ? 'enabled' : 'disabled'}</div></div>`);
    grid.innerHTML = rows.join('');
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

    // Save tool diff vs defaults: only persist deviations.
    //   allowed_tools = checked tools NOT in defaults (extras user turned on)
    //   disallowed_tools = UNchecked tools IN defaults (defaults user turned off)
    const _defs = (state.options && state.options.defaults) || {};
    const _defAllowed = new Set(_defs.allowed_tools || []);
    const _checkedSet = new Set(Array.from(document.querySelectorAll('#f-allowed_tools input:checked')).map(cb=>cb.dataset.tool));
    const _allTools = Array.from(document.querySelectorAll('#f-allowed_tools input')).map(cb=>cb.dataset.tool);
    c.allowed_tools = _allTools.filter(t => _checkedSet.has(t) && !_defAllowed.has(t));
    c.disallowed_tools = _allTools.filter(t => !_checkedSet.has(t) && _defAllowed.has(t));
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

    // Advanced (Claude Agent SDK) fields — only set when non-blank / non-inherit
    if (s('f-cwd')) c.cwd = s('f-cwd');
    if (s('f-max_thinking_tokens')) c.max_thinking_tokens = parseInt(s('f-max_thinking_tokens'), 10);
    const fsVal = $('f-fork_session').value;
    if (fsVal !== 'inherit') c.fork_session = fsVal === 'true';
    if (s('f-task_budget')) c.task_budget = { total: parseInt(s('f-task_budget'), 10) };
    const efcVal = $('f-enable_file_checkpointing').value;
    if (efcVal !== 'inherit') c.enable_file_checkpointing = efcVal === 'true';
    const ofRaw = s('f-output_format');
    if (ofRaw) {
      try { c.output_format = JSON.parse(ofRaw); }
      catch(e) { throw new Error('output_format must be valid JSON: '+e.message); }
    }
    const sbRaw = s('f-sandbox');
    if (sbRaw) {
      try { c.sandbox = JSON.parse(sbRaw); }
      catch(e) { throw new Error('sandbox must be valid JSON: '+e.message); }
    }

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
  loadOptions().then(loadConfig).then(loadConnectors);

  // ---- connectors ----
  async function loadConnectors() {
    const r = await fetch(`/api/connectors?agent=${encodeURIComponent(AGENT_NAME)}`);
    if (!r.ok) { $('conn-grid').innerHTML = '<div class="dim">failed to load connectors</div>'; return; }
    const j = await r.json();
    const conns = j.connectors || [];
    const enabled = conns.filter(c => c.enabled).length;
    const needsSetup = conns.filter(c => c.kind === 'env_key' && !c.credentials_ready).length;
    $('conn-count').textContent = `— ${enabled}/${conns.length} connected${needsSetup?`, ${needsSetup} need .env setup`:''}`;
    const grid = $('conn-grid');
    grid.innerHTML = '';
    for (const c of conns) {
      grid.appendChild(buildConnCard(c));
    }
  }

  function buildConnCard(c) {
    const card = document.createElement('div');
    card.className = 'conn-card';
    if (c.enabled) card.classList.add('enabled');
    if (c.kind === 'env_key' && !c.credentials_ready) card.classList.add('needs-setup');
    const statusBadge = c.enabled
      ? (c.legacy_per_agent
          ? '<span class="conn-badge enabled" title="Using global .env value — click Edit to set a per-agent slug">connected (legacy)</span>'
          : '<span class="conn-badge enabled">connected</span>')
      : (c.kind === 'env_key' && !c.credentials_ready)
        ? '<span class="conn-badge needs-setup">needs .env</span>'
        : '<span class="conn-badge">available</span>';
    const kindChip = c.kind === 'mcp_note'
      ? '<span class="chip-default">MCP</span>'
      : '<span class="chip-override">API key</span>';
    const missingBlock = (c.kind === 'env_key' && !c.credentials_ready && c.missing_env_vars?.length)
      ? `<div class="conn-missing">Set in <code>.env</code>: ${c.missing_env_vars.map(v=>`<code>${escHtml(v)}</code>`).join(' ')}</div>`
      : '';
    const hasPerAgent = c.kind === 'env_key' && c.per_agent_env && Object.keys(c.per_agent_env).length > 0;
    let btn;
    if (c.enabled) {
      const editBtn = hasPerAgent
        ? `<button class="conn-btn" data-action="edit" data-id="${escAttr(c.id)}">Edit</button>`
        : '';
      btn = `${editBtn}<button class="conn-btn danger" data-action="disconnect" data-id="${escAttr(c.id)}">Disconnect</button>`;
    } else if (c.kind === 'env_key' && !c.credentials_ready) {
      btn = `<button class="conn-btn primary" data-action="setup" data-id="${escAttr(c.id)}">Add credentials</button>`;
    } else if (hasPerAgent) {
      // Global creds ready but this agent still needs its per-agent value — modal, not direct toggle.
      btn = `<button class="conn-btn primary" data-action="setup" data-id="${escAttr(c.id)}">Connect</button>`;
    } else {
      btn = `<button class="conn-btn primary" data-action="connect" data-id="${escAttr(c.id)}">Connect</button>`;
    }
    card.innerHTML = `
      <div class="conn-head">
        <span class="conn-emoji">${c.emoji || '🔌'}</span>
        <span>${escHtml(c.name)}</span>
        <span class="conn-cat">${escHtml(c.category || '')}</span>
      </div>
      <div class="conn-desc">${escHtml(c.description || '')}</div>
      ${missingBlock}
      <div class="conn-btn-row">
        ${btn}
        ${statusBadge}
        ${kindChip}
      </div>
    `;
    for (const actionBtn of card.querySelectorAll('[data-action]')) {
      actionBtn.onclick = async () => {
        const action = actionBtn.dataset.action;
        const id = actionBtn.dataset.id;
        if (action === 'setup' || action === 'edit') {
          openCredModal(c);
          return;
        }
        const originalLabel = actionBtn.textContent;
        actionBtn.disabled = true;
        actionBtn.textContent = action === 'connect' ? 'Connecting…' : 'Disconnecting…';
        try {
          const res = await fetch(`/api/agents/${AGENT_NAME}/connectors/${encodeURIComponent(id)}`, {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({enable: action === 'connect'}),
          });
          const j = await res.json();
          if (!res.ok) {
            const msg = j.detail?.message || j.detail?.error || j.detail || 'toggle failed';
            toast(msg, 'fail', 6000);
            actionBtn.disabled = false;
            actionBtn.textContent = originalLabel;
            return;
          }
          toast(`${action === 'connect' ? 'connected' : 'disconnected'} + restart signaled`, 'ok');
          await loadConnectors();
          await loadConfig();
        } catch(e) {
          toast('error: '+e.message, 'fail', 6000);
          actionBtn.disabled = false;
        }
      };
    }
    return card;
  }

  // ---- credential modal (paste-in-dashboard for env_key connectors) ----
  let _connModalCtx = null;

  function openCredModal(c) {
    _connModalCtx = c;
    $('conn-modal-title').innerHTML = `${c.emoji || '🔌'} Connect ${escHtml(c.name)}`;
    const helpLines = [];
    if (c.docs_help) helpLines.push(escHtml(c.docs_help));
    if (c.docs_url) helpLines.push(`<a href="${escAttr(c.docs_url)}" target="_blank" rel="noopener">Open provider dashboard →</a>`);
    $('conn-modal-help').innerHTML = helpLines.join('<br>');
    const fieldsDiv = $('conn-modal-fields');
    fieldsDiv.innerHTML = '';
    const vars = c.env_vars || [];
    const perAgent = c.per_agent_env || {};
    const perAgentVals = c.per_agent_values || {};
    // Group 1: global .env fields
    for (const v of vars) {
      if (v in perAgent) continue;  // handled below
      const already = !(c.missing_env_vars || []).includes(v);
      const label = document.createElement('label');
      label.innerHTML = `${escHtml(v)}${already ? ' <span class="dim" style="font-size:0.78em;">(already set — overwrite optional)</span>' : ''}`;
      const input = document.createElement('input');
      input.type = v.toLowerCase().includes('secret') || v.toLowerCase().includes('key') || v.toLowerCase().includes('token') || v.toLowerCase().includes('password') ? 'password' : 'text';
      input.dataset.var = v;
      input.dataset.scope = 'global';
      input.placeholder = already ? '(leave blank to keep)' : `paste ${v}`;
      input.autocomplete = 'off';
      input.spellcheck = false;
      label.appendChild(input);
      fieldsDiv.appendChild(label);
    }
    // Group 2: per-agent fields, if any
    if (Object.keys(perAgent).length) {
      const hr = document.createElement('div');
      hr.className = 'dim';
      hr.style.cssText = 'margin:0.8em 0 0.3em 0;font-size:0.82em;border-top:1px dashed var(--border);padding-top:0.6em;';
      hr.innerHTML = `Per-agent values (stored in <code>agents/${escHtml(AGENT_NAME)}/agent.yaml</code>)`;
      fieldsDiv.appendChild(hr);
      for (const v of Object.keys(perAgent)) {
        const meta = perAgent[v] || {};
        const existing = perAgentVals[v] || '';
        const label = document.createElement('label');
        const labelText = meta.label ? `${escHtml(meta.label)} <span class="dim" style="font-size:0.78em;">(${escHtml(v)})</span>` : escHtml(v);
        label.innerHTML = `${labelText}${existing ? ` <span class="dim" style="font-size:0.78em;">(current: ${escHtml(existing)})</span>` : ''}`;
        const input = document.createElement('input');
        input.type = 'text';
        input.dataset.var = v;
        input.dataset.scope = 'per_agent';
        input.value = existing;
        input.placeholder = meta.placeholder || `value for ${v}`;
        input.autocomplete = 'off';
        input.spellcheck = false;
        label.appendChild(input);
        fieldsDiv.appendChild(label);
      }
    }
    $('connModal').classList.remove('hidden');
    setTimeout(() => fieldsDiv.querySelector('input')?.focus(), 50);
  }

  function closeCredModal() {
    $('connModal').classList.add('hidden');
    _connModalCtx = null;
  }

  $('connCancel').onclick = closeCredModal;
  $('connModal').addEventListener('click', (e) => {
    if (e.target === $('connModal')) closeCredModal();
  });

  $('connSubmit').onclick = async () => {
    if (!_connModalCtx) return;
    const c = _connModalCtx;
    const submitBtn = $('connSubmit');
    const globalValues = {};
    const perAgentValues = {};
    const inputs = $('conn-modal-fields').querySelectorAll('input[data-var]');
    for (const inp of inputs) {
      const v = inp.value.trim();
      if (!v) continue;
      if (inp.dataset.scope === 'per_agent') {
        perAgentValues[inp.dataset.var] = v;
      } else {
        globalValues[inp.dataset.var] = v;
      }
    }
    // Validate: all missing global vars covered + every per-agent var has a value
    // (existing per-agent values count — the modal pre-fills them).
    const missing = c.missing_env_vars || [];
    const stillMissingGlobal = missing.filter(v => !(v in globalValues));
    if (stillMissingGlobal.length) {
      toast(`still missing: ${stillMissingGlobal.join(', ')}`, 'fail', 5000);
      return;
    }
    const perAgentKeys = Object.keys(c.per_agent_env || {});
    const existingPerAgent = c.per_agent_values || {};
    const stillMissingPerAgent = perAgentKeys.filter(
      v => !(perAgentValues[v] || existingPerAgent[v]));
    if (stillMissingPerAgent.length) {
      toast(`per-agent value required: ${stillMissingPerAgent.join(', ')}`, 'fail', 5000);
      return;
    }
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving…';
    try {
      // 1. Write global credentials to .env (only if any were actually provided)
      let envJ = { count: 0 };
      if (Object.keys(globalValues).length) {
        const envRes = await fetch('/api/env/write', {
          method: 'POST',
          headers: {'content-type': 'application/json'},
          body: JSON.stringify({ values: globalValues, connector_id: c.id }),
        });
        envJ = await envRes.json();
        if (!envRes.ok) {
          const msg = envJ.detail?.message || envJ.detail || envJ.error || 'env write failed';
          toast(msg, 'fail', 6000);
          submitBtn.disabled = false;
          submitBtn.textContent = 'Save & connect';
          return;
        }
      }
      // 2. Toggle connector on + pass per-agent values (stored in agent.yaml)
      submitBtn.textContent = 'Connecting…';
      const togRes = await fetch(`/api/agents/${AGENT_NAME}/connectors/${encodeURIComponent(c.id)}`, {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({ enable: true, per_agent_values: perAgentValues }),
      });
      const togJ = await togRes.json();
      if (!togRes.ok) {
        const msg = togJ.detail?.message || togJ.detail?.error || togJ.detail || 'toggle failed';
        toast('saved .env but toggle failed: ' + msg, 'fail', 6000);
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save & connect';
        return;
      }
      const globalCount = envJ.count || 0;
      const perAgentCount = Object.keys(perAgentValues).length;
      const bits = [];
      if (globalCount) bits.push(`${globalCount} to .env`);
      if (perAgentCount) bits.push(`${perAgentCount} per-agent in agent.yaml`);
      toast(`${c.name} connected (${bits.join(', ') || 'updated'})`, 'ok', 4500);
      closeCredModal();
      await loadConnectors();
      await loadConfig();
    } catch(e) {
      toast('error: ' + e.message, 'fail', 6000);
      submitBtn.disabled = false;
      submitBtn.textContent = 'Save & connect';
    }
  };

})();
"""
