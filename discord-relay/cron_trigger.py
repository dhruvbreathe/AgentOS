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
import re
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import yaml
from dotenv import load_dotenv

from agent_loader import load_agent
from relay import CollectingSink, run_agent

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Strip YAML frontmatter from a task file. Returns (fm_dict, body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, text[m.end():]

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

    raw = task_file.read_text()
    fm, body = _parse_frontmatter(raw)

    # Task kind — "systemEvent" (or silent: true) means internal housekeeping:
    # run the agent, capture the output in the trajectory + stdout, do NOT
    # post to the Discord webhook. Use for memory maintenance, LEARNINGS
    # distillation, vault hygiene, etc. The default is "post" — output goes
    # to the agent's channel via webhook.
    kind = str(fm.get("kind", "")).strip().lower()
    silent = bool(fm.get("silent")) or kind in ("systemevent", "system_event", "internal")

    prompt = (
        f"[Scheduled task `{task_name}` triggered at "
        f"{datetime.now().isoformat(timespec='seconds')}]\n\n{body}"
    )

    sink = CollectingSink()
    text, _session = await run_agent(agent, prompt, sink)

    if silent:
        log.info(
            "systemEvent task %s/%s completed (kind=%s) — output in trajectory, no webhook post",
            agent.name, task_name, kind or "silent",
        )
        print(text)
        return 0

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
