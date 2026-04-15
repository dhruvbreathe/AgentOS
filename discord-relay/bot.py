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
from relay import DiscordMessageSink, run_agent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("discord-relay")

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
        super().__init__(intents=intents)

        self.label = label
        self.agents: dict[str, AgentConfig] = agents  # channel_id → cfg
        self.global_cfg = load_global()
        self.streaming_cfg = self.global_cfg.get("streaming", {}) or {}
        self.sessions = _load_sessions()
        self._locks: dict[str, asyncio.Lock] = {}

    async def on_ready(self) -> None:
        log.info(
            "[%s] logged in as %s — agents: %s",
            self.label,
            self.user,
            [a.name for a in self.agents.values()],
        )

    def _should_respond(self, message: discord.Message, agent: AgentConfig) -> bool:
        if message.author == self.user:
            return False
        if message.author.bot and not agent.allow_bots:
            return False
        if not message.content.strip():
            return False
        return True

    def _channel_lock(self, channel_id: str) -> asyncio.Lock:
        if channel_id not in self._locks:
            self._locks[channel_id] = asyncio.Lock()
        return self._locks[channel_id]

    async def on_message(self, message: discord.Message) -> None:
        channel_id = str(message.channel.id)
        agent = self.agents.get(channel_id)
        if not agent:
            return  # not my channel
        if not self._should_respond(message, agent):
            return

        author = f"{message.author.display_name} ({message.author.id})"
        prompt = (
            f"[Discord #{message.channel.name} — from {author}]\n\n{message.content}"
        )

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
                await message.channel.typing()
                _, session_id = await run_agent(
                    agent, prompt, sink, resume_session_id=resume
                )
                if session_id:
                    self.sessions[channel_id] = session_id
                    _save_sessions(self.sessions)
            except Exception as e:
                log.exception("[%s] agent %s failed", self.label, agent.name)
                await sink.finalize(f"⚠️ `{agent.name}` error: {e}")


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
        label = ",".join(sorted(a.name for a in agents.values()))
        clients.append((RelayBot(label=label, agents=agents), token))

    log.info("starting %d Discord client(s)", len(clients))
    await asyncio.gather(*(c.start(t) for c, t in clients))


def main() -> None:
    default_token = os.environ.get("DISCORD_BOT_TOKEN") or None
    asyncio.run(_run_all(default_token))


if __name__ == "__main__":
    main()
