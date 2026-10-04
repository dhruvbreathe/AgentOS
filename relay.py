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
import secrets
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    MirrorErrorMessage,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

import quota_state
import secret_guard
from agent_loader import AgentConfig
from agent_tools import build_comms_server
from text_lint import sanitize as _strip_emdash

ROOT = Path(__file__).parent
TRAJECTORY_ROOT = ROOT / "logs" / "trajectories"
CONTEXT_USAGE_LOG = ROOT / "logs" / "context-usage.jsonl"
TURN_METRICS_LOG = ROOT / "logs" / "turn-metrics.jsonl"

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

    # After this many seconds of turn duration, finalize() sends a fresh
    # bottom message in addition to the in-place edit. Edits never fire a
    # new-message event, so a long turn otherwise completes with zero
    # notification and zero unread badge. To the operator it reads as the
    # agent going silent even though the full reply is sitting there.
    FINALIZE_PING_AFTER_S = 90.0

    def __init__(
        self,
        message,
        edit_interval: float = 1.2,
        max_length: int = 1900,
        continuation_marker: str = "…",
        agent_name: str | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._created = time.monotonic()
        # `messages` grows as the reply overflows past `max_length`. The
        # first element is the placeholder we were handed; subsequent ones
        # are sent via channel.send as continuations land.
        self.messages = [message]
        # Overflow messages deleted at finalize. Still count as "ours" for
        # the buried-reply check: channel.last_message_id isn't updated on
        # delete, so a retired tail would otherwise trigger a false pointer.
        self._retired_ids: set = set()
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
        # The final reply is usually much shorter than the streamed trace,
        # which may have spilled into overflow messages. Retire those so
        # stale trace chunks don't sit under the answer.
        if finalizing and len(self.messages) > len(chunks):
            surplus = self.messages[len(chunks):]
            self.messages = self.messages[:len(chunks)]
            for msg in surplus:
                self._retired_ids.add(msg.id)
                try:
                    await msg.delete()
                except Exception as e:
                    log.warning("relay finalize: couldn't delete overflow message: %s", e)
                    try:
                        await msg.edit(content="-# (progress trace collapsed)")
                    except Exception:
                        pass
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
        # final reply arrives as a silent EDIT above them: no notification,
        # no bump. The operator sees gates, then apparent silence. Drop a
        # pointer at the bottom so the reply is discoverable. Fail-open: any
        # error here must never eat the (already delivered) reply.
        try:
            channel = self.messages[0].channel
            last_id = getattr(channel, "last_message_id", None)
            our_ids = {m.id for m in self.messages} | self._retired_ids
            buried = last_id is not None and last_id not in our_ids
            long_turn = (
                time.monotonic() - self._created > self.FINALIZE_PING_AFTER_S
            )
            if buried:
                await channel.send(
                    "-# ⬆️ reply finished above (streamed into the earlier "
                    "message, before the messages below)"
                )
            elif long_turn:
                # Edits fire no notification. After a long turn, emit a real
                # message event so the channel goes unread and pings land.
                await channel.send("-# ✅ done, reply above ⬆️")
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
        obj = secret_guard.redact(obj)  # T-cacd32: no .env values on disk
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


def _write_turn_metrics(line: dict) -> None:
    """One line per turn: where the time went and how much was cached.
    connect_s = CLI spawn + init; ttft_s = query() to first stream event.
    (The CLI's duration_api_ms isn't usable for an overhead split: it came
    out larger than duration_ms on every bench turn.)"""
    try:
        if (
            TURN_METRICS_LOG.exists()
            and TURN_METRICS_LOG.stat().st_size > _CONTEXT_LOG_MAX_BYTES
        ):
            TURN_METRICS_LOG.replace(TURN_METRICS_LOG.with_suffix(".jsonl.1"))
        with TURN_METRICS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as e:
        log.debug("turn-metrics write skipped: %s", e)


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


class _ReplyTracker:
    """Separates the deliverable reply from the progress trace.

    The streamed buffer interleaves narration, 🤔 thinking snippets and 🔧
    tool lines. Watching that live is useful; delivering it as the answer is
    not. On 2026-09-30 a cron posted a wall of tool calls ending in a literal
    `[SILENT]` (the silence check only looked at the start of the buffer),
    and a task-board result reached its requester as the first 1500 chars of
    the worker's trace, cut off before the actual answer.

    The reply is the text the agent wrote after its last tool call. If that
    is empty (turn ended on a tool call), fall back to all text blocks with
    the trace lines removed. `complete` says which one you got: callers that
    deliver without a human watching (cron, task board) must not pass the
    fallback off as a deliverable. On 2026-10-01 a cron that hit max_turns
    mid chart render posted 1.4K chars of "Checking X now" notes as its
    report; see incomplete_notice().
    """

    def __init__(self) -> None:
        self.tool_calls = 0
        self._all: list[str] = []
        self._tail: list[str] = []

    def text(self, t: str | None) -> None:
        t = (t or "").strip()
        if t:
            self._all.append(t)
            self._tail.append(t)

    def tool(self) -> None:
        self.tool_calls += 1
        self._tail = []

    @property
    def complete(self) -> bool:
        """True when the turn ended on text, i.e. a final message exists."""
        return bool(self._tail)

    @property
    def last_note(self) -> str:
        """The most recent text block: where an unfinished turn got to."""
        return self._all[-1] if self._all else ""

    @property
    def reply(self) -> str:
        return "\n\n".join(self._tail or self._all)


def stopped_early(outcome: dict) -> bool:
    """The turn was cut off (max_turns, abort, budget) rather than ending on
    its own. A turn only stops on a tool_use response when something outside
    the model ends the loop."""
    return outcome.get("stop_reason") == "tool_use" or outcome.get(
        "terminal_reason"
    ) not in (None, "completed")


def incomplete_notice(outcome: dict) -> str:
    """One line for a run that never wrote its final message, built from the
    run_agent outcome dict. Callers post it INSTEAD of the narration
    fallback, e.g. "hit max_turns (41/40) before its final message. Trace:
    `logs/trajectories/...jsonl`"."""
    terminal = outcome.get("terminal_reason")
    if terminal == "max_turns":
        why = (f"hit max_turns ({outcome.get('num_turns') or '?'}/"
               f"{outcome.get('max_turns') or '?'}) before its final message")
    elif stopped_early(outcome):
        why = f"stopped before its final message ({terminal or outcome.get('stop_reason')})"
    else:
        why = "ended without a final message"
    trace = outcome.get("trajectory")
    return f"{why}." + (f" Trace: `{trace}`" if trace else "")


async def run_agent(
    agent: AgentConfig,
    prompt: str,
    sink: Sink,
    resume_session_id: str | None = None,
    current_hop: int = 0,
    max_hops: int = 8,
    chain: str = "",
    model_override: str | None = None,
    effort_override: str | None = None,
    max_turns_override: int | None = None,
    origin: str = "turn",
    outcome: dict | None = None,
    task_ctx: dict | None = None,
    warm_pool=None,
    warm_key: str | None = None,
) -> tuple[str, str | None]:
    """Run `prompt` through the agent and stream into `sink`.

    `current_hop` is the hop value of the incoming message (0 for human input,
    1+ for agent-to-agent). `max_hops` is a generous depth ceiling; the real
    loop guard is `chain` (the `>`-joined path of agents that already routed
    this message), which lets `send_to_agent` refuse cycles at any depth. The
    agent_comms MCP server is mounted fresh each turn with these values
    closure-captured, so the tool enforces both guards automatically.

    The `*_override` params support utility runs (OpenClaw utilityModel
    semantics, Wave 3 P0): heartbeats and pre-rotation memory flushes run
    the SAME agent identity on a cheaper model / lower effort / tighter
    turn cap. They apply to this turn's options copy only — the cached
    AgentConfig is never mutated, so the next operator turn is back on the
    agent's real model.

    `origin` tags the turn in logs/turn-metrics.jsonl (operator / routed /
    heartbeat / flush / cron). Pass a dict as `outcome` to receive
    {"error": <AssistantMessageError or None>, "is_error": bool,
    "api_error_status": int | None} for classifying the run.

    Returns (final_text, session_id). final_text is the reply only (see
    _ReplyTracker): the sink streams the full trace while the turn runs,
    then finalize() replaces it with the reply. The trace stays in the
    trajectory log. outcome also gets tool_calls, reply_complete (False =
    no text after the last tool call, so final_text is the narration
    fallback), last_note, stop_reason, terminal_reason, num_turns,
    max_turns and trajectory (path relative to the repo root).
    session_id can be persisted by the caller to resume a conversation in
    the same Discord thread next time.
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
    # Utility-run overrides (heartbeat / rotation flush). Scalar reassignment
    # on the per-turn copy — see the shallow-copy contract above.
    if model_override:
        options.model = model_override
    if effort_override:
        options.effort = effort_override
    if max_turns_override:
        options.max_turns = max_turns_override
    # Quota breaker: weekly Opus window exhausted -> run on the fallback.
    _swapped = quota_state.model_for(options.model)
    if _swapped != options.model:
        log.warning("[%s] opus window exhausted: %s -> %s",
                    agent.name, options.model, _swapped)
        options.model = _swapped
    # The CLI refuses to start when --fallback-model equals --model; a model
    # override (e.g. rotation flush on Sonnet) can collide with the default
    # fallback. Same guard as cron_trigger's lite downshift.
    if options.fallback_model and options.fallback_model == options.model:
        options.fallback_model = None

    # Mount the agent-comms MCP server with this turn's hop context.
    # With a warm session the server built on its FIRST turn stays mounted,
    # so per-turn values go through a shared holder (session_pool.ctx_for).
    comms_ctx = warm_pool.ctx_for(warm_key) if (warm_pool is not None and warm_key) else {}
    comms_ctx["batch"] = f"{agent.name}:{secrets.token_hex(4)}"
    comms_server = build_comms_server(
        sender_name=agent.name,
        current_hop=current_hop,
        max_hops=max_hops,
        chain=chain,
        task_ctx=task_ctx,
        ctx=comms_ctx,
    )
    mcp_servers = dict(options.mcp_servers) if isinstance(options.mcp_servers, dict) else {}
    mcp_servers["agent_comms"] = comms_server
    options.mcp_servers = mcp_servers

    # Pre-approve the comms tools so Claude doesn't hit a permission prompt
    # (send_to_agent + the task-board tools: delegate/status/complete).
    new_tools = [
        f"mcp__agent_comms__{t}"
        for t in ("send_to_agent", "delegate_task", "task_status", "complete_task")
    ]
    options.allowed_tools = [
        *options.allowed_tools, *(t for t in new_tools if t not in options.allowed_tools)
    ]

    traj = TrajectoryLogger(agent.name, resume_session_id)
    traj.prompt(prompt)

    buffer = ""  # live trace: text + 🤔 + 🔧 lines, streamed to the sink
    reply = _ReplyTracker()  # the deliverable, returned + finalized
    session_id: str | None = None
    stop_reason: str | None = None
    # Track whether the last assistant message produced any text. When
    # stop_reason is tool_use but the agent already said something final in
    # prose, there's nothing to continue — silent-drop the warning.
    had_final_text = False

    MAX_CONTINUES = 3

    t0 = time.monotonic()
    timing: dict[str, float | None] = {"connect_s": None, "ttft_s": None}
    t_query: float | None = None
    result_meta: dict = {}
    run_outcome = outcome if outcome is not None else {}
    run_outcome.update({"error": None, "is_error": False, "api_error_status": None})

    async def _drain(client) -> None:
        nonlocal buffer, session_id, stop_reason, had_final_text
        this_round_had_text = False
        async for msg in client.receive_response():
            if (
                timing["ttft_s"] is None
                and t_query is not None
                and isinstance(msg, (StreamEvent, AssistantMessage))
            ):
                timing["ttft_s"] = round(time.monotonic() - t_query, 2)
            if isinstance(msg, RateLimitEvent):
                quota_state.record_event(msg.rate_limit_info)
                continue
            if isinstance(msg, AssistantMessage):
                if getattr(msg, "error", None):
                    run_outcome["error"] = msg.error
                    quota_state.record_error(
                        msg.error,
                        " ".join(b.text for b in msg.content if isinstance(b, TextBlock)),
                    )
                if msg.session_id:
                    session_id = msg.session_id
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        traj.text(block.text)
                        reply.text(block.text)
                        if (block.text or "").strip():
                            this_round_had_text = True
                    elif isinstance(block, ThinkingBlock):
                        traj.thinking(block.thinking)
                    elif isinstance(block, ToolUseBlock):
                        traj.tool_use(block.name, block.input)
                        reply.tool()
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
                if msg.is_error:
                    run_outcome["is_error"] = True
                    run_outcome["api_error_status"] = msg.api_error_status
                    if msg.api_error_status == 429:
                        quota_state.record_error(
                            "rate_limit", "; ".join(msg.errors or []) or "HTTP 429"
                        )
                model_usage = {
                    m: (u if isinstance(u, dict) else getattr(u, "__dict__", str(u)))
                    for m, u in (msg.model_usage or {}).items()
                }
                # Auto-continue rounds each emit a result: sum the counters.
                for k in ("duration_ms", "duration_api_ms", "num_turns"):
                    result_meta[k] = result_meta.get(k, 0) + (getattr(msg, k) or 0)
                result_meta["terminal_reason"] = getattr(msg, "terminal_reason", None)
                result_meta["usage"] = msg.usage
                result_meta["model_usage"] = model_usage
                traj.result(
                    {
                        "session_id": session_id,
                        "stop_reason": stop_reason,
                        "usage": getattr(msg, "usage", None),
                        "model_usage": model_usage or None,
                        "duration_ms": msg.duration_ms,
                        "duration_api_ms": msg.duration_api_ms,
                        "num_turns": msg.num_turns,
                        "terminal_reason": msg.terminal_reason,
                        "is_error": msg.is_error,
                        "api_error_status": msg.api_error_status,
                        "timing": dict(timing),
                    }
                )
        if this_round_had_text:
            had_final_text = True

    final = ""
    started = False  # any reply received: a failed warm turn can't be retried

    async def _turn(client) -> tuple[str, str | None]:
        """The turn body. `client` is a fresh ClaudeSDKClient or a warm
        session's adapter (same query/receive_response/get_context_usage)."""
        nonlocal t_query, final, started
        timing["connect_s"] = round(time.monotonic() - t0, 2)
        t_query = time.monotonic()
        await client.query(prompt)
        started = True
        await _drain(client)

        # Auto-continue if the turn ended on tool_use WITHOUT a final text
        # reply. The Claude Agent SDK splits work into rounds capped by
        # max_turns; a routing-heavy turn can burn rounds on tool calls
        # and end before wrapping up. Nudge the model to finish.
        # NOTE: had_final_text is per drain round ("any text this round"),
        # so a narrating agent never gets the nudge. Kept as is on purpose
        # (2026-10-01): gating on reply.complete instead would let every
        # max_turns death run up to MAX_CONTINUES more rounds of max_turns
        # each. Needs a cost cap before it changes.
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

        # Only surface the warning if the turn still has no final message
        # (text after its last tool call) after auto-continuing. Before
        # 2026-10-01 this keyed on had_final_text, so a turn with any
        # narration at all hit max_turns with no footer.
        # A tool_use stop means something outside the model ended the loop
        # (max_turns, abort), so even trailing text in that last message
        # (text after a tool block) is narration, not a final message.
        ended_on_text = reply.complete and stop_reason != "tool_use"
        footer = ""
        if (
            stop_reason
            and stop_reason not in ("end_turn", "stop_sequence", None)
            and not ended_on_text
        ):
            why = (
                f"hit max_turns ({result_meta.get('num_turns') or '?'}/"
                f"{options.max_turns or '?'})"
                if result_meta.get("terminal_reason") == "max_turns"
                else f"stop_reason: `{stop_reason}`"
            )
            footer = (
                f"\n\n-# ⚠️ {why}: turn ended before a final text reply. "
                f"Ask me to continue and I'll pick up from here."
            )
        # Deliver the reply, not the trace (see _ReplyTracker). The live
        # sink already showed the trace while the turn ran.
        final = (reply.reply or "*(agent returned no text)*") + footer
        # What callers need to tell "delivered" from "ran out" (cron_trigger,
        # the task board). See incomplete_notice().
        try:
            _trace = str(traj.path.relative_to(ROOT))
        except ValueError:
            _trace = str(traj.path)
        run_outcome.update({
            "tool_calls": reply.tool_calls,
            "reply_complete": ended_on_text,
            "last_note": reply.last_note,
            "stop_reason": stop_reason,
            "terminal_reason": result_meta.get("terminal_reason"),
            "num_turns": result_meta.get("num_turns"),
            "max_turns": options.max_turns,
            "trajectory": _trace,
        })
        # Deliver BEFORE post-turn telemetry: get_context_usage can take
        # up to 10s and the operator shouldn't wait on it.
        await sink.finalize(final)
        try:
            _emit_turn_metrics(
                agent.name, session_id, origin, options, timing, result_meta,
                total_s=time.monotonic() - t0, outcome=run_outcome,
            )
        except Exception as e:  # noqa: BLE001 — telemetry never breaks a turn
            log.debug("turn metrics skipped: %s", e)

        # Post-turn context telemetry — needs the live CLI, after all drains
        # so it reflects the whole turn.
        await _capture_context_usage(client, agent.name, session_id)
        return final, session_id

    try:
        done = False
        if warm_pool is not None and warm_key:
            try:
                await warm_pool.run(warm_key, options, _turn, timing)
                done = True
            except Exception as e:
                await warm_pool.evict(warm_key)
                if started:
                    raise  # the reply already began: retrying would repeat it
                log.warning("[%s] warm session unavailable (%s); per-turn client",
                            agent.name, e)
        if not done:
            async with ClaudeSDKClient(options=options) as client:
                await _turn(client)
    finally:
        traj.close()

    return final, session_id


def _emit_turn_metrics(
    agent_name: str,
    session_id: str | None,
    origin: str,
    options,
    timing: dict,
    result_meta: dict,
    total_s: float,
    outcome: dict,
) -> None:
    usage = result_meta.get("usage") or {}
    _write_turn_metrics({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent": agent_name,
        "session_id": session_id,
        "origin": origin,
        "model": options.model,
        "effort": options.effort,
        "warm": timing.get("warm"),
        "connect_s": timing.get("connect_s"),
        "ttft_s": timing.get("ttft_s"),
        "total_s": round(total_s, 2),
        "num_turns": result_meta.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "cache_read": usage.get("cache_read_input_tokens"),
        "cache_create": usage.get("cache_creation_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "models_used": sorted((result_meta.get("model_usage") or {}).keys()),
        "error": outcome.get("error") or (
            f"http_{outcome['api_error_status']}" if outcome.get("api_error_status") else None
        ),
    })
