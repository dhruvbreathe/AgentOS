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
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

ROOT = Path(__file__).parent
AGENTS_DIR = ROOT / "agents"
SHARED_DIR = ROOT / "shared"
GLOBAL_CONFIG = ROOT / "config.yaml"

# Universal files that get prepended to every agent's system prompt.
# Concise, hard rules that don't vary per agent (writing style, safety, etc.).
SHARED_FILES = ["HUMANIZER.md", "EXPRESSION.md", "AGENT_COMMS.md", "SUBAGENTS.md"]


@dataclass
class AgentConfig:
    name: str
    channel_ids: list[str]  # primary first, any extras after
    webhook_url: str | None
    system_prompt: str
    options: ClaudeAgentOptions
    bot_token: str | None = None  # None → use default DISCORD_BOT_TOKEN
    allow_bots: bool = True
    tasks_dir: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def channel_id(self) -> str:
        """Primary channel — used for webhook creation and as the canonical
        target for `send_to_agent`."""
        return self.channel_ids[0]


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
    "LEARNINGS.md",
]


def _has_unsafe_crontab(cmd: str) -> bool:
    """Scan every `crontab` occurrence. Anything other than -l/--list writes."""
    for m in _CRONTAB_RE.finditer(cmd):
        if not _CRONTAB_READONLY_RE.match(cmd[m.end() :]):
            return True
    return False


def _load_subagents(agent_dir: Path, cfg: dict) -> dict[str, AgentDefinition] | None:
    """Build AgentDefinition map from agent.yaml `subagents:` entries.

    Each subagent has a name, description, prompt, optional tool list and
    model. Lets the parent agent spawn a scoped specialist via the SDK's
    Task tool without polluting any channel.
    """
    subs_cfg = cfg.get("subagents")
    if not subs_cfg:
        return None
    out: dict[str, AgentDefinition] = {}
    for name, sub in subs_cfg.items():
        prompt = sub.get("prompt", "")
        # Allow `prompt_file: skills/research.md` pattern — resolve relative to agent dir.
        pf = sub.get("prompt_file")
        if pf:
            p = (agent_dir / pf).resolve()
            if p.is_file():
                prompt = p.read_text() + ("\n\n" + prompt if prompt else "")
        out[name] = AgentDefinition(
            description=sub.get("description", ""),
            prompt=prompt,
            tools=sub.get("tools"),
            model=sub.get("model"),
            maxTurns=sub.get("max_turns"),
        )
    return out or None


def _build_session_log_hooks(agent_name: str):
    """Stop + PreCompact hooks that persist a lightweight session marker
    to agents/<name>/memory/YYYY-MM-DD.md at end-of-turn and before
    auto-compaction. The full trajectory is already JSONL-logged; this
    drops a human-readable breadcrumb the agent reads next session."""

    mem_dir = AGENTS_DIR / agent_name / "memory"

    def _append(line: str) -> None:
        try:
            from datetime import datetime, timezone
            mem_dir.mkdir(parents=True, exist_ok=True)
            path = mem_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    async def on_stop(input_data, tool_use_id, context):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        _append(f"- {ts}  turn ended")
        return {}

    async def on_precompact(input_data, tool_use_id, context):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        trigger = input_data.get("trigger", "auto") if isinstance(input_data, dict) else "auto"
        _append(f"- {ts}  PRE-COMPACT ({trigger}) — context about to be compressed")
        return {}

    return on_stop, on_precompact


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


def _load_shared_prompt() -> str:
    """Universal rules that apply to every agent (writing style, etc.)."""
    chunks: list[str] = []
    for name in SHARED_FILES:
        p = SHARED_DIR / name
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

    # System prompt: shared universals + layered per-agent files + optional
    # legacy system_prompt.md + skills.
    shared = _load_shared_prompt()
    layered = _load_layered_prompt(agent_dir)
    legacy_name = agent_cfg.get("system_prompt_file", "system_prompt.md")
    legacy_sp = ""
    if legacy_name:
        legacy_sp_file = agent_dir / legacy_name
        if legacy_sp_file.is_file():
            legacy_sp = legacy_sp_file.read_text()
    skills = _load_skills(agent_dir, agent_cfg.get("skills", []) or [])

    system_prompt = "\n\n".join(
        [p for p in (shared, layered, legacy_sp.rstrip(), skills) if p]
    )

    # Webhook URL (for outbound posting via cron or cross-agent replies)
    webhook_env = agent_cfg.get("webhook_url_env")
    webhook_url = os.environ.get(webhook_env) if webhook_env else None

    # Per-agent bot token (optional — falls back to DISCORD_BOT_TOKEN in bot.py)
    bot_token_env = agent_cfg.get("bot_token_env")
    bot_token = os.environ.get(bot_token_env) if bot_token_env else None

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

    # Thinking config. Adaptive = model decides when to think; surfaces
    # ThinkingBlock when it does, skips when the task is trivial.
    thinking_cfg = agent_cfg.get("thinking") or defaults.get("thinking")
    if isinstance(thinking_cfg, str):
        thinking_cfg = {"type": thinking_cfg}  # "adaptive" / "enabled" / "disabled"

    # Settings-source isolation. [project] means agents won't inherit
    # ~/.claude/settings.json (the operator's personal Claude Code env).
    setting_sources = agent_cfg.get("setting_sources") or defaults.get(
        "setting_sources"
    )

    # Per-agent env vars. Two ways to declare them in agent.yaml:
    #   env_passthrough: [VAR1, VAR2]  → forwarded from current shell
    #   env: {LITERAL: "value"}        → literal key/value pairs
    env_out: dict[str, str] = {}
    for var in agent_cfg.get("env_passthrough", []) or []:
        val = os.environ.get(var)
        if val is not None:
            env_out[var] = val
    for k, v in (agent_cfg.get("env") or {}).items():
        env_out[k] = str(v)

    # Sandbox settings — macOS/Linux bash sandboxing. Opt-in per agent
    # (default off); when enabled, bash is isolated from filesystem/network
    # beyond declared permissions.
    sandbox_cfg = agent_cfg.get("sandbox") or defaults.get("sandbox")

    subagents = _load_subagents(agent_dir, agent_cfg)
    on_stop, on_precompact = _build_session_log_hooks(name)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt or None,
        allowed_tools=allowed,
        disallowed_tools=disallowed,
        permission_mode=agent_cfg.get("permission_mode")
        or defaults.get("permission_mode"),
        max_turns=agent_cfg.get("max_turns") or defaults.get("max_turns"),
        model=agent_cfg.get("model") or defaults.get("model"),
        fallback_model=agent_cfg.get("fallback_model")
        or defaults.get("fallback_model"),
        cwd=cwd,
        add_dirs=add_dirs,
        env=env_out,
        setting_sources=setting_sources,
        sandbox=sandbox_cfg,
        mcp_servers=mcp_servers,
        thinking=thinking_cfg,
        agents=subagents,
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[_block_raw_crontab]),
            ],
            "Stop": [HookMatcher(hooks=[on_stop])],
            "PreCompact": [HookMatcher(hooks=[on_precompact])],
        },
        include_partial_messages=True,  # enable token-level streaming
    )

    primary = str(agent_cfg["channel_id"])
    extras = [str(c) for c in (agent_cfg.get("extra_channel_ids") or [])]
    channel_ids = [primary, *extras]

    return AgentConfig(
        name=name,
        channel_ids=channel_ids,
        webhook_url=webhook_url,
        system_prompt=system_prompt,
        options=options,
        bot_token=bot_token,
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
            for cid in cfg.channel_ids:
                out[cid] = cfg
    return out


def load_agent_by_channel(channel_id: str) -> AgentConfig | None:
    for cfg in load_all_agents().values():
        if cfg.channel_id == str(channel_id):
            return cfg
    return None
