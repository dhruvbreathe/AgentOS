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
from collections import defaultdict
from pathlib import Path

import discord
from dotenv import load_dotenv

from agent_loader import AgentConfig, load_all_agents, load_global
from agent_tools import parse_routing_header
from relay import DiscordMessageSink, run_agent
from transcribe import is_audio, transcribe

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("agentos")

ROOT = Path(__file__).parent
SESSIONS_FILE = ROOT / "logs" / "sessions.json"


def _load_sessions() -> dict[str, str]:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_sessions(data: dict[str, str]) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(data, indent=2))


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
        # check "did the user say anything?" still see intent.
        if not body.strip() and transcripts:
            body = transcripts[0][1]
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
        )

        async with self._channel_lock(channel_id):
            resume = self.sessions.get(channel_id)
            try:
                # `async with channel.typing():` keeps Discord's "typing..."
                # indicator alive for the whole turn. The single-shot
                # channel.typing() call it replaces expires after ~10s,
                # which made long tool chains feel stuck even though work
                # was happening.
                async with message.channel.typing():
                    final_text, session_id = await run_agent(
                        agent,
                        prompt,
                        sink,
                        resume_session_id=resume,
                        current_hop=current_hop,
                        max_hops=max_hops,
                    )
                if session_id:
                    self.sessions[channel_id] = session_id
                    _save_sessions(self.sessions)

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
