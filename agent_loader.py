"""Loads agent configurations from agents/<name>/agent.yaml into
ClaudeAgentOptions. One agent per Discord channel."""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CRONTAB_RE = re.compile(r"\bcrontab\b")
_CRONTAB_READONLY_RE = re.compile(r"^\s+(-l|--list)\b")

import yaml
from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

import session_store_supabase
from session_store_file import FileSessionStore

ROOT = Path(__file__).parent
AGENTS_DIR = ROOT / "agents"
SHARED_DIR = ROOT / "shared"
GLOBAL_CONFIG = ROOT / "config.yaml"

# One store instance per backend/target, shared by every agent in this
# process — the per-key asyncio locks inside the stores only serialise
# appends if all agents route through the same instance.
_file_session_stores: dict[str, FileSessionStore] = {}
_supabase_session_store: list[object] = []  # [store] once built; [] = unbuilt
_supabase_fallback_logged = False


def _get_file_session_store(root: Path) -> FileSessionStore:
    key = str(root)
    store = _file_session_stores.get(key)
    if store is None:
        store = FileSessionStore(root)
        _file_session_stores[key] = store
    return store


def _get_session_store(ss_cfg: dict) -> object:
    """Resolve the configured backend; supabase falls back to file when the
    Agent NS env isn't wired, so a missing key degrades loudly-but-safely."""
    global _supabase_fallback_logged
    backend = str(ss_cfg.get("backend") or "file").lower()
    if backend == "supabase":
        if not _supabase_session_store:
            _supabase_session_store.append(session_store_supabase.from_env())
        store = _supabase_session_store[0]
        if store is not None:
            return store
        if not _supabase_fallback_logged:
            logging.getLogger("agent-loader").warning(
                "session_store backend=supabase but SUPABASE_AGENT_NS_URL/"
                "SERVICE_ROLE_KEY missing — falling back to file store"
            )
            _supabase_fallback_logged = True
    ss_dir = Path(ss_cfg.get("dir") or "logs/session_store")
    if not ss_dir.is_absolute():
        ss_dir = ROOT / ss_dir
    return _get_file_session_store(ss_dir)

# Universal files that get prepended to every agent's system prompt.
# Concise, hard rules that don't vary per agent (writing style, safety, etc.).
SHARED_FILES = [
    "HUMANIZER.md",
    "EXPRESSION.md",
    "LESSONS.md",   # Phase-0 (2026-07-05): fleet-wide hard lessons — stops per-agent re-learning
    "AGENT_COMMS.md",
    "SUBAGENTS.md",
    "MEMORY_STACK.md",  # architecture doc; distinct from per-agent MEMORY.md
    "WORKSPACE.md",     # canonical per-agent folder layout (doctor/status/audit depend on this)
    "MODELS.md",        # catalog of available Claude models + when to use which
    "APPROVALS.md",     # operator-reaction gate for dangerous Bash
    "CAVEMAN.md",       # compressed communication mode (~75% token cut on non-customer output)
    "CONTINUATION.md",  # no-false-promises rule + scripts/defer.py for legitimate self-deferral
    "FILE_DELIVERY.md", # always attach files via webhook, not vault paths (operator rule 2026-05-14)
    "VISUALS.md",       # visual-first output: charts for 3+ numbers, Components V2 for structure (operator hard rule 2026-06-09)
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

# Stripped layer for `bootstrap: lite` cron tasks. No SOUL/USER/AGENTS/
# SCHEDULING/LEARNINGS — those are personality/process files that don't
# help a single-shot status ping or hygiene job. Keep identity (so the
# agent still posts as itself), tools (so paths work), integrations
# (so HTTP-API recipes are reachable), and curated MEMORY facts.
# Net effect: ~70% token cut on each lite cron fire vs. full bootstrap.
LAYERED_FILES_LITE = [
    "IDENTITY.md",
    "TOOLS.md",
    "INTEGRATIONS.md",
    "MEMORY.md",
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


def _build_post_write_lint_hook(agent_name: str, enabled: bool = True):
    """PostToolUse hook on Write/Edit/MultiEdit: lint the file that was
    just written.

    Observational — never blocks the write (the tool has already run by the
    time PostToolUse fires). Warnings + errors land in:
      - The agent's trajectory log (always, via this hook's return)
      - additionalContext for the next turn (so the agent sees its mistake)

    If a linter is missing on the host, the file is silently skipped — no
    noise. The hook is fail-open by design; it should never break a session.
    """
    if not enabled:
        async def _noop(input_data, tool_use_id, context):
            return {}
        return _noop

    from lint_gate import lint_file

    async def _on_post(input_data, tool_use_id, context):
        try:
            tool_name = input_data.get("tool_name", "")
            if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                return {}
            tool_input = input_data.get("tool_input", {}) or {}
            # Write/Edit use `file_path`; MultiEdit too; NotebookEdit uses
            # `notebook_path`.
            path_str = (
                tool_input.get("file_path")
                or tool_input.get("notebook_path")
                or ""
            )
            if not path_str:
                return {}
            result = lint_file(Path(path_str))
            if not result.is_problem:
                return {}
            # Surface as additionalContext on the next assistant turn so the
            # agent sees it and can self-correct.
            prefix = "⚠️" if result.status == "warn" else "❌"
            note = (
                f"{prefix} [post-write lint · {result.linter}] {Path(path_str).name}: "
                f"{result.message or result.status}"
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": note,
                }
            }
        except Exception:
            # Fail open — lint hook must never break the session.
            return {}

    return _on_post


def _build_pre_write_checkpoint_hook(agent_name: str, cwd: Path, enabled: bool = True):
    """PreToolUse hook on Write/Edit/MultiEdit: snapshot file state before
    the edit fires, so /rollback can restore later.

    Two modes auto-detected from cwd:
      - git-stash for code agents (cwd has .git)
      - file-snapshot for vault/non-git agents

    Fails open: if checkpoint creation errors out, the write still proceeds.
    The agent's safety bet is "rollback if you regret it", not "block if you
    might regret it". Debounced — fast-fire edits in the same turn bundle
    into one snapshot.
    """
    if not enabled:
        async def _noop(input_data, tool_use_id, context):
            return {}
        return _noop

    from checkpoint_gate import create_checkpoint

    async def _on_pre(input_data, tool_use_id, context):
        try:
            tool_name = input_data.get("tool_name", "")
            if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                return {}
            tool_input = input_data.get("tool_input", {}) or {}
            path_str = (
                tool_input.get("file_path")
                or tool_input.get("notebook_path")
                or ""
            )
            if not path_str:
                return {}
            p = Path(path_str)
            if not p.exists():
                # New file — nothing to checkpoint.
                return {}
            meta = create_checkpoint(
                agent_name,
                cwd,
                [p],
                tool=tool_name,
                message="auto pre-edit",
            )
            if meta is None:
                return {}
            # Don't surface in additionalContext on every edit — it'd flood
            # the trajectory with low-value noise. The checkpoint is
            # silent; agents discover it via /rollback list when they need
            # it.
            return {}
        except Exception:
            # Hook must never break the session.
            return {}

    return _on_pre


def _build_subagent_start_hook(parent_name: str):
    """SubagentStart hook: prepend parent attribution + scope reminder into the
    subagent's session as additionalContext.

    Inspired by OpenClaw's `sessions_spawn` first-message injection — instead of
    relying on the parent agent to remember to add context to every Task prompt,
    the SDK injects it automatically. Subagent gets:
      - who spawned them (parent agent name)
      - reminder they are scoped and should fail fast on missing context
      - clear instruction to focus on the immediate user message

    Cost: ~150 tokens of system context per subagent spawn. Worth it for less
    "subagent guesses at what the parent wanted" failure mode.
    """

    async def _on_start(input_data, tool_use_id, context):
        agent_type = (
            input_data.get("agent_type", "subagent") if isinstance(input_data, dict) else "subagent"
        )
        ctx = (
            f"[Subagent context]\n"
            f"- Spawned by: `{parent_name}` (parent agent)\n"
            f"- Your agent_type: `{agent_type}`\n"
            f"- You are a scoped specialist. Focus on the task in the user "
            f"message; do not invent adjacent work.\n"
            f"- If essential context is missing, fail fast with a clear "
            f"question rather than guessing.\n"
            f"- Return a tight, structured answer. The parent will synthesize."
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": ctx,
            }
        }

    return _on_start


def _build_approval_hook(
    agent_name: str,
    webhook_url: str | None,
    patterns: list,
    timeout_seconds: float,
    poll_interval_seconds: float,
    enabled: bool,
    operator_id: str | None = None,
):
    """Build a PreToolUse hook that gates dangerous Bash commands behind
    a Discord reaction approval. Closure-captures agent context.

    A8 (2026-07-01): when `operator_id` is configured and the relay bot
    token is available, only the operator's reaction counts. Both absent →
    legacy any-reaction behaviour."""
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
            operator_id=operator_id,
            bot_token=os.environ.get("DISCORD_BOT_TOKEN"),
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


def _build_write_gate_hook(
    agent_name: str,
    webhook_url: str | None,
    protected_paths: list,
    timeout_seconds: float,
    poll_interval_seconds: float,
    enabled: bool,
    operator_id: str | None = None,
):
    """A2 (2026-07-02): gate Write/Edit on protected paths behind the same
    Discord reaction approval as dangerous Bash. Closes the bypass where
    rm-equivalent damage lands via file tools instead of Bash, and makes
    APPROVALS.md's documented protections (DECISIONS.md, .env) actually
    hold for Write/Edit. Regexes match against the tool's file_path."""
    from approval_gate import compile_patterns, match_dangerous, request_approval

    if not enabled or not webhook_url or not protected_paths:
        async def _noop(input_data, tool_use_id, context):
            return {}
        return _noop

    compiled = compile_patterns(protected_paths)

    async def _gate(input_data, tool_use_id, context):
        tool = input_data.get("tool_name")
        if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            return {}
        ti = input_data.get("tool_input", {}) or {}
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if not path:
            return {}
        hit = match_dangerous(path, compiled)
        if not hit:
            return {}
        # Compact preview so the operator sees WHAT changes, not just where.
        if tool == "Write":
            body = (ti.get("content") or "")[:280]
            preview = f"{path}\n--- new content (first 280 chars) ---\n{body}"
        else:
            old = (ti.get("old_string") or "")[:140]
            new = (ti.get("new_string") or "")[:140]
            preview = f"{path}\n--- old ---\n{old}\n--- new ---\n{new}"
        decision, detail = await request_approval(
            webhook_url=webhook_url,
            agent_name=agent_name,
            tool_name=tool,
            command=preview,
            reason=f"Protected path: `{hit}`",
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval_seconds,
            operator_id=operator_id,
            bot_token=os.environ.get("DISCORD_BOT_TOKEN"),
        )
        if decision == "approve":
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Write gate: {decision} ({detail}). `{path}` is a "
                    f"protected path (`{hit}`). If this edit is genuinely "
                    f"wanted, the operator can react ✅ on the approval "
                    f"message in Discord and ask again."
                ),
            }
        }

    return _gate


def _build_facts_hygiene_hook(agent_name: str, enabled: bool = True):
    """PreToolUse gate on Write/Edit to Company/FACTS.md. Denies any write
    that would add a fact line missing its `conf:<tier>` provenance tag, so
    untagged guesses can't propagate into investor copy (the failure mode that
    leaked "4.8 stars" / "40% anxiety reduction"). Reuses the exact scan logic
    from scripts/check_facts_hygiene.py so the gate and the audit stay in sync.

    Fail-open on every error path: if the checker can't load or the proposed
    text can't be reconstructed, the write passes. A hygiene gate must never
    block legitimate work because of its own bug.
    """
    if not enabled:
        async def _noop(input_data, tool_use_id, context):
            return {}
        return _noop

    scan_fn = None
    try:
        scripts_dir = str(ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from check_facts_hygiene import scan as scan_fn  # type: ignore
    except Exception:
        scan_fn = None

    async def _gate(input_data, tool_use_id, context):
        if scan_fn is None:
            return {}
        tool = input_data.get("tool_name")
        if tool not in ("Write", "Edit"):
            return {}
        ti = input_data.get("tool_input", {}) or {}
        fp = str(ti.get("file_path", "") or "")
        if not fp.endswith("FACTS.md"):
            return {}
        try:
            if tool == "Write":
                proposed = ti.get("content", "") or ""
            else:  # Edit — reconstruct the resulting file text, then scan it
                old = ti.get("old_string", "")
                new = ti.get("new_string", "")
                try:
                    current = Path(fp).read_text()
                except Exception:
                    current = ""
                if ti.get("replace_all"):
                    proposed = current.replace(old, new)
                else:
                    proposed = current.replace(old, new, 1)
            findings = scan_fn(proposed)
        except Exception:
            return {}  # fail-open
        if not findings:
            return {}
        preview = "; ".join(
            f"L{f.get('line')} {f.get('issue')}: {f.get('text')}" for f in findings[:5]
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"FACTS.md hygiene gate: {len(findings)} fact line(s) lack a "
                    f"valid conf:<tier> provenance tag ({preview}). Every fact "
                    f"needs a provenance comment like "
                    f"`<!-- conf:confirmed | YYYY-MM-DD <agent> | source: X -->` "
                    f"with tier one of confirmed/estimate/unverified/stale. Add "
                    f"the tier + provenance and retry — this is what keeps "
                    f"untagged guesses out of investor-facing copy."
                ),
            }
        }

    return _gate


def _build_loop_guard_hook(agent_name: str, threshold: int = 5, enabled: bool = True):
    """Build a PreToolUse hook that aborts phantom / runaway tool-call loops
    early, before `max_turns` burns out the whole budget.

    Two failure modes are caught:

    1. CONSECUTIVE identical calls. We track, per session, the last
       (tool_name, fingerprint) where fingerprint is a stable hash of the
       JSON-serialized tool_input. When the SAME pair arrives >= `threshold`
       times in a row we DENY and tell the model to change approach.

       Why identical-input (not just same-tool) repetition is the right
       signal: legitimate loops vary their input — reading many files,
       grepping many patterns, editing many locations all change
       `tool_input` every call, so the fingerprint changes and the
       consecutive counter resets to 1. Only a genuinely stuck agent
       re-issues the byte-for-byte identical call. So this fires on the true
       phantom-loop signal and leaves normal fan-out work untouched (no false
       positives on "read 20 files").

    2. EMPTY / missing / whitespace tool_name. This is the empty-name
       phantom tool-call that Hermes #47967 dampened upstream; we keep a
       host-side backstop so a malformed stream can't spin even once.

    State is bounded:
      - one small entry per session: (last_key, count);
      - sessions are evicted LRU-style once we exceed _MAX_SESSIONS, and
        idle sessions older than _SESSION_TTL_S are dropped on access.
    Fail-open everywhere: any internal error lets the call through — a loop
    guard must never be the thing that breaks a healthy session.
    """
    if not enabled or threshold is None or int(threshold) < 2:
        # threshold < 2 is meaningless (would deny the first repeat); disable.
        async def _noop(input_data, tool_use_id, context):
            return {}
        return _noop

    import hashlib
    import json
    import time
    from collections import OrderedDict

    threshold = int(threshold)
    _MAX_SESSIONS = 256          # hard cap on tracked sessions (memory bound)
    _SESSION_TTL_S = 3600.0      # drop a session's counter after 1h idle

    # session_id -> {"key": (tool_name, fp) | None, "count": int, "ts": float}
    # OrderedDict so we can evict the least-recently-used session cheaply.
    _state: "OrderedDict[str, dict]" = OrderedDict()

    def _fingerprint(tool_input) -> str:
        try:
            blob = json.dumps(tool_input, sort_keys=True, default=str)
        except Exception:
            blob = repr(tool_input)
        return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]

    def _evict_if_needed(now: float) -> None:
        # Drop expired sessions first (cheap scan, bounded by _MAX_SESSIONS).
        stale = [s for s, v in _state.items() if now - v.get("ts", now) > _SESSION_TTL_S]
        for s in stale:
            _state.pop(s, None)
        # Then enforce the hard cap LRU-style.
        while len(_state) > _MAX_SESSIONS:
            _state.popitem(last=False)

    async def _guard(input_data, tool_use_id, context):
        try:
            if not isinstance(input_data, dict):
                return {}
            tool_name = input_data.get("tool_name")

            # (2) Empty / missing / whitespace tool_name → deny immediately.
            if not isinstance(tool_name, str) or not tool_name.strip():
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "Loop guard: empty or missing tool name. This is a "
                            "malformed / phantom tool call. STOP emitting empty "
                            "tool calls. Either produce a normal text reply to "
                            "the operator, or issue a well-formed tool call with "
                            "a concrete tool name and input."
                        ),
                    }
                }

            tool_input = input_data.get("tool_input", {})
            fp = _fingerprint(tool_input)
            key = (tool_name, fp)

            now = time.monotonic()
            sid = input_data.get("session_id") or f"agent:{agent_name}"

            entry = _state.get(sid)
            if entry is None:
                entry = {"key": key, "count": 1, "ts": now}
                _state[sid] = entry
            else:
                _state.move_to_end(sid)  # mark as recently used
                entry["ts"] = now
                if entry["key"] == key:
                    entry["count"] += 1
                else:
                    # (1-reset) A different call arrived — reset the counter.
                    entry["key"] = key
                    entry["count"] = 1

            _evict_if_needed(now)

            # (1) Consecutive-identical threshold reached → deny.
            if entry["count"] >= threshold:
                # Reset so that if the operator/model legitimately retries the
                # same call later it isn't insta-denied on the very next turn;
                # it gets a fresh run-up to the threshold.
                entry["count"] = 0
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Loop guard: `{tool_name}` has been called with the "
                            f"exact same input {threshold} times in a row. You are "
                            f"stuck in a loop and burning turns/tokens. STOP "
                            f"repeating this identical call. Change approach: vary "
                            f"the input, use a different tool, re-read the last "
                            f"tool result to see why it isn't advancing you, or "
                            f"reply to the operator explaining what's blocking "
                            f"you. Do NOT re-issue the same call."
                        ),
                    }
                }
            return {}
        except Exception:
            # Fail open — the loop guard must never break a healthy session.
            return {}

    return _guard


def _build_budget_hook(agent_name: str, monthly_budget: int | None,
                       warn_pct: float = 80.0, block_pct: float | None = None):
    """UserPromptSubmit hook — on each turn, check this agent's month-to-date
    token spend against `monthly_budget`. If over `warn_pct`, inject a warning
    into additionalContext. If over `block_pct` (opt-in), deny the turn.
    """
    async def _noop(input_data, tool_use_id, context):
        return {}
    if not monthly_budget or monthly_budget <= 0:
        return _noop

    async def _check(input_data, tool_use_id, context):
        try:
            import tasks as _t
            totals = _t.spend_totals()
            used = 0
            if agent_name in totals:
                t = totals[agent_name]
                used = (t.get("input_tokens") or 0) + (t.get("output_tokens") or 0)
            pct = (used * 100.0 / monthly_budget) if monthly_budget else 0
        except Exception:
            return {}
        if block_pct is not None and pct >= block_pct:
            # UserPromptSubmit blocking uses the TOP-LEVEL decision field;
            # permissionDecision inside hookSpecificOutput is ignored for
            # this event and the prompt would sail through.
            return {
                "decision": "block",
                "reason": (
                    f"Budget: {agent_name} is at {pct:.0f}% of monthly cap "
                    f"({used:,} / {monthly_budget:,} tokens). Hard block at "
                    f"{block_pct}%. Operator must raise the cap in "
                    f"agent.yaml or wait for next month."
                ),
            }
        if pct >= warn_pct:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        f"[budget notice] {agent_name} is at {pct:.0f}% of "
                        f"monthly token cap ({used:,} / {monthly_budget:,}). "
                        f"Be terse. Skip non-essential tool calls."
                    ),
                }
            }
        return {}
    return _check


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
    # Whitelist: the launchd scheduler only. The legacy cron/install.py
    # hangs in-sandbox (TCC gate) — no reason to let it through anymore.
    if "scheduler/install.py" in cmd:
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
                "scheduler: `python scheduler/install.py --apply` from the "
                "AgentOS project root. That bypasses the macOS TCC gate "
                "that hangs raw `crontab` writes in the sandbox. See "
                "SCHEDULING.md."
            ),
        }
    }


def _load_layered_prompt(agent_dir: Path, lite: bool = False) -> str:
    """Concatenate OpenClaw-style per-agent files (if present) into a single
    system prompt. Each file starts with its own H1, so we just separate with
    blank lines.

    When `lite=True`, only the minimum identity + reference files are loaded —
    used by `bootstrap: lite` cron tasks where SOUL/USER/AGENTS/SCHEDULING/
    LEARNINGS would be dead weight for a single-shot housekeeping job.
    """
    chunks: list[str] = []
    files = LAYERED_FILES_LITE if lite else LAYERED_FILES
    for name in files:
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


def load_agent(name: str, lite: bool = False) -> AgentConfig:
    """Load an agent's runtime config.

    `lite=True` strips the system prompt down to the bare minimum
    (IDENTITY + TOOLS + INTEGRATIONS + MEMORY, no SHARED files, no skills,
    no legacy system_prompt). Use only for `bootstrap: lite` cron tasks —
    single-shot housekeeping jobs where loading SOUL/USER/HUMANIZER/etc.
    would burn 30-50K tokens for no behavioural benefit.
    """
    agent_dir = AGENTS_DIR / name
    cfg_path = agent_dir / "agent.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"No agent config at {cfg_path}")

    agent_cfg = _load_yaml(cfg_path)
    global_cfg = load_global()
    defaults = global_cfg.get("defaults", {}) or {}

    # Parse mcp_servers early so note-only entries can be folded into the
    # system prompt as "integrations available" hints. Three buckets:
    #   - real MCP server cfg (command/url/instance)  -> pass through to SDK
    #   - type: mcp note                              -> "available via parent MCP" list
    #   - type: env note                              -> "available via HTTP API" list
    #                                                    with env vars + endpoint recipe
    raw_mcp = agent_cfg.get("mcp_servers") or {}
    mcp_servers: dict[str, Any] = {}
    mcp_notes: list[tuple[str, str]] = []
    env_integrations: list[dict] = []

    # Lazy-load connector registry so older agent.yaml entries (written before
    # the api_* fields existed) still get enriched at prompt-build time.
    _registry_by_id: dict[str, dict] = {}
    try:
        import yaml as _yaml  # type: ignore
        _reg_path = Path(__file__).resolve().parent / "connectors" / "registry.yaml"
        if _reg_path.exists():
            _reg = _yaml.safe_load(_reg_path.read_text()) or {}
            for c in _reg.get("connectors") or []:
                if c.get("id"):
                    _registry_by_id[c["id"]] = c
    except Exception:
        _registry_by_id = {}

    for mcp_name, mcp_cfg in raw_mcp.items():
        if isinstance(mcp_cfg, dict) and (
            "command" in mcp_cfg or "url" in mcp_cfg or "instance" in mcp_cfg
        ):
            mcp_servers[mcp_name] = mcp_cfg
            continue
        cfg = mcp_cfg if isinstance(mcp_cfg, dict) else {}
        note = cfg.get("note") if isinstance(mcp_cfg, dict) else str(mcp_cfg)
        note = note or "(no description)"
        # Registry fallback: if the yaml entry is bare (old format), pull the
        # recipe straight from the registry.
        reg = _registry_by_id.get(mcp_name) or {}
        if cfg.get("type") == "env" or reg.get("kind") == "env_key":
            env_integrations.append({
                "name": mcp_name,
                "note": note,
                "env_vars": cfg.get("env_vars") or reg.get("env_vars") or [],
                "api_base": cfg.get("api_base") or reg.get("api_base"),
                "api_auth": cfg.get("api_auth") or reg.get("api_auth"),
                "api_hints": cfg.get("api_hints") or reg.get("api_hints") or [],
                "docs_url": cfg.get("docs_url") or reg.get("docs_url"),
            })
        else:
            mcp_notes.append((mcp_name, note))

    # P0-2 MCP schema diet (2026-07-18 Wave 3). The claude.ai account
    # connectors (Apollo/Gmail/GCal/GDrive/Mixpanel/Supermetrics/higgsfield,
    # ~200 tools) ride into EVERY agent subprocess via the subscription
    # OAuth — context telemetry measured them at ~175k tokens of deferred
    # schemas per turn, fleet-wide. strict_mcp_config=True makes the CLI
    # mount ONLY options.mcp_servers (per-agent real configs above + the
    # per-turn agent_comms server relay.py injects), stripping account,
    # user (~/.claude.json) and project MCP.
    # Resolution order: explicit `strict_mcp_config` in agent.yaml always
    # wins; otherwise the fleet default applies EXCEPT for agents that
    # declare `type: mcp` notes — a declared parent-MCP dependency IS the
    # opt-out. Self-maintaining: document an integration in agent.yaml and
    # the agent keeps the connectors; no declaration, it diets.
    strict_mcp = agent_cfg.get("strict_mcp_config")
    if strict_mcp is None:
        strict_mcp = bool(defaults.get("strict_mcp_config", False)) and not mcp_notes
    strict_mcp = bool(strict_mcp)

    blocks: list[str] = []
    if mcp_notes:
        lines = [f"- **{n}** — {d}" for n, d in mcp_notes]
        blocks.append(
            "## Integrations available via parent MCP\n\n"
            "These services are reachable through the Claude Code CLI's "
            "shared MCP servers (not a per-agent stdio transport). Use them "
            "when the task requires them.\n\n" + "\n".join(lines)
        )
    if env_integrations:
        parts = [
            "## Integrations available via HTTP API (env-key)",
            "",
            "These services are **NOT** MCP tools. They're plain HTTP APIs you "
            "reach with `Bash` + `curl`, authed via environment variables the "
            "relay has injected for you. **Do not search for MCP tools for "
            "these** — `ToolSearch` will return nothing and you'll waste turns.",
            "",
        ]
        for it in env_integrations:
            parts.append(f"### {it['name']}")
            parts.append(f"{it['note']}")
            if it["env_vars"]:
                parts.append(f"- **Env vars available:** {', '.join(f'`${v}`' for v in it['env_vars'])}")
            if it["api_base"]:
                parts.append(f"- **API base:** `{it['api_base']}`")
            if it["api_auth"]:
                parts.append(f"- **Auth:** `{it['api_auth']}`")
            if it["api_hints"]:
                parts.append("- **Example calls:**")
                for hint in it["api_hints"]:
                    parts.append(f"  - `{hint}`")
            if it["docs_url"]:
                parts.append(f"- **Docs:** {it['docs_url']}")
            parts.append("")
        blocks.append("\n".join(parts).rstrip())

    mcp_note_block = "\n\n".join(blocks)

    # System prompt: shared universals + layered per-agent files + optional
    # legacy system_prompt.md + skills + inline integration notes.
    # `lite=True` skips SHARED + skills + legacy and uses LAYERED_FILES_LITE
    # for the layered chunk — keeps the integration notes (cheap, scope-aware).
    if lite:
        shared = ""
        layered = _load_layered_prompt(agent_dir, lite=True)
        legacy_sp = ""
        skills = ""
    else:
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
    # Resolve {AGENTOS_ROOT} placeholders used throughout the shared/layered
    # prompt files. Keeps committed docs portable: they reference
    # {AGENTOS_ROOT}/scheduler/install.py etc. and this line expands to the
    # current install location at runtime.
    system_prompt = system_prompt.replace("{AGENTOS_ROOT}", str(ROOT))

    # Webhook URL (for outbound posting via cron or cross-agent replies)
    webhook_env = agent_cfg.get("webhook_url_env")
    webhook_url = os.environ.get(webhook_env) if webhook_env else None

    # Per-agent bot token (optional — falls back to DISCORD_BOT_TOKEN in bot.py)
    bot_token_env = agent_cfg.get("bot_token_env")
    bot_token = os.environ.get(bot_token_env) if bot_token_env else None

    # Merge tool lists: defaults + agent-specific
    # Ordered dedup (not set()) so the tool list is deterministic across
    # loads/turns: defaults first, then per-agent additions, first occurrence
    # wins. Then subtract disallowed from allowed so a tool named in both
    # resolves to denied (least-privilege) with no ambiguity.
    def _ordered_dedup(*lists: list) -> list:
        seen: set = set()
        out: list = []
        for lst in lists:
            for item in lst or []:
                if item not in seen:
                    seen.add(item)
                    out.append(item)
        return out

    disallowed = _ordered_dedup(
        defaults.get("disallowed_tools") or [],
        agent_cfg.get("disallowed_tools") or [],
    )
    _disallowed_set = set(disallowed)
    allowed = [
        t
        for t in _ordered_dedup(
            defaults.get("allowed_tools") or [],
            agent_cfg.get("allowed_tools") or [],
        )
        if t not in _disallowed_set
    ]

    cwd = agent_cfg.get("cwd") or _resolve_vault_path(global_cfg)

    # mcp_servers was parsed above (split into valid configs + prompt notes).

    # Always include the AgentOS project root so agents can draft task files
    # under agents/<self>/tasks/ for the managed scheduler, read shared docs,
    # etc. Config-specified add_dirs layer on top.
    add_dirs = list(
        {
            str(ROOT),
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

    # Subscription-only enforcement (2026-07-03, operator directive after a
    # ~$20-in-2h API burn). The SDK's subprocess transport inherits the
    # ENTIRE bot os.environ, then merges options.env on top (options.env
    # wins). So if the bot was ever launched from a shell that exports
    # ANTHROPIC_API_KEY, every agent's Claude CLI subprocess would silently
    # bill API-per-token instead of using the Claude.ai subscription OAuth
    # (stored in the macOS Keychain). Blanking these keys in options.env
    # overrides any inherited value, forcing the CLI to fall back to the
    # subscription. Empty string reads as "unset" to the CLI's auth check.
    # Toggle off with `subscription_only: false` in config.yaml defaults or
    # a specific agent.yaml (e.g. an agent that genuinely needs API billing).
    _sub_only = agent_cfg.get("subscription_only")
    if _sub_only is None:
        _sub_only = defaults.get("subscription_only")
    if _sub_only is None:
        _sub_only = True
    if _sub_only:
        for _key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY"):
            env_out[_key] = ""

    # Sandbox settings — macOS/Linux bash sandboxing. Opt-in per agent
    # (default off); when enabled, bash is isolated from filesystem/network
    # beyond declared permissions.
    sandbox_cfg = agent_cfg.get("sandbox") or defaults.get("sandbox")

    subagents = _load_subagents(agent_dir, agent_cfg)
    # Phase-2 quality loops (2026-07-05): fleet-default subagents from
    # config.yaml `defaults.subagents`, merged UNDER per-agent ones —
    # agent.yaml wins on name collision. Definitions here must use inline
    # prompts (prompt_file would resolve against each agent's own dir,
    # which is wrong for shared definitions).
    _default_subs_cfg = defaults.get("subagents")
    if _default_subs_cfg:
        try:
            _default_subs = _load_subagents(
                agent_dir, {"subagents": _default_subs_cfg}
            )
            if _default_subs:
                subagents = {**_default_subs, **(subagents or {})}
        except Exception as _e:  # noqa: BLE001 — defaults must not break load
            logging.getLogger("agent-loader").warning(
                "[%s] default subagents skipped: %s", name, _e
            )
    on_stop, on_precompact = _build_session_log_hooks(name)
    on_subagent_start = _build_subagent_start_hook(name)

    # Post-write lint — per-agent opt-out via `lint_on_write: false`.
    # Default ON; observational only (never blocks). Cheap because each
    # linter early-exits when not installed on the host.
    _lint_enabled = agent_cfg.get(
        "lint_on_write",
        defaults.get("lint_on_write", True),
    )
    on_post_write_lint = _build_post_write_lint_hook(name, enabled=bool(_lint_enabled))

    # Pre-write checkpoint — per-agent opt-out via `checkpoint_on_write: false`.
    # Default ON for everyone except agents that don't write files (none yet).
    _checkpoint_enabled = agent_cfg.get(
        "checkpoint_on_write",
        defaults.get("checkpoint_on_write", True),
    )
    on_pre_write_checkpoint = _build_pre_write_checkpoint_hook(
        name, cwd, enabled=bool(_checkpoint_enabled),
    )

    # FACTS.md hygiene gate — deny writes that add untagged facts to the
    # shared Company/FACTS.md. Default ON; per-agent opt-out via
    # `facts_hygiene_gate: false`. Fail-open by construction.
    _facts_gate_enabled = agent_cfg.get(
        "facts_hygiene_gate",
        defaults.get("facts_hygiene_gate", True),
    )
    on_facts_hygiene = _build_facts_hygiene_hook(name, enabled=bool(_facts_gate_enabled))

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
        # A8: reactor identity. config approval.operator_id, env override.
        operator_id=str(
            approval_cfg.get("operator_id")
            or os.environ.get("DISCORD_OPERATOR_ID")
            or ""
        ) or None,
    )
    # A2: Write/Edit twin of the Bash gate — protected file paths need the
    # same ✅ before mutation. Shares timeout/operator config with approval.
    write_gate_hook = _build_write_gate_hook(
        agent_name=name,
        webhook_url=webhook_url,
        protected_paths=approval_cfg.get("protected_paths") or [],
        timeout_seconds=float(approval_cfg.get("timeout_seconds", 60)),
        poll_interval_seconds=float(approval_cfg.get("poll_interval_seconds", 2.5)),
        enabled=bool(approval_cfg.get("enabled", True)),
        operator_id=str(
            approval_cfg.get("operator_id")
            or os.environ.get("DISCORD_OPERATOR_ID")
            or ""
        ) or None,
    )

    # Loop guard config — mirrors the approval block. Aborts phantom /
    # runaway tool-call loops early instead of letting them burn all
    # max_turns. Per-agent override on top of config.yaml defaults; disable
    # with `loop_guard: {enabled: false}` in agent.yaml.
    _loop_guard_defaults = defaults.get("loop_guard") or {}
    _loop_guard_override = agent_cfg.get("loop_guard") or {}
    loop_guard_cfg = {**_loop_guard_defaults, **_loop_guard_override}
    loop_guard_hook = _build_loop_guard_hook(
        agent_name=name,
        threshold=int(loop_guard_cfg.get("threshold", 5)),
        enabled=bool(loop_guard_cfg.get("enabled", True)),
    )

    # Budget hook — warn / deny on monthly token cap. Per-agent override on
    # top of config.yaml defaults. Accepts either a flat int or a dict with
    # {monthly, warn_pct, block_pct}.
    _budget_default = defaults.get("budget_monthly_tokens")
    _budget_raw = agent_cfg.get("budget_monthly_tokens", _budget_default)
    _budget_total: int | None = None
    _warn_pct = 80.0
    _block_pct: float | None = None
    if isinstance(_budget_raw, (int, float)) and _budget_raw > 0:
        _budget_total = int(_budget_raw)
    elif isinstance(_budget_raw, dict):
        _m = _budget_raw.get("monthly") or _budget_raw.get("total")
        if _m:
            _budget_total = int(_m)
        if "warn_pct" in _budget_raw:
            _warn_pct = float(_budget_raw["warn_pct"])
        if "block_pct" in _budget_raw and _budget_raw["block_pct"] is not None:
            _block_pct = float(_budget_raw["block_pct"])
    budget_hook = _build_budget_hook(
        agent_name=name,
        monthly_budget=_budget_total,
        warn_pct=_warn_pct,
        block_pct=_block_pct,
    )

    # Additional SDK pass-through fields: thinking budget, session forking,
    # output format, file checkpointing, task budget. All optional — only set
    # on the dataclass when the agent config provides a value.
    _opts_extra: dict = {}
    _mtt = agent_cfg.get("max_thinking_tokens") or defaults.get("max_thinking_tokens")
    if _mtt is not None:
        _opts_extra["max_thinking_tokens"] = int(_mtt)
    _fs = agent_cfg.get("fork_session")
    if _fs is None:
        _fs = defaults.get("fork_session")
    if _fs is not None:
        _opts_extra["fork_session"] = bool(_fs)
    _of = agent_cfg.get("output_format") or defaults.get("output_format")
    if _of is not None:
        _opts_extra["output_format"] = _of
    _efc = agent_cfg.get("enable_file_checkpointing")
    if _efc is None:
        _efc = defaults.get("enable_file_checkpointing")
    if _efc is not None:
        _opts_extra["enable_file_checkpointing"] = bool(_efc)
    _tb = agent_cfg.get("task_budget") or defaults.get("task_budget")
    if _tb is not None:
        if isinstance(_tb, int):
            _opts_extra["task_budget"] = {"total": _tb}
        elif isinstance(_tb, dict) and "total" in _tb:
            _opts_extra["task_budget"] = {"total": int(_tb["total"])}

    # Session transcript mirror (SDK >= 0.2.110). One process-wide store
    # mirrors every session's JSONL — backend "supabase" (Agent Nervous
    # System project, schema=agents) or "file" (logs/session_store/).
    # Resume materializes from the mirror once a session has been mirrored;
    # sessions the store has never seen fall through to the CLI's own local
    # transcripts, so pre-migration conversations keep resuming unchanged.
    # Skipped when file checkpointing is on — the SDK forbids the combo
    # (validate_session_store_options raises). Per-agent opt-out:
    # `session_store: {enabled: false}` in agent.yaml.
    _ss_raw = {
        **(defaults.get("session_store") or {}),
        **(agent_cfg.get("session_store") or {}),
    }
    if _ss_raw.get("enabled") and not _opts_extra.get("enable_file_checkpointing"):
        _opts_extra["session_store"] = _get_session_store(_ss_raw)
        if _ss_raw.get("flush") in ("batched", "eager"):
            _opts_extra["session_store_flush"] = _ss_raw["flush"]

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
        strict_mcp_config=strict_mcp,
        thinking=thinking_cfg,
        agents=subagents,
        hooks={
            "PreToolUse": [
                # Order matters. Loop guard first (cheapest, kills phantom /
                # runaway loops before anything awaits): matcher=None so it
                # inspects EVERY tool call, not just Bash. Then crontab guard
                # (structural rule), approval gate (pattern-based, may await a
                # Discord reaction), then checkpoint pre-write (snapshot state
                # before mutation). Any single deny blocks the call.
                HookMatcher(hooks=[loop_guard_hook]),
                HookMatcher(matcher="Bash", hooks=[_block_raw_crontab]),
                HookMatcher(matcher="Bash", hooks=[approval_hook]),
                HookMatcher(matcher="Write", hooks=[on_facts_hygiene]),
                HookMatcher(matcher="Edit", hooks=[on_facts_hygiene]),
                # A2: protected-path gate AFTER the cheap local checks (it
                # may await a Discord reaction), BEFORE the checkpoint.
                HookMatcher(matcher="Write", hooks=[write_gate_hook]),
                HookMatcher(matcher="Edit", hooks=[write_gate_hook]),
                HookMatcher(hooks=[on_pre_write_checkpoint]),
            ],
            "UserPromptSubmit": [HookMatcher(hooks=[budget_hook])],
            "Stop": [HookMatcher(hooks=[on_stop])],
            "PreCompact": [HookMatcher(hooks=[on_precompact])],
            "SubagentStart": [HookMatcher(hooks=[on_subagent_start])],
            "PostToolUse": [HookMatcher(hooks=[on_post_write_lint])],
        },
        include_partial_messages=True,  # enable token-level streaming
        **_opts_extra,
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
