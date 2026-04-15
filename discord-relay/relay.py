"""Core relay: runs a prompt through the Claude Agent SDK for a given
AgentConfig and streams the response into a caller-provided sink.

Two sinks are provided:
- DiscordMessageSink: live-edits a discord.Message as tokens arrive.
- CollectingSink: accumulates text; used by cron for webhook posting.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from agent_loader import AgentConfig


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
    ) -> None:
        self.message = message
        self.edit_interval = edit_interval
        self.max_length = max_length
        self._last_edit = 0.0
        self._last_text = ""

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_length:
            return text
        return text[: self.max_length - 20] + "\n\n…[truncated]"

    async def update(self, text: str) -> None:
        now = time.monotonic()
        if now - self._last_edit < self.edit_interval:
            return
        if text == self._last_text:
            return
        try:
            await self.message.edit(content=self._truncate(text) or "…")
            self._last_edit = now
            self._last_text = text
        except Exception:
            # Rate-limited or transient — next update will retry.
            pass

    async def finalize(self, text: str) -> None:
        try:
            await self.message.edit(content=self._truncate(text) or "*(no output)*")
        except Exception:
            pass


# ---- Runner -----------------------------------------------------------

def _block_text(block) -> str | None:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ThinkingBlock):
        return None  # don't surface thinking in Discord
    if isinstance(block, ToolUseBlock):
        return f"\n🔧 `{block.name}`"
    return None


async def run_agent(
    agent: AgentConfig,
    prompt: str,
    sink: Sink,
    resume_session_id: str | None = None,
) -> tuple[str, str | None]:
    """Run `prompt` through the agent and stream into `sink`.

    Returns (final_text, session_id). session_id can be persisted by the
    caller to resume a conversation in the same Discord thread next time.
    """
    options = agent.options
    if resume_session_id:
        options.resume = resume_session_id

    buffer = ""
    session_id: str | None = None

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                if msg.session_id:
                    session_id = msg.session_id
                for block in msg.content:
                    chunk = _block_text(block)
                    if chunk:
                        buffer += chunk
                        await sink.update(buffer.strip())
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "session_id", None):
                    session_id = msg.session_id

    final = buffer.strip() or "*(agent returned no text)*"
    await sink.finalize(final)
    return final, session_id
