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
SHARED_FILES = [
    "HUMANIZER.md",
    "EXPRESSION.md",
    "AGENT_COMMS.md",
    "SUBAGENTS.md",
    "MEMORY_STACK.md",  # architecture doc; distinct from per-agent MEMORY.md
    "WORKSPACE.md",     # canonical per-agent folder layout (doctor/status/audit depend on this)
    "MODELS.md",        # catalog of available Claude models + when to use which
    "APPROVALS.md",     # operator-reaction gate for dangerous Bash
    "CAVEMAN.md",       # compressed communication mode (~75% token cut on non-customer output)
]


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


def _resolve_skill_ref(agent_dir: Path, ref: str) -> Path | None:
    """Resolve a skill reference into a filesystem path.

    Supported forms:
      - `skill:<name>` → shared/skills/<name>/SKILL.md   (bundle convention)
      - `local:<name>` → <agent_dir>/skills/<name>/SKILL.md (per-agent)
      - `<relative-path>.md` → legacy path form, resolved against agent_dir

    Returns the resolved Path or None if nothing matched.
    """
    if ref.startswith("skill:"):
        name = ref.split(":", 1)[1].strip()
        candidate = SHARED_DIR / "skills" / name / "SKILL.md"
        if candidate.exists():
            return candidate
        # Back-compat: flat file form shared/skills/<name>.md
        flat = SHARED_DIR / "skills" / f"{name}.md"
        return flat if flat.exists() else None
    if ref.startswith("local:"):
        name = ref.split(":", 1)[1].strip()
        candidate = agent_dir / "skills" / name / "SKILL.md"
        if candidate.exists():
            return candidate
        flat = agent_dir / "skills" / f"{name}.md"
        return flat if flat.exists() else None
    # Legacy: relative path from agent_dir
    p = (agent_dir / ref).resolve()
    return p if p.exists() else None


def _load_skills(agent_dir: Path, skill_files: list[str]) -> str:
    """Concatenate skill markdown files into one block appended to the
    system prompt. Supports `skill:<name>`, `local:<name>`, and legacy
    relative-path refs. Mirrors how CLAUDE.md skills are surfaced."""
    parts: list[str] = []
    for ref in skill_files:
        p = _resolve_skill_ref(agent_dir, ref)
        if p is None:
            continue
        # Stem-only title for single files; parent dir name for bundles.
        title = p.parent.name if p.name == "SKILL.md" else p.stem
        parts.append(f"## Skill: {title}\n\n{p.read_text()}")
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
    "MEMORY.md",  # curated facts about people/places/preferences (distinct from LEARNINGS)
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


def _build_approval_hook(
    agent_name: str,
    webhook_url: str | None,
    patterns: list,
    timeout_seconds: float,
    poll_interval_seconds: float,
    enabled: bool,
):
    """Build a PreToolUse hook that gates dangerous Bash commands behind
    a Discord reaction approval. Closure-captures agent context."""
    from approval_gate import compile_patterns, match_dangerous, request_approval

    if not enabled or not webhook_url or not patterns:
        async def _noop(input_data, tool_use_id, context):
            return {}
        return _noop

    compiled = compile_patterns(patterns)

    async def _gate(input_data, tool_use_id, context):
        if input_data.get("tool_name") != "Bash":
            return {}
        cmd = input_data.get("tool_input", {}).get("command", "") or ""
        hit = match_dangerous(cmd, compiled)
        if not hit:
            return {}
        decision, detail = await request_approval(
            webhook_url=webhook_url,
            agent_name=agent_name,
            tool_name="Bash",
            command=cmd,
            reason=f"Matched dangerous pattern: `{hit}`",
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval_seconds,
        )
        if decision == "approve":
            return {}  # pass through
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Approval gate: {decision} ({detail}). The command "
                    f"matched a dangerous pattern (`{hit}`). If the operator "
                    f"actually wants this to run, they can react ✅ to the "
                    f"approval message in Discord, or re-ask with an explicit "
                    f"operator instruction after approving."
                ),
            }
        }

    return _gate


async def _block_raw_crontab(input_data, tool_use_id, context):
    """PreToolUse hook: deny raw `crontab` write invocations. Agents should
    use `scheduler/install.py` (launchd) for new work. The legacy
    `cron/install.py` is kept whitelisted for backward compat but hangs
    in-sandbox on macOS due to TCC privacy gating."""
    if input_data.get("tool_name") != "Bash":
        return {}
    cmd = input_data.get("tool_input", {}).get("command", "") or ""
    if "crontab" not in cmd:
        return {}
    # Whitelist: both the new scheduler and the legacy installer.
    if "scheduler/install.py" in cmd or "cron/install.py" in cmd:
        return {}
    # Allow read-only crontab usage.
    if not _has_unsafe_crontab(cmd):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Raw `crontab` write blocked. Use the launchd-based "
                "scheduler: `python /Users/celainc/Developers/ClaudeAgentSDK/"
                "discord-relay/scheduler/install.py --apply`. That bypasses "
                "the macOS TCC gate that hangs raw `crontab` writes in the "
                "sandbox. See SCHEDULING.md."
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

    # Parse mcp_servers early so note-only entries can be folded into the
    # system prompt as "integrations available" hints.
    raw_mcp = agent_cfg.get("mcp_servers") or {}
    mcp_servers: dict[str, Any] = {}
    mcp_notes: list[tuple[str, str]] = []
    for mcp_name, mcp_cfg in raw_mcp.items():
        if isinstance(mcp_cfg, dict) and (
            "command" in mcp_cfg or "url" in mcp_cfg or "instance" in mcp_cfg
        ):
            mcp_servers[mcp_name] = mcp_cfg
        else:
            note = (
                (mcp_cfg or {}).get("note") if isinstance(mcp_cfg, dict) else str(mcp_cfg)
            )
            mcp_notes.append((mcp_name, note or "(no description)"))

    mcp_note_block = ""
    if mcp_notes:
        lines = [f"- **{n}** — {d}" for n, d in mcp_notes]
        mcp_note_block = (
            "## Integrations available via parent MCP\n\n"
            "These services are reachable through the Claude Code CLI's "
            "shared MCP servers (not a per-agent stdio transport). Use them "
            "when the task requires them.\n\n" + "\n".join(lines)
        )

    # System prompt: shared universals + layered per-agent files + optional
    # legacy system_prompt.md + skills + inline integration notes.
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
        [
            p for p in (shared, layered, legacy_sp.rstrip(), skills, mcp_note_block)
            if p
        ]
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

    # mcp_servers was parsed above (split into valid configs + prompt notes).

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

    # Approval gate config — merge per-agent override on top of defaults.
    _approval_defaults = defaults.get("approval") or {}
    _approval_override = agent_cfg.get("approval") or {}
    approval_cfg = {**_approval_defaults, **_approval_override}
    approval_hook = _build_approval_hook(
        agent_name=name,
        webhook_url=webhook_url,
        patterns=approval_cfg.get("dangerous_patterns") or [],
        timeout_seconds=float(approval_cfg.get("timeout_seconds", 60)),
        poll_interval_seconds=float(approval_cfg.get("poll_interval_seconds", 2.5)),
        enabled=bool(approval_cfg.get("enabled", True)),
    )

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
        effort=agent_cfg.get("effort") or defaults.get("effort"),
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
                # Order matters: crontab guard first (structural rule),
                # approval gate second (pattern-based, may await user).
                HookMatcher(matcher="Bash", hooks=[_block_raw_crontab]),
                HookMatcher(matcher="Bash", hooks=[approval_hook]),
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
    for fast routing in the bot. If two agents claim the same channel_id,
    log a loud warning — silent overwrite previously made research-labs
    disappear because main had the same channel in extra_channel_ids."""
    if not AGENTS_DIR.exists():
        return {}
    import logging
    log = logging.getLogger("agent-loader")
    out: dict[str, AgentConfig] = {}
    for child in sorted(AGENTS_DIR.iterdir()):
        if child.name.startswith((".", "_")):
            continue  # skip _template/, _base/, hidden dirs
        if child.is_dir() and (child / "agent.yaml").exists():
            cfg = load_agent(child.name)
            for cid in cfg.channel_ids:
                if cid in out and out[cid].name != cfg.name:
                    log.warning(
                        "channel_id %s claimed by both %s and %s — %s wins "
                        "(loaded later). Remove the duplicate from one of "
                        "the agent.yaml files.",
                        cid, out[cid].name, cfg.name, cfg.name,
                    )
                out[cid] = cfg
    return out


def load_agent_by_channel(channel_id: str) -> AgentConfig | None:
    for cfg in load_all_agents().values():
        if cfg.channel_id == str(channel_id):
            return cfg
    return None
