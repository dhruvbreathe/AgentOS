"""Core relay: runs a prompt through the Claude Agent SDK for a given
AgentConfig and streams the response into a caller-provided sink.

Two sinks are provided:
- DiscordMessageSink: live-edits a discord.Message as tokens arrive.
- CollectingSink: accumulates text; used by cron for webhook posting.

Trajectory logs are written to logs/trajectories/<agent>/<session_id>.jsonl —
one JSON record per event (prompt, assistant text, tool_use, tool_result).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    MirrorErrorMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent_loader import AgentConfig
from agent_tools import build_comms_server
from text_lint import sanitize as _strip_emdash

ROOT = Path(__file__).parent
TRAJECTORY_ROOT = ROOT / "logs" / "trajectories"
CONTEXT_USAGE_LOG = ROOT / "logs" / "context-usage.jsonl"

# relay.py used `log` in two exception paths without ever defining it — a
# latent NameError that only fired when Discord message allocation failed.
log = logging.getLogger("relay")


# ---- Sinks ------------------------------------------------------------

class Sink(ABC):
    @abstractmethod
    async def update(self, text: str) -> None: ...

    @abstractmethod
    async def finalize(self, text: str) -> None: ...


class CollectingSink(Sink):
    def __init__(self) -> None:
        self.text = ""

    async def update(self, text: str) -> None:
        self.text = text

    async def finalize(self, text: str) -> None:
        self.text = text


class DiscordMessageSink(Sink):
    """Edits a discord.Message at a throttled interval so token streaming
    doesn't trip Discord's edit rate limit (~5/5s per channel)."""

    def __init__(
        self,
        message,
        edit_interval: float = 1.2,
        max_length: int = 1900,
        continuation_marker: str = "…",
        agent_name: str | None = None,
    ) -> None:
        self._agent_name = agent_name
        # `messages` grows as the reply overflows past `max_length`. The
        # first element is the placeholder we were handed; subsequent ones
        # are sent via channel.send as continuations land.
        self.messages = [message]
        self.edit_interval = edit_interval
        self.max_length = max_length
        self.continuation = continuation_marker
        self._last_edit = 0.0
        self._last_text = ""

    def _chunk(self, text: str) -> list[str]:
        """Split text into <=max_length chunks, preferring natural boundaries
        (paragraph > newline > sentence > word > hard cut)."""
        if not text:
            return [""]
        chunks: list[str] = []
        remaining = text
        min_break = self.max_length // 3  # avoid tiny first chunks
        while len(remaining) > self.max_length:
            window = remaining[: self.max_length]
            candidates = [
                window.rfind("\n\n"),
                window.rfind("\n"),
                (window.rfind(". ") + 2) if window.rfind(". ") >= 0 else -1,
                (window.rfind("? ") + 2) if window.rfind("? ") >= 0 else -1,
                window.rfind(" "),
            ]
            split_at = max(
                (c for c in candidates if c > min_break),
                default=self.max_length,
            )
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    async def _flush(self, text: str, finalizing: bool) -> None:
        # Strip em-dashes / en-dashes before any chunking. Operator directive
        # 2026-05-19: zero tolerance on `—` and `–` in any output.
        text = _strip_emdash(text, agent=getattr(self, "_agent_name", None), surface="discord_sink")
        chunks = self._chunk(text)
        channel = self.messages[0].channel
        # Add placeholders for new overflow chunks.
        while len(self.messages) < len(chunks):
            try:
                new_msg = await channel.send(self.continuation)
                self.messages.append(new_msg)
            except Exception as e:
                if finalizing:
                    # On the final flush, dropping silently means the tail of
                    # the reply is lost forever. Log it and render what we can
                    # into the messages we DO have.
                    log.warning(
                        "relay finalize: couldn't allocate overflow message "
                        "(%s) — rendering %d/%d chunks", e, len(self.messages), len(chunks)
                    )
                    break
                return  # streaming tick — skip this flush, try again next tick
        # Edit each message to its chunk content.
        empty_placeholder = "*(no output)*" if finalizing else self.continuation
        for msg, chunk in zip(self.messages, chunks):
            content = chunk[: self.max_length] if chunk else empty_placeholder
            try:
                await msg.edit(content=content)
            except Exception as e:
                # One failed edit must not eat the rest of the reply.
                log.warning("relay _flush: edit failed on a chunk: %s", e)
                continue

    async def update(self, text: str) -> None:
        now = time.monotonic()
        if now - self._last_edit < self.edit_interval:
            return
        if text == self._last_text:
            return
        await self._flush(text, finalizing=False)
        self._last_edit = now
        self._last_text = text

    async def finalize(self, text: str) -> None:
        await self._flush(text, finalizing=True)
        # Visibility guard (2026-07-06): if other messages landed below our
        # placeholder while the turn ran (approval gates, agent posts), the
        # final reply arrives as a silent EDIT above them — no notification,
        # no bump. The operator sees gates, then apparent silence. Drop a
        # pointer at the bottom so the reply is discoverable. Fail-open: any
        # error here must never eat the (already delivered) reply.
        try:
            channel = self.messages[0].channel
            last_id = getattr(channel, "last_message_id", None)
            our_ids = {m.id for m in self.messages}
            if last_id is not None and last_id not in our_ids:
                await channel.send(
                    "-# ⬆️ reply finished above (streamed into the earlier "
                    "message, before the messages below)"
                )
        except Exception as e:
            log.warning("relay finalize: bottom-pointer failed: %s", e)


# ---- Trajectory logger ------------------------------------------------


class TrajectoryLogger:
    """Append-only JSONL log of everything that happened on a single agent
    run — prompt, thinking, text, tool calls, tool results, result metadata.

    One file per session. Sessions with a resume_id reuse the same file so
    a Discord thread accumulates its whole history in one place."""

    def __init__(self, agent_name: str, session_hint: str | None) -> None:
        self.agent = agent_name
        # `session_hint` is either a prior Claude session_id (reused across
        # turns) or None (first turn — we synthesise a timestamp-based id).
        self.session_id = session_hint or datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        self.path = TRAJECTORY_ROOT / agent_name / f"{self.session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = None
        # Per-turn tool stats — count + success/failure per tool name. Written
        # at result() so post-hoc analysis can rank which tools actually help
        # this agent. Pattern borrowed from Hermes' ShareGPT export.
        self._tool_stats: dict[str, dict[str, int]] = {}
        # Track the last tool_use name so tool_result (which arrives as a
        # separate message) can attribute success/failure to the right tool.
        self._last_tool_name: str | None = None

    def _write(self, obj: dict) -> None:
        obj = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **obj}
        if self._fp is None:
            self._fp = self.path.open("a", encoding="utf-8")
        self._fp.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._fp.flush()

    def prompt(self, text: str) -> None:
        self._write({"role": "user", "type": "prompt", "content": text})

    def text(self, text: str) -> None:
        if text:
            self._write({"role": "assistant", "type": "text", "content": text})

    def thinking(self, text: str) -> None:
        if text:
            self._write({"role": "assistant", "type": "thinking", "content": text})

    def tool_use(self, name: str, inp: dict) -> None:
        self._write(
            {"role": "assistant", "type": "tool_use", "name": name, "input": inp}
        )
        stats = self._tool_stats.setdefault(name, {"count": 0, "success": 0, "failure": 0})
        stats["count"] += 1
        self._last_tool_name = name

    def tool_result(self, content, is_error: bool | None) -> None:
        if not isinstance(content, str):
            try:
                content = json.dumps(content)
            except Exception:
                content = str(content)
        self._write(
            {
                "role": "tool",
                "type": "tool_result",
                "content": content,
                "is_error": bool(is_error),
            }
        )
        if self._last_tool_name:
            stats = self._tool_stats.setdefault(
                self._last_tool_name, {"count": 0, "success": 0, "failure": 0}
            )
            if is_error:
                stats["failure"] += 1
            else:
                stats["success"] += 1

    def result(self, meta: dict) -> None:
        # Include accumulated per-tool stats so post-hoc analysis can see
        # which tools this agent actually used + success rate.
        self._write({
            "role": "system",
            "type": "result",
            "tool_stats": dict(self._tool_stats),
            **meta,
        })

    def close(self) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None


# ---- Context-usage telemetry -------------------------------------------

_CONTEXT_LOG_MAX_BYTES = 5 * 1024 * 1024  # rotate to .1 past this


def _write_context_line(line: dict) -> None:
    """Append one telemetry line, rotating the log once past the size cap."""
    try:
        if (
            CONTEXT_USAGE_LOG.exists()
            and CONTEXT_USAGE_LOG.stat().st_size > _CONTEXT_LOG_MAX_BYTES
        ):
            CONTEXT_USAGE_LOG.replace(CONTEXT_USAGE_LOG.with_suffix(".jsonl.1"))
    except OSError:
        pass
    CONTEXT_USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CONTEXT_USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


async def _capture_context_usage(
    client: ClaudeSDKClient, agent_name: str, session_id: str | None
) -> None:
    """Post-turn context telemetry (SDK 0.2.110 `get_context_usage`).

    Writes one JSONL line per turn to logs/context-usage.jsonl; doctor.py
    reads the tail and flags agents drifting toward their autocompact
    threshold. Fail-open: telemetry must never break a turn.
    """
    try:
        usage = await asyncio.wait_for(client.get_context_usage(), timeout=10)
        cats = [
            c
            for c in (usage.get("categories") or [])
            if (c.get("name") or "").lower() != "free space"
        ]
        cats.sort(key=lambda c: -(c.get("tokens") or 0))
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "agent": agent_name,
            "session_id": session_id,
            "total_tokens": usage.get("totalTokens"),
            "max_tokens": usage.get("maxTokens"),
            "percentage": round(float(usage.get("percentage") or 0.0), 1),
            "model": usage.get("model"),
            "autocompact_enabled": usage.get("isAutoCompactEnabled"),
            "autocompact_threshold": usage.get("autoCompactThreshold"),
            "top_categories": [
                {"name": c.get("name"), "tokens": c.get("tokens")}
                for c in cats[:5]
            ],
        }
        await asyncio.to_thread(_write_context_line, line)
    except Exception as e:
        log.debug("context-usage capture skipped: %s", e)


# ---- Runner -----------------------------------------------------------

def _block_text(block) -> str | None:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ThinkingBlock):
        # Surface a condensed thinking summary in Discord (small text)
        snippet = (block.thinking or "")[:200].replace("\n", " ").strip()
        if snippet:
            return f"\n-# 🤔 {snippet}{'...' if len(block.thinking or '') > 200 else ''}\n"
        return None
    if isinstance(block, ToolUseBlock):
        # Show tool name + key args for visibility
        args_preview = ""
        if isinstance(block.input, dict):
            for k in ("command", "file_path", "pattern", "query", "agent", "message"):
                if k in block.input:
                    val = str(block.input[k])[:80]
                    args_preview = f" → `{val}`"
                    break
        return f"\n🔧 `{block.name}`{args_preview}"
    return None


async def run_agent(
    agent: AgentConfig,
    prompt: str,
    sink: Sink,
    resume_session_id: str | None = None,
    current_hop: int = 0,
    max_hops: int = 8,
    chain: str = "",
) -> tuple[str, str | None]:
    """Run `prompt` through the agent and stream into `sink`.

    `current_hop` is the hop value of the incoming message (0 for human input,
    1+ for agent-to-agent). `max_hops` is a generous depth ceiling; the real
    loop guard is `chain` (the `>`-joined path of agents that already routed
    this message), which lets `send_to_agent` refuse cycles at any depth. The
    agent_comms MCP server is mounted fresh each turn with these values
    closure-captured, so the tool enforces both guards automatically.

    Returns (final_text, session_id). session_id can be persisted by the
    caller to resume a conversation in the same Discord thread next time.
    """
    # Work on a per-turn shallow copy, NEVER the cached `agent.options`.
    # `agent_loader.load_all_agents` stores one AgentConfig per channel, so the
    # primary channel and any extra_channel_ids of the same agent share this
    # object. `bot.py` serializes per-channel (not per-agent), so two channels
    # of the same agent can run turns concurrently. Mutating the shared options
    # in place leaks one turn's resume id / mcp set / allowed_tools into the
    # other. A shallow copy is sufficient because every field we touch below is
    # reassigned to a fresh object (scalar resume, fresh dict, fresh list) —
    # we never mutate a nested structure in place.
    options = copy.copy(agent.options)
    # Always assign (never conditionally): a stale resume id from a previous
    # turn would leak into a fresh conversation if we only set it when truthy.
    options.resume = resume_session_id or None

    # Mount the agent-comms MCP server with this turn's hop context.
    comms_server = build_comms_server(
        sender_name=agent.name,
        current_hop=current_hop,
        max_hops=max_hops,
        chain=chain,
    )
    mcp_servers = dict(options.mcp_servers) if isinstance(options.mcp_servers, dict) else {}
    mcp_servers["agent_comms"] = comms_server
    options.mcp_servers = mcp_servers

    # Pre-approve the tool so Claude doesn't hit a permission prompt.
    tool_id = "mcp__agent_comms__send_to_agent"
    if tool_id not in options.allowed_tools:
        options.allowed_tools = [*options.allowed_tools, tool_id]

    traj = TrajectoryLogger(agent.name, resume_session_id)
    traj.prompt(prompt)

    buffer = ""
    session_id: str | None = None
    stop_reason: str | None = None
    # Track whether the last assistant message produced any text. When
    # stop_reason is tool_use but the agent already said something final in
    # prose, there's nothing to continue — silent-drop the warning.
    had_final_text = False

    MAX_CONTINUES = 3

    async def _drain(client) -> None:
        nonlocal buffer, session_id, stop_reason, had_final_text
        this_round_had_text = False
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.session_id:
                    session_id = msg.session_id
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        traj.text(block.text)
                        if (block.text or "").strip():
                            this_round_had_text = True
                    elif isinstance(block, ThinkingBlock):
                        traj.thinking(block.thinking)
                    elif isinstance(block, ToolUseBlock):
                        traj.tool_use(block.name, block.input)
                    chunk = _block_text(block)
                    if chunk:
                        buffer += chunk
                        await sink.update(buffer.strip())
            elif isinstance(msg, UserMessage):
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock):
                            traj.tool_result(block.content, block.is_error)
            elif isinstance(msg, MirrorErrorMessage):
                # SessionStore mirror failure (after SDK retries). Local disk
                # still has the transcript; log loudly, never render to chat.
                log.warning(
                    "[%s] session-store mirror error: %s",
                    agent.name, getattr(msg, "error", msg),
                )
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "session_id", None):
                    session_id = msg.session_id
                stop_reason = getattr(msg, "stop_reason", None)
                traj.result(
                    {
                        "session_id": session_id,
                        "stop_reason": stop_reason,
                        "usage": getattr(msg, "usage", None),
                    }
                )
        if this_round_had_text:
            had_final_text = True

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            await _drain(client)

            # Auto-continue if the turn ended on tool_use WITHOUT a final text
            # reply. The Claude Agent SDK splits work into rounds capped by
            # max_turns; a routing-heavy turn can burn rounds on tool calls
            # and end before wrapping up. Nudge the model to finish.
            continues = 0
            while (
                stop_reason == "tool_use"
                and not had_final_text
                and continues < MAX_CONTINUES
            ):
                continues += 1
                await client.query(
                    "Continue — wrap up the task with a short final reply "
                    "summarising what you did and any next step. If there's "
                    "genuinely nothing more to say, reply with a single line."
                )
                await _drain(client)

            # Post-turn context telemetry — inside the async with (needs the
            # live CLI), after all drains so it reflects the whole turn.
            await _capture_context_usage(client, agent.name, session_id)
    finally:
        traj.close()

    # Only surface the warning if we actually have no final text after
    # auto-continuing. Otherwise the agent wrapped up cleanly and the footer
    # is noise.
    footer = ""
    if (
        stop_reason
        and stop_reason not in ("end_turn", "stop_sequence", None)
        and not had_final_text
    ):
        footer = (
            f"\n\n-# ⚠️ stop_reason: `{stop_reason}` — turn ended before a "
            f"final text reply. Ask me to continue and I'll pick up from here."
        )

    final = (buffer.strip() or "*(agent returned no text)*") + footer
    await sink.finalize(final)
    return final, session_id
