"""Cron entry point. Runs a task prompt through an agent and posts the
result to the agent's webhook.

Usage:
    python cron_trigger.py <agent_name> <task_name>

`<task_name>` refers to agents/<agent_name>/tasks/<task_name>.md — the file's
contents are used as the prompt.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from agent_loader import load_agent
from relay import CollectingSink, run_agent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("cron-trigger")

CHUNK_LIMIT = 1900  # Discord message cap is 2000; leave room.


async def _post_webhook(webhook_url: str, content: str, username: str) -> None:
    # Split into chunks to respect Discord's 2000-char limit.
    chunks = [content[i : i + CHUNK_LIMIT] for i in range(0, len(content), CHUNK_LIMIT)] or [content]
    async with aiohttp.ClientSession() as session:
        for chunk in chunks:
            payload = {"content": chunk, "username": username}
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status >= 300:
                    body = await resp.text()
                    log.error("webhook post failed %s: %s", resp.status, body)


async def _run(agent_name: str, task_name: str) -> int:
    agent = load_agent(agent_name)

    task_file = (agent.tasks_dir or Path()) / f"{task_name}.md"
    if not task_file.exists():
        log.error("No task file at %s", task_file)
        return 2

    prompt = task_file.read_text()
    prompt = (
        f"[Scheduled task `{task_name}` triggered at "
        f"{datetime.now().isoformat(timespec='seconds')}]\n\n{prompt}"
    )

    sink = CollectingSink()
    text, _session = await run_agent(agent, prompt, sink)

    if not agent.webhook_url:
        log.warning(
            "Agent %s has no webhook_url; printing to stdout instead", agent.name
        )
        print(text)
        return 0

    header = f"**[{task_name}]**\n"
    await _post_webhook(
        agent.webhook_url, header + text, username=f"{agent.name} (scheduled)"
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("agent", help="Agent name (folder under agents/)")
    p.add_argument("task", help="Task name (file under agents/<agent>/tasks/<task>.md)")
    args = p.parse_args()
    sys.exit(asyncio.run(_run(args.agent, args.task)))


if __name__ == "__main__":
    main()
