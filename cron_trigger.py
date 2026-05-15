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
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import yaml
from dotenv import load_dotenv

from agent_loader import load_agent
from relay import CollectingSink, run_agent

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.agentos"

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
    # Peek at the task file before loading the agent — `bootstrap: lite`
    # in the frontmatter changes how we load the system prompt.
    tasks_dir = Path(__file__).resolve().parent / "agents" / agent_name / "tasks"
    task_file = tasks_dir / f"{task_name}.md"
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
    # One-shot deferred runs created via scripts/defer.py — after this run
    # we delete the plist + task file so the agent doesn't re-fire.
    oneshot = bool(fm.get("oneshot"))
    # Lite bootstrap — strip SOUL/USER/HUMANIZER/etc. for single-shot
    # housekeeping crons that don't need the full personality. ~70% token cut.
    bootstrap = str(fm.get("bootstrap", "")).strip().lower()
    lite = bootstrap in ("lite", "minimal", "light")

    agent = load_agent(agent_name, lite=lite)
    if lite:
        log.info("Loaded %s in lite-bootstrap mode (task=%s)", agent_name, task_name)

    prompt = (
        f"[Scheduled task `{task_name}` triggered at "
        f"{datetime.now().isoformat(timespec='seconds')}]\n\n{body}"
    )

    sink = CollectingSink()
    try:
        text, _session = await run_agent(agent, prompt, sink)

        if silent:
            log.info(
                "systemEvent task %s/%s completed (kind=%s) — output in trajectory, no webhook post",
                agent.name, task_name, kind or "silent",
            )
            print(text)
        elif not agent.webhook_url:
            log.warning(
                "Agent %s has no webhook_url; printing to stdout instead", agent.name
            )
            print(text)
        else:
            header = f"**[{task_name}]**\n"
            await _post_webhook(
                agent.webhook_url, header + text, username=f"{agent.name} (scheduled)"
            )
        return 0
    finally:
        if oneshot:
            _cleanup_oneshot(agent_name, task_name, task_file)


def _cleanup_oneshot(agent_name: str, task_name: str, task_file: Path) -> None:
    """Bootout + delete the launchd plist + task file for a one-shot deferred run.

    Order matters: unlink the files BEFORE issuing `launchctl bootout`. We
    are running INSIDE the launchd-managed process; bootout sends SIGTERM
    to the calling process before returning, so any work after the bootout
    call is unreliable. Files-first guarantees the on-disk state is clean
    even if our process gets killed.

    Best-effort: failures are logged but never raised — a stuck plist is
    inconvenient but not data-corrupting, and we don't want cleanup errors
    to mask the actual run result.
    """
    label = f"{LABEL_PREFIX}.{agent_name}-{task_name}"
    plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    for p in (plist_path, task_file):
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            log.warning("oneshot unlink failed for %s: %s", p, e)
    log.info("oneshot files removed: %s", label)
    # Bootout last. launchd will SIGTERM us once it processes this; we may
    # never return from this subprocess.run call. That's fine — the on-disk
    # cleanup above is what matters.
    try:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{label}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        log.warning("oneshot bootout failed for %s: %s", label, e)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("agent", help="Agent name (folder under agents/)")
    p.add_argument("task", help="Task name (file under agents/<agent>/tasks/<task>.md)")
    args = p.parse_args()
    sys.exit(asyncio.run(_run(args.agent, args.task)))


if __name__ == "__main__":
    main()
