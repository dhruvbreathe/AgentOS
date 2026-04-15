"""Discord bot that routes channel messages to per-channel Claude agents.

Run: python bot.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        super().__init__(intents=intents)

        self.agents: dict[str, AgentConfig] = {}
        self.global_cfg = load_global()
        self.streaming_cfg = self.global_cfg.get("streaming", {}) or {}
        self.sessions = _load_sessions()
        self._locks: dict[str, asyncio.Lock] = {}

    async def on_ready(self) -> None:
        self.agents = load_all_agents()
        log.info(
            "Logged in as %s — %d agents loaded: %s",
            self.user,
            len(self.agents),
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
            return
        if not self._should_respond(message, agent):
            return

        author = f"{message.author.display_name} ({message.author.id})"
        prompt = (
            f"[Discord #{message.channel.name} — from {author}]\n\n{message.content}"
        )

        # Placeholder message we'll stream into
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
                log.exception("agent %s failed", agent.name)
                await sink.finalize(f"⚠️ `{agent.name}` error: {e}")


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN not set in .env")
    RelayBot().run(token)


if __name__ == "__main__":
    main()
