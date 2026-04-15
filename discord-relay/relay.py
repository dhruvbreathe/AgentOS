"""Core relay: runs a prompt through the Claude Agent SDK for a given
AgentConfig and streams the response into a caller-provided sink.

Two sinks are provided:
- DiscordMessageSink: live-edits a discord.Message as tokens arrive.
- CollectingSink: accumulates text; used by cron for webhook posting.

Trajectory logs are written to logs/trajectories/<agent>/<session_id>.jsonl —
one JSON record per event (prompt, assistant text, tool_use, tool_result).
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent_loader import AgentConfig

ROOT = Path(__file__).parent
TRAJECTORY_ROOT = ROOT / "logs" / "trajectories"


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

    def result(self, meta: dict) -> None:
        self._write({"role": "system", "type": "result", **meta})

    def close(self) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None


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

    traj = TrajectoryLogger(agent.name, resume_session_id)
    traj.prompt(prompt)

    buffer = ""
    session_id: str | None = None

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    if msg.session_id:
                        session_id = msg.session_id
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            traj.text(block.text)
                        elif isinstance(block, ThinkingBlock):
                            traj.thinking(block.thinking)
                        elif isinstance(block, ToolUseBlock):
                            traj.tool_use(block.name, block.input)
                        chunk = _block_text(block)
                        if chunk:
                            buffer += chunk
                            await sink.update(buffer.strip())
                elif isinstance(msg, UserMessage):
                    # Tool results arrive as UserMessage with ToolResultBlock
                    # content. We log them to the trajectory but don't stream
                    # them to Discord.
                    if isinstance(msg.content, list):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                traj.tool_result(block.content, block.is_error)
                elif isinstance(msg, ResultMessage):
                    if getattr(msg, "session_id", None):
                        session_id = msg.session_id
                    traj.result(
                        {
                            "session_id": session_id,
                            "stop_reason": getattr(msg, "stop_reason", None),
                            "usage": getattr(msg, "usage", None),
                        }
                    )
    finally:
        traj.close()

    final = buffer.strip() or "*(agent returned no text)*"
    await sink.finalize(final)
    return final, session_id
