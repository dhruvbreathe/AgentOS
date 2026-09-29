"""Multi-client Discord relay. Agents can share a bot token (one Discord
identity for multiple channels) or each have their own (one Discord
identity per agent). We group agents by bot_token and spawn one
discord.Client per group in the same asyncio event loop.

Run: python bot.py
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

import discord
from dotenv import load_dotenv

import memory_recall
import quota_state
import session_pool
import session_rotation
import session_store
from agent_loader import AgentConfig, load_all_agents, load_global
from agent_tools import parse_routing_header
from relay import DiscordMessageSink, run_agent
from transcribe import is_audio, transcribe

load_dotenv()

# --- Concurrent-safe rotating logging ------------------------------------
# bot.py OWNS logs/bot.log. Under scripts/autorestart.sh the old and new
# bot.py can briefly overlap during a restart, and logs/bot.log also grows
# unbounded with no rotation. We attach a ROTATING file handler to the ROOT
# logger (so discord.py's `discord.*` loggers and our child loggers —
# agent-comms, agent-loader, transcribe, approval-gate, save-marker — all
# propagate into the same file) plus a console StreamHandler so the terminal
# still shows logs.
#
# IMPORTANT: scripts/autorestart.sh must NOT redirect shell stdout/stderr to
# logs/bot.log anymore (it now uses logs/bot.console.log). Two writers on one
# file fight when rotation renames it out from under the shell's fd.
#
# restart.sh parses logs/bot.log for `starting [0-9]+ Discord` and
# `logged in as`; both come from the agentos logger below, so they still land
# in bot.log unchanged.
try:
    # Multi-process safe: takes a cross-process file lock around rotation so
    # the brief old+new bot.py overlap during autorestart can't corrupt the
    # file. Preferred — see requirements.txt (concurrent-log-handler).
    from concurrent_log_handler import ConcurrentRotatingFileHandler as _RotatingHandler
    _CONCURRENT_LOGGING = True
except ImportError:
    # Fallback: stdlib RotatingFileHandler is NOT multi-process safe. If two
    # bot.py processes overlap during a restart, a rotation by one can drop
    # or interleave the other's lines. Install concurrent-log-handler to
    # fully close the double-writer hazard.
    from logging.handlers import RotatingFileHandler as _RotatingHandler
    _CONCURRENT_LOGGING = False

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "bot.log"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_formatter = logging.Formatter(_LOG_FORMAT)

_file_handler = _RotatingHandler(
    str(_LOG_FILE),
    maxBytes=20 * 1024 * 1024,  # 20 MB per file
    backupCount=5,              # bot.log + bot.log.1 .. bot.log.5 (~120 MB cap)
    encoding="utf-8",
)
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

_root = logging.getLogger()
_root.setLevel(logging.INFO)
# Replace any handlers a stray basicConfig/import may have attached so we own
# logs/bot.log exclusively (and don't double-log to console).
for _h in list(_root.handlers):
    _root.removeHandler(_h)
_root.addHandler(_file_handler)
_root.addHandler(_console_handler)

log = logging.getLogger("agentos")
log.info(
    "logging initialized: %s (rotating 20MB x5, concurrent=%s)",
    _LOG_FILE,
    _CONCURRENT_LOGGING,
)
# -------------------------------------------------------------------------

ROOT = Path(__file__).parent
SESSIONS_FILE = ROOT / "logs" / "sessions.json"


def _load_sessions() -> dict[str, str]:
    return session_store.load()


def _save_session(key: str, session_id: str) -> None:
    # Merge-only write via session_store: ~15 RelayBot clients plus the
    # dashboard all share logs/sessions.json. Whole-file rewrites from a
    # stale snapshot were clobbering other channels' session ids.
    session_store.set_session(key, session_id)


class LaneLock:
    """Per-channel turn lock with priority lanes (2026-09-29, OpenClaw
    command-queue). Still one turn at a time per channel, but when several
    wait, operator turns go before routed agent-to-agent turns, which go
    before background work (post-reply rotation). Never preempts a running
    turn; background work is made cancellable by its owner instead."""

    FOREGROUND, NORMAL, BACKGROUND = 0, 1, 2

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._held = False
        self._waiters: list[tuple[int, int, asyncio.Future]] = []
        self._seq = 0

    def locked(self) -> bool:
        return self._held

    def depth(self) -> int:
        return len(self._waiters)

    async def acquire(self, priority: int) -> None:
        if not self._held and not self._waiters:
            self._held = True
            return
        fut = asyncio.get_running_loop().create_future()
        self._seq += 1
        heapq.heappush(self._waiters, (priority, self._seq, fut))
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release()  # granted just as we were cancelled: pass it on
            else:
                self._waiters = [w for w in self._waiters if w[2] is not fut]
                heapq.heapify(self._waiters)
            raise

    def release(self) -> None:
        while self._waiters:
            _, _, fut = heapq.heappop(self._waiters)
            if not fut.done():
                fut.set_result(True)  # ownership transfers; _held stays True
                return
        self._held = False

    @contextlib.asynccontextmanager
    async def lane(self, priority: int):
        t0 = time.monotonic()
        await self.acquire(priority)
        waited = time.monotonic() - t0
        if waited > 2:
            log.info("lane %s: waited %.1fs (priority %d, %d still queued)",
                     self.name, waited, priority, len(self._waiters))
        try:
            yield
        finally:
            self.release()


_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")


def _interactive_effort(agent: AgentConfig, cap: str | None) -> str | None:
    """Effort override for an operator Discord turn, or None to keep the
    agent's own. The cap only ever lowers effort (defaults.interactive_effort)."""
    current = getattr(agent.options, "effort", None)
    if cap not in _EFFORT_ORDER or current not in _EFFORT_ORDER:
        return None
    if _EFFORT_ORDER.index(current) <= _EFFORT_ORDER.index(cap):
        return None
    return cap


# Heuristic for the Hermes-style auto-save 💾 reaction. A reply counts as
# "substantive" if it is long enough OR has the kind of structure that
# signals a real decision/handoff rather than a quick ack.
_SUBSTANTIVE_MARKERS = (
    "**", "##", "\n- ", "\n* ", "\n1. ",
    "Decision", "decision", "→", "✅", "⚠️", "🎯", "📋",
)


def _is_substantive_reply(text: str, min_chars: int) -> bool:
    if not text:
        return False
    # Tool-only output ("🔧 Edit → ...") shouldn't be auto-marked.
    if text.lstrip().startswith("🔧"):
        return False
    if len(text) >= min_chars:
        return True
    return any(m in text for m in _SUBSTANTIVE_MARKERS)


class RelayBot(discord.Client):
    """One discord.Client, scoped to the set of agents that share its
    bot token. Messages in channels not bound to this client are ignored."""

    def __init__(self, label: str, agents: dict[str, AgentConfig]) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        # reactions intent is required for on_raw_reaction_add → save-marker.
        # Webhook-poll path (approval_gate) doesn't use it.
        intents.reactions = True
        super().__init__(intents=intents)

        self.label = label
        self.agents: dict[str, AgentConfig] = agents  # channel_id → cfg
        self.global_cfg = load_global()
        self.streaming_cfg = self.global_cfg.get("streaming", {}) or {}
        self.save_cfg = self.global_cfg.get("save", {}) or {}
        self.sessions = _load_sessions()
        self._locks: dict[str, LaneLock] = {}
        # Post-reply background rotation (OpenClaw compaction: flush after
        # delivery, cancelled by new input) and its handoff notes.
        self._bg_rotation: dict[str, asyncio.Task] = {}
        self._rotation_notes: dict[str, str] = {}
        # Burst debounce: (channel, author) -> messages awaiting one turn.
        self._debounce: dict[tuple[str, int], list[discord.Message]] = {}
        # channel_id → unix-time after which we'll try typing again. When
        # Discord returns 40062 ("service resource is being rate limited")
        # we mark the channel for 30 min and skip typing entirely. Without
        # this cache, discord.py's 5-retry-on-429 loop keeps re-arming the
        # cooldown on every turn and the indicator never returns.
        self._typing_cooldown_until: dict[str, float] = {}
        # A5 (2026-07-01): message_id → session_id for every message a turn
        # posted, so a 💾 on an OLDER message saves THAT turn, not whatever
        # session the channel currently points at. In-memory, bounded.
        self._msg_sessions: dict[int, str] = {}
        # Phase-1 heartbeats (2026-07-05): scheduled self-checks, silent
        # unless action taken. Since 2026-09-29 they run in their own fresh
        # session; a beat that ACTS leaves a note here for the channel's next
        # operator turn instead of writing into the operator session.
        from heartbeat import HeartbeatScheduler
        self._heartbeat = HeartbeatScheduler(self)
        self._pending_notes: dict[str, list[str]] = {}
        # Quota breaker (2026-09-29): messages that arrived while the
        # subscription was out, replayed once it resets. In-memory: a restart
        # while blocked drops the queue (the operator was told it's paused).
        self._quota_queue: list[discord.Message] = []
        self._quota_noticed: dict[str, float] = {}
        self._quota_task: asyncio.Task | None = None

    def _remember_msg_sessions(self, sink, session_id: str) -> None:
        """Map every message this turn's sink posted to its session id.
        Bounded FIFO (~1000 entries) so long uptimes don't grow unbounded."""
        try:
            for m in getattr(sink, "messages", None) or []:
                mid = getattr(m, "id", None)
                if mid is not None:
                    self._msg_sessions[int(mid)] = session_id
            while len(self._msg_sessions) > 1000:
                self._msg_sessions.pop(next(iter(self._msg_sessions)))
        except Exception:
            pass  # best-effort — save falls back to channel session

    async def on_ready(self) -> None:
        log.info(
            "[%s] logged in as %s — agents: %s on %d channel(s)",
            self.label,
            self.user,
            sorted({a.name for a in self.agents.values()}),
            len(self.agents),
        )
        # Idempotent — on_ready re-fires on reconnect; start() guards.
        self._heartbeat.start()
        if self._quota_task is None or self._quota_task.done():
            self._quota_task = asyncio.create_task(self._quota_replay_loop())

    async def _quota_replay_loop(self) -> None:
        """Replay messages queued while the subscription was out of quota."""
        while True:
            await asyncio.sleep(60)
            if not self._quota_queue or quota_state.blocked()[0]:
                continue
            queued, self._quota_queue = self._quota_queue, []
            self._quota_noticed.clear()
            log.info("[%s] quota reset: replaying %d queued message(s)",
                     self.label, len(queued))
            for m in queued:
                asyncio.create_task(self.on_message(m))

    async def _debounce_burst(
        self, message: discord.Message, channel_id: str
    ) -> list[discord.Message] | None:
        """Collect same-author messages arriving within `debounce_ms` of each
        other (max 3s total). Returns the burst for the first message's
        handler; None for later messages, which it absorbs."""
        window = float(
            (self.global_cfg.get("defaults", {}) or {}).get("debounce_ms", 500)
        ) / 1000
        if window <= 0:
            return [message]
        key = (channel_id, message.author.id)
        if key in self._debounce:
            self._debounce[key].append(message)
            return None
        buf = self._debounce[key] = [message]
        deadline = time.monotonic() + 3.0
        seen = 0
        while len(buf) != seen and time.monotonic() < deadline:
            seen = len(buf)
            await asyncio.sleep(window)
        return self._debounce.pop(key)

    def _maybe_schedule_rotation(self, channel_id: str, agent, session_id: str) -> None:
        """Past the soft threshold (80% of the ceiling), flush + rotate in the
        background after the reply is delivered, instead of making the next
        operator turn pay for the flush (OpenClaw compaction)."""
        try:
            rot_cfg = (self.global_cfg.get("defaults", {}) or {}).get(
                "session_rotation") or {}
            if not rot_cfg.get("enabled", True):
                return
            tokens = session_rotation._last_context_tokens(session_id)
            if tokens is None or tokens < session_rotation.soft_threshold(agent.name, rot_cfg):
                return
            prev = self._bg_rotation.get(channel_id)
            if prev is not None and not prev.done():
                return
            self._bg_rotation[channel_id] = asyncio.create_task(
                self._bg_rotate(channel_id, agent, session_id, tokens, rot_cfg)
            )
        except Exception as e:  # noqa: BLE001 — scheduling must never break a turn
            log.warning("[%s] rotation scheduling skipped: %s", agent.name, e)

    async def _bg_rotate(self, channel_id: str, agent, session_id: str,
                         tokens: int, rot_cfg: dict) -> None:
        try:
            await asyncio.sleep(5)  # give a quick follow-up the chance to land
            async with self._channel_lock(channel_id).lane(LaneLock.BACKGROUND):
                if self.sessions.get(channel_id) != session_id:
                    return  # the conversation moved on
                t0 = time.monotonic()
                await session_rotation.flush(agent, session_id, rot_cfg)
                # Past the last await: nothing below can be cancelled halfway.
                self._rotation_notes[channel_id] = session_rotation.handoff_note(
                    agent.name, tokens)
                self.sessions.pop(channel_id, None)
                session_store.update({channel_id: None})
                log.info("[%s] background rotation done in %.0fs (session %s at %sk)",
                         agent.name, time.monotonic() - t0, session_id, tokens // 1000)
            await session_pool.POOL.evict(channel_id)  # its process holds the old session
        except asyncio.CancelledError:
            log.info("[%s] background rotation cancelled by new input", agent.name)
        except Exception:
            log.exception("[%s] background rotation failed", agent.name)

    async def system_turn(self, channel_id: str, prompt: str, origin: str) -> None:
        """A turn with no Discord message behind it (task-board wake-up):
        runs in the channel session, streams into the channel, normal-lane."""
        agent = self.agents.get(channel_id)
        if agent is None:
            return
        try:
            channel = self.get_channel(int(channel_id)) or await self.fetch_channel(int(channel_id))
            placeholder = await channel.send(self.streaming_cfg.get("thinking_indicator", "…"))
        except Exception as e:
            log.warning("[%s] system turn: cannot post to %s: %s", self.label, channel_id, e)
            return
        sink = DiscordMessageSink(
            placeholder,
            edit_interval=float(self.streaming_cfg.get("edit_interval_seconds", 1.2)),
            max_length=int(self.streaming_cfg.get("max_message_length", 1900)),
            agent_name=agent.name,
        )
        _bg = self._bg_rotation.get(channel_id)
        if _bg is not None and not _bg.done():
            _bg.cancel()
        async with self._channel_lock(channel_id).lane(LaneLock.NORMAL):
            resume = self.sessions.get(channel_id)
            _bg_note = self._rotation_notes.pop(channel_id, None)
            if _bg_note and resume is None:
                prompt = f"{_bg_note}\n\n{prompt}"
            try:
                # Wake-ups are conversational (they land in the operator's
                # channel), so they get the same effort cap as operator turns.
                _, session_id = await run_agent(
                    agent, prompt, sink, resume_session_id=resume, origin=origin,
                    effort_override=_interactive_effort(
                        agent,
                        (self.global_cfg.get("defaults", {}) or {}).get("interactive_effort"),
                    ),
                )
            except Exception as e:
                log.exception("[%s] system turn failed", agent.name)
                await sink.finalize(f"⚠️ `{agent.name}` error: {e}")
                return
            if session_id:
                self.sessions[channel_id] = session_id
                _save_session(channel_id, session_id)
                self._remember_msg_sessions(sink, session_id)
                self._maybe_schedule_rotation(channel_id, agent, session_id)

    async def _quota_hold(self, message: discord.Message, channel_id: str) -> bool:
        """True if the message was queued because every model is out."""
        is_blocked, window, until = quota_state.blocked()
        if not is_blocked:
            return False
        self._quota_queue.append(message)
        if self._quota_noticed.get(channel_id) != until:
            self._quota_noticed[channel_id] = until
            when = time.strftime("%a %H:%M", time.localtime(until)) if until else "soon"
            try:
                await message.channel.send(
                    f"-# ⏸️ Claude subscription out of quota ({window} window), "
                    f"resets {when}. Queued; I'll pick this up then."
                )
            except Exception as e:
                log.warning("[%s] quota notice failed: %s", self.label, e)
        log.warning("[%s] quota blocked (%s): queued message in %s",
                    self.label, window, channel_id)
        return True

    def _should_respond(self, message: discord.Message, agent: AgentConfig) -> bool:
        if message.author == self.user:
            return False
        if message.author.bot and not agent.allow_bots:
            return False
        # Webhook-posted messages without a routing header are self-echoes
        # (e.g. an agent curl-posting an attachment back into its own
        # channel) or outside webhooks we don't own. Real agent-to-agent
        # traffic always has the `📡 @target (via @sender, hop N/M)` header.
        if message.webhook_id is not None:
            if not parse_routing_header(message.content or ""):
                return False
        # Empty text is fine as long as there's at least an attachment —
        # dropping a file with no caption should still trigger a response.
        if not message.content.strip() and not message.attachments:
            return False
        return True

    def _channel_lock(self, channel_id: str) -> LaneLock:
        if channel_id not in self._locks:
            self._locks[channel_id] = LaneLock(channel_id)
        return self._locks[channel_id]

    async def _download_attachments(
        self, message: discord.Message
    ) -> list[Path]:
        """Save any Discord attachments to local disk so the agent can Read
        them. One dir per channel, timestamped names keep history + avoid
        collisions. Returns local paths, or [] on no attachments / failure."""
        if not message.attachments:
            return []
        from datetime import datetime
        attach_dir = ROOT / "logs" / "attachments" / str(message.channel.id)
        attach_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for att in message.attachments:
            ts = datetime.now().strftime("%Y%m%dT%H%M%S")
            safe_name = "".join(
                c for c in att.filename if c.isalnum() or c in "._-"
            ) or "attachment"
            local_path = attach_dir / f"{ts}-{safe_name}"
            try:
                await att.save(local_path)
                paths.append(local_path)
                log.info(
                    "[%s] saved attachment %s (%d bytes) -> %s",
                    self.label, att.filename, att.size, local_path
                )
            except Exception as e:
                log.warning("[%s] failed to save attachment %s: %s",
                            self.label, att.filename, e)
        return paths

    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Save-to-vault gate. Operator reacts with the configured save
        emoji on any agent message → write the most recent turn to
        $VAULT_PATH/Sessions/ and react ack on the message. Mirrors the
        Hermes 'save icon' UX."""
        if not self.save_cfg.get("enabled", True):
            return
        save_emoji = self.save_cfg.get("emoji", "💾")
        ack_emoji = self.save_cfg.get("ack_emoji", "✅")
        error_emoji = self.save_cfg.get("error_emoji", "⚠️")

        if str(payload.emoji) != save_emoji:
            return
        if self.user is not None and payload.user_id == self.user.id:
            return  # ignore my own ack reactions

        channel_id = str(payload.channel_id)
        agent = self.agents.get(channel_id)
        if not agent:
            return  # not a channel I own

        vault_env = self.global_cfg.get("vault_path_env", "VAULT_PATH")
        vault_path = os.environ.get(vault_env)
        if not vault_path:
            log.warning("[%s] save: %s not set, skipping", self.label, vault_env)
            return

        channel = self.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(payload.channel_id)
            except Exception as e:
                log.warning("[%s] save: cannot fetch channel: %s", self.label, e)
                return
        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception as e:
            log.warning("[%s] save: cannot fetch message: %s", self.label, e)
            return

        from save_marker import save_turn
        # A5: prefer the session the reacted MESSAGE belongs to; fall back
        # to the channel's current session (pre-fix behaviour) for messages
        # posted before this mapping existed or after a restart.
        session_id = (
            self._msg_sessions.get(payload.message_id)
            or self.sessions.get(channel_id)
        )
        try:
            out_path = save_turn(
                agent_name=agent.name,
                channel_name=getattr(channel, "name", channel_id),
                session_id=session_id,
                vault_path=Path(vault_path),
            )
        except Exception:
            log.exception("[%s] save: save_turn raised", self.label)
            try:
                await message.add_reaction(error_emoji)
            except Exception:
                pass
            return

        ack = ack_emoji if out_path else error_emoji
        try:
            await message.add_reaction(ack)
        except Exception as e:
            log.warning("[%s] save: cannot add reaction: %s", self.label, e)
        if out_path:
            log.info("[%s] save: %s -> %s", self.label, agent.name, out_path)

    async def on_message(self, message: discord.Message) -> None:
        channel_id = str(message.channel.id)
        agent = self.agents.get(channel_id)
        if not agent:
            return  # not my channel
        if not self._should_respond(message, agent):
            return

        # Parse agent-to-agent routing header if present.
        routing = parse_routing_header(message.content)
        max_hops = int(
            (self.global_cfg.get("defaults", {}) or {}).get("max_hops", 8)
        )
        current_hop = 0
        chain = ""
        sender = None
        body = message.content

        if routing:
            # Self-reflection guard.
            if routing["sender"] == agent.name:
                return
            # Route-target mismatch: shouldn't happen (webhooks post to target's
            # channel), but guard anyway.
            if routing["target"] != agent.name:
                return
            current_hop = routing["hop"]
            max_hops = routing["max"]
            chain = routing.get("chain", "")
            sender = routing["sender"]
            body = routing["body"].strip()
            # Hard stop: if the incoming message is already at max_hops,
            # the agent may read it but cannot route further. The MCP tool
            # enforces this too; we just log for observability.
            if current_hop >= max_hops:
                log.info(
                    "[%s] %s received at max_hops (%d/%d) — no outbound routing",
                    self.label,
                    agent.name,
                    current_hop,
                    max_hops,
                )

        if await self._quota_hold(message, channel_id):
            return

        # Burst debounce (OpenClaw 9.6): a long paste Discord splits into
        # several messages, or rapid follow-ups, become ONE operator turn.
        burst: list[discord.Message] = [message]
        if not sender:
            merged = await self._debounce_burst(message, channel_id)
            if merged is None:
                return  # folded into an earlier message's turn
            if len(merged) > 1:
                burst = merged
                message = merged[-1]
                body = "\n\n".join(m.content for m in merged if m.content.strip())
                log.info("[%s] merged %d burst messages into one turn",
                         agent.name, len(merged))

        author = (
            f"@{sender} (agent, hop {current_hop}/{max_hops})"
            if sender
            else f"{message.author.display_name} ({message.author.id})"
        )

        # Attachments — download and inject paths into the prompt so the
        # agent can Read them (PDFs, images, text, etc.). Discord delivers
        # these as .attachments, not in .content, so they're invisible
        # unless we surface them explicitly.
        attach_paths: list[Path] = []
        for m in burst:
            attach_paths += await self._download_attachments(m)

        # Transcribe any audio attachments — voice messages are just .ogg
        # files in Discord. We inject the transcript inline so the agent
        # sees spoken input as if it were typed.
        transcripts: list[tuple[Path, str]] = []
        for p in attach_paths:
            if is_audio(p):
                text = await transcribe(p)
                if text:
                    transcripts.append((p, text))
                    log.info("[%s] transcribed %s: %s",
                             self.label, p.name, text[:120])

        voice_block = ""
        if transcripts:
            parts = []
            for p, text in transcripts:
                parts.append(f"> _(voice message {p.name})_\n> {text}")
            voice_block = "\n\n**Voice transcript:**\n" + "\n\n".join(parts)

        # If the message has no text body but does have a transcript,
        # promote the transcript as the effective body so agents that
        # check "did the user say anything?" still see intent. We
        # PREFIX a voice-origin marker so the agent knows the medium —
        # otherwise the transcript looks identical to a typed message
        # and agents have been confidently telling operators "voice
        # doesn't work" when in fact the transcript reached them
        # cleanly. Be loud about it.
        if not body.strip() and transcripts:
            body = f"🎙️ _(voice message, transcribed)_\n\n{transcripts[0][1]}"
            voice_block = ""  # already in body

        # Only list non-audio attachments in the file list — audio files
        # are already represented by their transcripts.
        non_audio = [p for p in attach_paths if not is_audio(p)]
        attach_block = ""
        if non_audio:
            lines = [f"- `{p}`" for p in non_audio]
            attach_block = (
                "\n\n**Attached files** (saved locally, you can `Read` "
                "them directly):\n" + "\n".join(lines)
            )

        prompt = (
            f"[Discord #{message.channel.name} — from {author}]\n\n{body}"
            + voice_block
            + attach_block
        )

        # Mirror the inbound Discord message into the receiver's web chat
        # history so operators watching the web UI see the same traffic
        # that's hitting Discord. Best-effort — don't block the turn if it
        # fails.
        # _append_history publishes its own event to the bus, so we don't
        # need to call publish_event here — one write, one event.
        try:
            from web_chat import _append_history, DEFAULT_THREAD
            if sender:  # agent-to-agent routing
                _append_history(
                    agent.name, "routed", body,
                    meta={"from": sender, "hop": current_hop, "max": max_hops,
                          "origin": "discord"},
                    thread_id=DEFAULT_THREAD,
                )
            else:
                _append_history(
                    agent.name, "user", body,
                    meta={"from": "operator", "origin": "discord",
                          "discord_author": str(message.author)},
                    thread_id=DEFAULT_THREAD,
                )
        except Exception as e:
            log.warning("[%s] failed to mirror inbound discord → web: %s",
                        self.label, e)

        placeholder = await message.channel.send(
            self.streaming_cfg.get("thinking_indicator", "…")
        )
        sink = DiscordMessageSink(
            placeholder,
            edit_interval=float(self.streaming_cfg.get("edit_interval_seconds", 1.2)),
            max_length=int(self.streaming_cfg.get("max_message_length", 1900)),
            agent_name=agent.name,
        )

        # New input cancels a pending post-reply rotation; it retries after
        # a later turn (and the hard-ceiling inline rotation still backstops).
        _bg = self._bg_rotation.get(channel_id)
        if _bg is not None and not _bg.done():
            _bg.cancel()
        _prio = LaneLock.NORMAL if sender else LaneLock.FOREGROUND
        async with self._channel_lock(channel_id).lane(_prio):
            resume = self.sessions.get(channel_id)
            # A background rotation finished since the last turn: this turn
            # starts the fresh session, seeded with its handoff.
            _bg_note = self._rotation_notes.pop(channel_id, None)
            if _bg_note and resume is None:
                prompt = f"{_bg_note}\n\n{prompt}"
            # Phase-2 memory recall (2026-07-05): prepend top-k vault/memory
            # matches for the incoming message so the agent starts the turn
            # already holding its most relevant notes. Keys off the raw
            # message body (not routing headers). Fail-open: build() returns
            # None on any error and the prompt is untouched.
            _rec_note = memory_recall.build(
                agent.name, body,
                (self.global_cfg.get("defaults", {}) or {}).get("memory_recall"),
            )
            if _rec_note:
                prompt = f"{_rec_note}\n\n{prompt}"
            # Heartbeat actions since the last operator turn (beats run in
            # their own session, so this is how the conversation hears).
            _notes = None if sender else self._pending_notes.pop(channel_id, None)
            if _notes:
                prompt = "\n".join(_notes) + f"\n\n{prompt}"
            # Phase-0 session rotation (2026-07-05): when the session's last
            # reported context exceeds the ceiling, start FRESH seeded with a
            # memory handoff instead of resuming a bloated session. Fail-open:
            # check() returns None on any error and we resume as before.
            _rot_cfg = (self.global_cfg.get("defaults", {}) or {}).get(
                "session_rotation"
            )
            _rot_note = session_rotation.check(agent.name, resume, _rot_cfg)
            if _rot_note is not None:
                # Wave 3 P0-1 (2026-07-18): pre-rotation memory flush. Run one
                # silent distillation turn in the OLD session so working state
                # (especially in-flight background work) lands in the daily
                # memory file, then rebuild the handoff from that fresh
                # memory. Fail-open: flush() False → rotate with the original
                # note, exactly the pre-flush behavior.
                if await session_rotation.flush(agent, resume, _rot_cfg):
                    _rot_note = (
                        session_rotation.check(agent.name, resume, _rot_cfg)
                        or _rot_note
                    )
                resume = None
                prompt = f"{_rot_note}\n\n{prompt}"
            try:
                # Show Discord's "is typing..." indicator for the duration
                # of the turn. discord.py keeps the indicator alive by
                # re-triggering every 5s while the context is held — that
                # endpoint is rate-limited per channel and can return
                # error 40062 under sustained load. We cache a per-channel
                # cooldown: first 40062 → silently skip typing on this
                # channel for 30 min so the cooldown can expire on
                # Discord's side, then try again. The streaming placeholder
                # still updates with tool calls regardless, so the operator
                # never loses sight of progress.
                typing_cm = message.channel.typing()
                typing_started = False
                cooldown_until = self._typing_cooldown_until.get(channel_id, 0.0)
                if cooldown_until <= time.time():
                    try:
                        await typing_cm.__aenter__()
                        typing_started = True
                    except discord.HTTPException as e:
                        if not (e.status == 429 or getattr(e, "code", None) == 40062):
                            raise
                        cool_for_s = 30 * 60
                        self._typing_cooldown_until[channel_id] = time.time() + cool_for_s
                        log.warning(
                            "[%s] typing rate-limited (%s) on channel %s — skipping indicator for %ds",
                            self.label, getattr(e, "code", e.status), channel_id, cool_for_s,
                        )
                # Operator turns are latency-sensitive: cap effort. Routed
                # agent-to-agent turns (sender set) keep the agent's own.
                _effort = None if sender else _interactive_effort(
                    agent,
                    (self.global_cfg.get("defaults", {}) or {}).get(
                        "interactive_effort"
                    ),
                )
                if _effort:
                    log.info("[%s] interactive effort cap: %s -> %s",
                             agent.name, agent.options.effort, _effort)
                try:
                    final_text, session_id = await run_agent(
                        agent,
                        prompt,
                        sink,
                        resume_session_id=resume,
                        current_hop=current_hop,
                        max_hops=max_hops,
                        chain=chain,
                        effort_override=_effort,
                        origin="routed" if sender else "operator",
                        warm_pool=(
                            session_pool.POOL
                            if not sender and session_pool.POOL.enabled_for(agent.name)
                            else None
                        ),
                        warm_key=channel_id,
                    )
                finally:
                    if typing_started:
                        try:
                            await typing_cm.__aexit__(None, None, None)
                        except Exception:
                            pass
                if session_id:
                    self.sessions[channel_id] = session_id
                    _save_session(channel_id, session_id)
                    # A5: remember which messages belong to this session so
                    # a later 💾 on them saves the right turn.
                    self._remember_msg_sessions(sink, session_id)
                    self._maybe_schedule_rotation(channel_id, agent, session_id)

                # Mirror the agent's outbound reply into the web chat too,
                # so operators watching the web UI see the answer alongside
                # the inbound message we mirrored above.
                if final_text:
                    try:
                        from web_chat import _append_history, DEFAULT_THREAD
                        _append_history(
                            agent.name, "assistant", final_text,
                            meta={"origin": "discord",
                                  "session_id": session_id},
                            thread_id=DEFAULT_THREAD,
                        )
                    except Exception as e:
                        log.warning("[%s] failed to mirror agent reply → web: %s",
                                    self.label, e)

                # Hermes-style auto-react: bot adds 💾 to its own substantive
                # replies so the operator can ✅ to save with one tap. Guarded
                # by save.auto_react in config.yaml. The existing reaction
                # handler ignores reactions added by the bot itself
                # (payload.user_id == self.user.id check), so this won't
                # auto-trigger a save — operator confirmation is still required.
                if final_text and self.save_cfg.get("auto_react", False):
                    min_chars = int(self.save_cfg.get("auto_react_min_chars", 500))
                    if _is_substantive_reply(final_text, min_chars):
                        anchor = sink.messages[0] if sink.messages else None
                        if anchor is not None:
                            try:
                                await anchor.add_reaction(
                                    self.save_cfg.get("emoji", "💾")
                                )
                            except Exception as e:
                                log.warning("[%s] auto-react failed: %s",
                                            self.label, e)
            except Exception as e:
                log.exception("[%s] agent %s failed", self.label, agent.name)
                await sink.finalize(f"⚠️ `{agent.name}` error: {e}")

            # Between turns: check for restart signal. An agent can trigger
            # this by running `touch logs/.restart-requested` from Bash.
            # The bot exits cleanly here; scripts/autorestart.sh (if
            # running) catches the exit and brings us back up.
            restart_signal = ROOT / "logs" / ".restart-requested"
            if restart_signal.exists():
                restart_signal.unlink(missing_ok=True)
                log.info("restart signal detected — exiting cleanly for autorestart")
                import sys
                sys.exit(0)


def _group_agents_by_token(
    default_token: str | None,
) -> dict[str, dict[str, AgentConfig]]:
    """Returns { bot_token: { channel_id: AgentConfig } }. Agents without
    their own token fall back to the default token."""
    by_token: dict[str, dict[str, AgentConfig]] = defaultdict(dict)
    for channel_id, cfg in load_all_agents().items():
        token = cfg.bot_token or default_token
        if not token:
            log.warning(
                "agent %s has no bot_token and no default DISCORD_BOT_TOKEN — skipping",
                cfg.name,
            )
            continue
        by_token[token][channel_id] = cfg
    return by_token


async def _deferred_cron_loop() -> None:
    """Fleet-wide: re-run crons that stood down under quota pressure once
    background work is allowed again. One process, not one per client."""
    python = ROOT / ".venv" / "bin" / "python"
    while True:
        await asyncio.sleep(300)
        try:
            if not quota_state.background_allowed()[0]:
                continue
            for agent_name, task in quota_state.take_deferred_crons():
                log.info("replaying deferred cron %s/%s", agent_name, task)
                logf = (ROOT / "logs" / f"{agent_name}-{task}.log").open("ab")
                proc = await asyncio.create_subprocess_exec(
                    str(python), str(ROOT / "cron_trigger.py"), agent_name, task,
                    cwd=str(ROOT), stdout=logf, stderr=logf,
                )
                await proc.wait()  # one at a time: don't stampede on reset
                logf.close()
        except Exception:
            log.exception("deferred cron replay failed")


async def _kanban_loop(clients: list[RelayBot]) -> None:
    """Task-board dispatcher (kanban.py). One per machine: a non-blocking
    flock guards against a second bot process (double launchd start)."""
    import fcntl

    import kanban
    from agent_tools import _post_to_webhook
    from relay import CollectingSink

    lock_fh = open(kanban.DISPATCH_LOCK, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("kanban: another process holds the dispatcher lock; not dispatching")
        return
    lost = kanban.reclaim(all_running=True)  # a restart killed in-flight workers
    if lost:
        log.info("kanban: requeued %d card(s) lost to restart: %s", len(lost), lost)

    def _agent(name: str):
        for c in clients:
            for a in c.agents.values():
                if a.name == name:
                    return c, a
        return None, None

    async def _notice(agent, text: str) -> None:
        if agent is not None and agent.webhook_url:
            try:
                await _post_to_webhook(agent.webhook_url, text, username=f"{agent.name} (task board)")
            except Exception as e:
                log.warning("kanban notice failed: %s", e)

    def _mirror(card: dict) -> None:
        if not card.get("ledger_id"):
            return
        try:
            import task_ledger
            status = "done" if card["status"] == "done" else "blocked"
            fields = {"status": status, "result": (card.get("summary") or "")[:2000]}
            if status == "blocked":
                fields["blocked_reason"] = (card.get("summary") or card["status"])[:500]
            task_ledger.update_task(card["ledger_id"], **fields)
        except Exception as e:  # noqa: BLE001 — mirror is best-effort
            log.warning("kanban ledger update failed for %s: %s", card["id"], e)

    async def _run_card(card: dict) -> None:
        _, agent = _agent(card["to_agent"])
        if agent is None:
            kanban.complete(card["id"], "failed", f"no loaded agent @{card['to_agent']}")
            return
        await _notice(agent, f"🗂️ **{card['id']}** from @{card['from_agent']}: {card['title']}")
        outcome: dict = {}
        text = ""
        try:
            text, _ = await asyncio.wait_for(
                run_agent(
                    agent, kanban.worker_prompt(card), CollectingSink(),
                    origin="task", outcome=outcome,
                    task_ctx={"task_id": card["id"], "chain": card["chain"],
                              "depth": card["depth"]},
                ),
                timeout=kanban.CLAIM_TTL_S - 60,
            )
        except asyncio.TimeoutError:
            text = "worker timed out"
            outcome["error"] = "timeout"
        except Exception as e:
            log.exception("kanban worker %s crashed", card["id"])
            text, outcome["error"] = f"worker crashed: {e}", "crash"
        if outcome.get("error") in ("rate_limit", "billing_error"):
            # Out of quota mid-task: run it again after the reset.
            if kanban.requeue(card["id"]):
                log.warning("kanban: %s requeued (quota)", card["id"])
                return
        closed = kanban.get(card["id"])
        if closed and closed["status"] == "running":
            ok = bool(text.strip()) and not outcome.get("error")
            closed = kanban.complete(card["id"], "done" if ok else "failed",
                                     text.strip()[:3000] or "(no output)") or closed
        if closed and closed["status"] in kanban.TERMINAL:
            icon = {"done": "✅", "blocked": "⛔", "failed": "❌"}[closed["status"]]
            await _notice(agent, f"{icon} **{closed['id']}** {closed['status']}: "
                                 f"{(closed.get('summary') or '')[:1500]}")
            await asyncio.to_thread(_mirror, closed)

    running: dict[str, asyncio.Task] = {}
    while True:
        await asyncio.sleep(5)
        try:
            running = {k: t for k, t in running.items() if not t.done()}
            kanban.reclaim()
            kanban.promote()
            if not quota_state.blocked()[0]:
                for card in kanban.claim_next(busy_agents=set(running)):
                    log.info("kanban: %s -> @%s: %s", card["id"], card["to_agent"], card["title"])
                    running[card["to_agent"]] = asyncio.create_task(_run_card(card))
            for batch in kanban.take_settled_batches():
                client, req = _agent(batch["requester"])
                if client is None:
                    log.warning("kanban: requester @%s not loaded; results unread", batch["requester"])
                    continue
                log.info("kanban: waking @%s with %d result(s)", req.name, len(batch["tasks"]))
                asyncio.create_task(client.system_turn(
                    req.channel_id, kanban.wake_prompt(batch), origin="wake"))
        except Exception:
            log.exception("kanban dispatcher tick failed")


async def _run_all(default_token: str | None) -> None:
    groups = _group_agents_by_token(default_token)
    if not groups:
        raise SystemExit("No agents with bot tokens to run.")

    # Pilot hygiene (Wave 3 P0-3), fleet-wide: a configured heartbeat pilot
    # with no loaded agent anywhere is invisible forever (the marketing
    # stale-17h failure). Must run against the union of all clients; each
    # client alone only sees its own token's agents and would false-warn
    # for pilots owned by sibling clients.
    hb_cfg = (load_global().get("defaults", {}) or {}).get("heartbeat") or {}
    if hb_cfg.get("enabled"):
        fleet = {a.name for grp in groups.values() for a in grp.values()}
        for missing in sorted(set(hb_cfg.get("agents") or []) - fleet):
            log.warning(
                "[heartbeat] configured pilot '%s' has no loaded agent "
                "anywhere in the fleet (retired?): remove it from "
                "defaults.heartbeat.agents",
                missing,
            )

    asyncio.create_task(_deferred_cron_loop())

    clients: list[tuple[RelayBot, str]] = []
    for token, agents in groups.items():
        label = ",".join(sorted({a.name for a in agents.values()}))
        clients.append((RelayBot(label=label, agents=agents), token))
    asyncio.create_task(_kanban_loop([c for c, _ in clients]))

    # Stagger startup by ~150ms per client so N>~12 websocket handshakes
    # don't all race DNS resolution at once (saw a gaierror burst at 9).
    async def _delayed_start(client: RelayBot, token: str, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await client.start(token)

    log.info("starting %d Discord client(s)", len(clients))
    await asyncio.gather(
        *(_delayed_start(c, t, i * 0.15) for i, (c, t) in enumerate(clients))
    )


def main() -> None:
    default_token = os.environ.get("DISCORD_BOT_TOKEN") or None
    asyncio.run(_run_all(default_token))


if __name__ == "__main__":
    main()
