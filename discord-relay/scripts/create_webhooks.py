"""Create a Discord webhook in each agent's channel using that agent's
bot token (requires MANAGE_WEBHOOKS permission on the channel).

Writes the resulting URLs back into .env under the env var name declared
in each agent's agent.yaml (webhook_url_env). Existing lines are updated
in place; missing ones are appended.

Usage:
    python scripts/create_webhooks.py            # all agents missing a webhook
    python scripts/create_webhooks.py <name>...  # just these
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
sys.path.insert(0, str(ROOT))
from agent_loader import load_all_agents  # noqa: E402


async def _create_webhook(
    session: aiohttp.ClientSession, token: str, channel_id: str, name: str
) -> tuple[str | None, str]:
    # If the bot already owns a webhook named `name`, reuse it rather than making duplicates.
    async with session.get(
        f"https://discord.com/api/v10/channels/{channel_id}/webhooks",
        headers={"Authorization": f"Bot {token}"},
    ) as r:
        if r.status == 200:
            existing = await r.json()
            for w in existing:
                if w.get("name") == name:
                    return (
                        f"https://discord.com/api/webhooks/{w['id']}/{w['token']}",
                        "reused existing",
                    )
        elif r.status in (401, 403):
            body = await r.text()
            return None, f"list-hooks {r.status}: {body[:200]}"
    async with session.post(
        f"https://discord.com/api/v10/channels/{channel_id}/webhooks",
        headers={"Authorization": f"Bot {token}"},
        json={"name": name},
    ) as r:
        body = await r.json()
        if r.status >= 300:
            return None, f"create {r.status}: {body}"
        return (
            f"https://discord.com/api/webhooks/{body['id']}/{body['token']}",
            "created",
        )


def _update_env(env_var: str, value: str) -> None:
    """Set or add `env_var=value` in .env, preserving the rest."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"{env_var}={value}\n")
        return
    text = ENV_PATH.read_text()
    pattern = re.compile(rf"^{re.escape(env_var)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(f"{env_var}={value}", text)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += f"{env_var}={value}\n"
    ENV_PATH.write_text(text)


async def main() -> int:
    targets = sys.argv[1:]
    agents = load_all_agents()
    default_token = os.environ.get("DISCORD_BOT_TOKEN") or ""

    async with aiohttp.ClientSession() as session:
        for channel_id, cfg in agents.items():
            if targets and cfg.name not in targets:
                continue
            env_var = cfg.raw.get("webhook_url_env")
            if not env_var:
                print(f"[skip] {cfg.name} — no webhook_url_env in agent.yaml")
                continue
            if os.environ.get(env_var):
                print(f"[skip] {cfg.name} — {env_var} already set")
                continue
            token = cfg.bot_token or default_token
            if not token:
                print(f"[skip] {cfg.name} — no bot_token available")
                continue
            url, note = await _create_webhook(
                session, token, cfg.channel_id, f"{cfg.name}-inbound"
            )
            if not url:
                print(f"[fail] {cfg.name}: {note}")
                continue
            _update_env(env_var, url)
            print(f"[ok] {cfg.name} ({note}) -> {env_var} written to .env")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
