"""Multi-client Discord relay. Agents can share a bot token (one Discord
identity for multiple channels) or each have their own (one Discord
identity per agent). We group agents by bot_token and spawn one
discord.Client per group in the same asyncio event loop.

Run: python bot.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

import discord
from dotenv import load_dotenv

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
        self._locks: dict[str, asyncio.Lock] = {}
        # channel_id → unix-time after which we'll try typing again. When
        # Discord returns 40062 ("service resource is being rate limited")
        # we mark the channel for 30 min and skip typing entirely. Without
        # this cache, discord.py's 5-retry-on-429 loop keeps re-arming the
        # cooldown on every turn and the indicator never returns.
        self._typing_cooldown_until: dict[str, float] = {}

    async def on_ready(self) -> None:
        log.info(
            "[%s] logged in as %s — agents: %s on %d channel(s)",
            self.label,
            self.user,
            sorted({a.name for a in self.agents.values()}),
            len(self.agents),
        )

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

    def _channel_lock(self, channel_id: str) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
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
        session_id = self.sessions.get(channel_id)
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
            (self.global_cfg.get("defaults", {}) or {}).get("max_hops", 3)
        )
        current_hop = 0
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

        author = (
            f"@{sender} (agent, hop {current_hop}/{max_hops})"
            if sender
            else f"{message.author.display_name} ({message.author.id})"
        )

        # Attachments — download and inject paths into the prompt so the
        # agent can Read them (PDFs, images, text, etc.). Discord delivers
        # these as .attachments, not in .content, so they're invisible
        # unless we surface them explicitly.
        attach_paths = await self._download_attachments(message)

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

        async with self._channel_lock(channel_id):
            resume = self.sessions.get(channel_id)
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
                try:
                    final_text, session_id = await run_agent(
                        agent,
                        prompt,
                        sink,
                        resume_session_id=resume,
                        current_hop=current_hop,
                        max_hops=max_hops,
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


async def _run_all(default_token: str | None) -> None:
    groups = _group_agents_by_token(default_token)
    if not groups:
        raise SystemExit("No agents with bot tokens to run.")

    clients: list[tuple[RelayBot, str]] = []
    for token, agents in groups.items():
        label = ",".join(sorted({a.name for a in agents.values()}))
        clients.append((RelayBot(label=label, agents=agents), token))

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
