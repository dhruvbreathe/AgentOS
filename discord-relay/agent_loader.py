"""Loads agent configurations from agents/<name>/agent.yaml into
ClaudeAgentOptions. One agent per Discord channel."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CRONTAB_RE = re.compile(r"\bcrontab\b")
_CRONTAB_READONLY_RE = re.compile(r"^\s+(-l|--list)\b")

import yaml
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

ROOT = Path(__file__).parent
AGENTS_DIR = ROOT / "agents"
GLOBAL_CONFIG = ROOT / "config.yaml"


@dataclass
class AgentConfig:
    name: str
    channel_id: str
    webhook_url: str | None
    system_prompt: str
    options: ClaudeAgentOptions
    allow_bots: bool = True
    tasks_dir: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _load_skills(agent_dir: Path, skill_files: list[str]) -> str:
    """Concatenate skill markdown files into one block appended to the
    system prompt. Mirrors how CLAUDE.md skills are surfaced."""
    parts: list[str] = []
    for rel in skill_files:
        p = (agent_dir / rel).resolve()
        if p.exists():
            parts.append(f"## Skill: {p.stem}\n\n{p.read_text()}")
    return "\n\n".join(parts)


# Order matters: identity first, then how to behave, who you serve,
# workspace contract, env-specific config, connected services, protocols.
LAYERED_FILES = [
    "IDENTITY.md",
    "SOUL.md",
    "USER.md",
    "AGENTS.md",
    "TOOLS.md",
    "INTEGRATIONS.md",
    "SCHEDULING.md",
]


def _has_unsafe_crontab(cmd: str) -> bool:
    """Scan every `crontab` occurrence. Anything other than -l/--list writes."""
    for m in _CRONTAB_RE.finditer(cmd):
        if not _CRONTAB_READONLY_RE.match(cmd[m.end() :]):
            return True
    return False


async def _block_raw_crontab(input_data, tool_use_id, context):
    """PreToolUse hook: deny raw `crontab` write invocations. Agents must go
    through `cron/install.py` which only touches the managed block."""
    if input_data.get("tool_name") != "Bash":
        return {}
    cmd = input_data.get("tool_input", {}).get("command", "") or ""
    if "crontab" not in cmd:
        return {}
    # Whitelist: anything through the managed installer.
    if "cron/install.py" in cmd:
        return {}
    # Allow read-only crontab usage.
    if not _has_unsafe_crontab(cmd):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Raw `crontab` write is blocked. Use `python "
                "/Users/celainc/Developers/ClaudeAgentSDK/discord-relay/"
                "cron/install.py` to manage the discord-relay block. "
                "See SCHEDULING.md."
            ),
        }
    }


def _load_layered_prompt(agent_dir: Path) -> str:
    """Concatenate OpenClaw-style per-agent files (if present) into a single
    system prompt. Each file starts with its own H1, so we just separate with
    blank lines."""
    chunks: list[str] = []
    for name in LAYERED_FILES:
        p = agent_dir / name
        if p.exists():
            chunks.append(p.read_text().rstrip())
    return "\n\n".join(chunks)


def load_global() -> dict[str, Any]:
    return _load_yaml(GLOBAL_CONFIG)


def _resolve_vault_path(global_cfg: dict[str, Any]) -> str | None:
    env_var = global_cfg.get("vault_path_env", "VAULT_PATH")
    return os.environ.get(env_var)


def load_agent(name: str) -> AgentConfig:
    agent_dir = AGENTS_DIR / name
    cfg_path = agent_dir / "agent.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"No agent config at {cfg_path}")

    agent_cfg = _load_yaml(cfg_path)
    global_cfg = load_global()
    defaults = global_cfg.get("defaults", {}) or {}

    # System prompt: layered files (SOUL/IDENTITY/AGENTS/...) + optional
    # legacy system_prompt.md + skills.
    layered = _load_layered_prompt(agent_dir)
    legacy_name = agent_cfg.get("system_prompt_file", "system_prompt.md")
    legacy_sp = ""
    if legacy_name:
        legacy_sp_file = agent_dir / legacy_name
        if legacy_sp_file.is_file():
            legacy_sp = legacy_sp_file.read_text()
    skills = _load_skills(agent_dir, agent_cfg.get("skills", []) or [])

    system_prompt = "\n\n".join(
        [p for p in (layered, legacy_sp.rstrip(), skills) if p]
    )

    # Webhook URL (for outbound posting via cron or cross-agent replies)
    webhook_env = agent_cfg.get("webhook_url_env")
    webhook_url = os.environ.get(webhook_env) if webhook_env else None

    # Merge tool lists: defaults + agent-specific
    allowed = list(
        {
            *(defaults.get("allowed_tools") or []),
            *(agent_cfg.get("allowed_tools") or []),
        }
    )
    disallowed = list(
        {
            *(defaults.get("disallowed_tools") or []),
            *(agent_cfg.get("disallowed_tools") or []),
        }
    )

    cwd = agent_cfg.get("cwd") or _resolve_vault_path(global_cfg)

    mcp_servers = agent_cfg.get("mcp_servers") or {}

    add_dirs = list(
        {
            *(defaults.get("add_dirs") or []),
            *(agent_cfg.get("add_dirs") or []),
        }
    )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt or None,
        allowed_tools=allowed,
        disallowed_tools=disallowed,
        permission_mode=agent_cfg.get("permission_mode")
        or defaults.get("permission_mode"),
        max_turns=agent_cfg.get("max_turns") or defaults.get("max_turns"),
        model=agent_cfg.get("model") or defaults.get("model"),
        cwd=cwd,
        add_dirs=add_dirs,
        mcp_servers=mcp_servers,
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[_block_raw_crontab]),
            ],
        },
        include_partial_messages=True,  # enable token-level streaming
    )

    return AgentConfig(
        name=name,
        channel_id=str(agent_cfg["channel_id"]),
        webhook_url=webhook_url,
        system_prompt=system_prompt,
        options=options,
        allow_bots=bool(
            agent_cfg.get("allow_bots", defaults.get("allow_bots", True))
        ),
        tasks_dir=agent_dir / "tasks",
        raw=agent_cfg,
    )


def load_all_agents() -> dict[str, AgentConfig]:
    """Load every agent.yaml under agents/. Returns dict keyed by channel_id
    for fast routing in the bot."""
    if not AGENTS_DIR.exists():
        return {}
    out: dict[str, AgentConfig] = {}
    for child in AGENTS_DIR.iterdir():
        if child.name.startswith((".", "_")):
            continue  # skip _template/, _base/, hidden dirs
        if child.is_dir() and (child / "agent.yaml").exists():
            cfg = load_agent(child.name)
            out[cfg.channel_id] = cfg
    return out


def load_agent_by_channel(channel_id: str) -> AgentConfig | None:
    for cfg in load_all_agents().values():
        if cfg.channel_id == str(channel_id):
            return cfg
    return None
