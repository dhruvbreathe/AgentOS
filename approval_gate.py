"""Exec-approvals via Discord reactions.

When a PreToolUse hook flags a command as dangerous, this module posts an
approval request to the agent's channel via webhook and polls the webhook's
own message for a ✅ or ❌ reaction. Returns a decision the hook uses to
permit or deny the tool call.

Design notes:
- Pure aiohttp, no bot-client access required. The Discord webhook token
  itself can read the messages it posted (GET /webhooks/{id}/{token}/
  messages/{msg_id}), and webhook-posted messages accept reactions from
  channel users. So the hook runs self-contained without a discord.Client
  reference.
- 60s default timeout. No reaction → deny. Hard default, override per-agent.
- Approve = ✅, Deny = ❌. Any other reaction ignored.
- Reactor identity (audit A8, 2026-07-01): when `operator_id` + `bot_token`
  are provided, a qualifying reaction only counts if the operator is among
  the reactors (verified via the bot API — webhook tokens can't see WHO
  reacted, only counts). Verification errors keep polling rather than
  accepting unverified (fail-safe: deny by timeout). Without operator_id /
  bot_token we fall back to legacy any-reaction-counts behaviour.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse

import aiohttp

log = logging.getLogger("approval-gate")

APPROVE_EMOJI = "✅"
DENY_EMOJI = "❌"

_WEBHOOK_URL_RE = re.compile(
    r"https?://(?:discord\.com|discordapp\.com)/api/webhooks/"
    r"(?P<id>\d+)/(?P<token>[A-Za-z0-9_.\-]+)"
)


def _parse_webhook_url(url: str) -> tuple[str, str] | None:
    m = _WEBHOOK_URL_RE.match(url)
    if not m:
        return None
    return m.group("id"), m.group("token")


async def _operator_reacted(
    session: aiohttp.ClientSession,
    bot_token: str,
    channel_id: str,
    message_id: str,
    emoji: str,
    operator_id: str,
) -> bool | None:
    """Check via the bot API whether the operator is among the reactors for
    `emoji`. Returns True/False, or None when the check itself failed
    (caller should keep polling, not accept)."""
    url = (
        f"https://discord.com/api/v10/channels/{channel_id}"
        f"/messages/{message_id}/reactions/{urllib.parse.quote(emoji)}"
    )
    try:
        async with session.get(
            url, headers={"Authorization": f"Bot {bot_token}"}
        ) as resp:
            if resp.status >= 300:
                log.debug("reactor check %s -> %s", emoji, resp.status)
                return None
            users = await resp.json()
    except Exception as e:
        log.debug("reactor check error: %s", e)
        return None
    return any(str(u.get("id")) == str(operator_id) for u in users or [])


async def request_approval(
    webhook_url: str,
    agent_name: str,
    tool_name: str,
    command: str,
    reason: str = "",
    timeout_seconds: float = 60.0,
    poll_interval: float = 2.5,
    operator_id: str | None = None,
    bot_token: str | None = None,
) -> tuple[str, str]:
    """Post an approval request and poll for a reaction.

    Returns (decision, detail) where decision is one of:
      "approve" — operator reacted ✅
      "deny"    — operator reacted ❌
      "timeout" — no reaction within timeout_seconds
      "error"   — couldn't post or poll the webhook
    """
    parsed = _parse_webhook_url(webhook_url)
    if not parsed:
        return "error", f"invalid webhook URL: {webhook_url[:60]}"
    webhook_id, webhook_token = parsed

    # Trim command for display — keep the dangerous bit visible, don't flood.
    body = command if len(command) <= 800 else command[:797] + "..."
    content = (
        f"🔐 **Approval needed — `{agent_name}`**\n"
        f"Tool: `{tool_name}`\n"
        f"```\n{body}\n```\n"
        + (f"> {reason}\n" if reason else "")
        + f"React {APPROVE_EMOJI} to approve or {DENY_EMOJI} to deny. "
        f"_Timeout {int(timeout_seconds)}s → auto-deny. Reactions only: a "
        f"typed reply won't reach the agent until this resolves (the turn "
        f"holds the channel)._"
    )

    # UA mandatory (Cloudflare 1010 blocks default aiohttp UA); per-request
    # 30s timeout so a hung Discord call can't wedge the approval gate.
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": "DiscordBot (PranaAgentOS, 1.0)"},
    ) as session:
        # POST with wait=true so we get the full message (need its id).
        try:
            async with session.post(
                f"{webhook_url}?wait=true",
                json={"content": content, "username": f"{agent_name} (approval)"},
            ) as resp:
                if resp.status >= 300:
                    body_text = await resp.text()
                    return "error", f"webhook post {resp.status}: {body_text[:200]}"
                msg = await resp.json()
        except Exception as e:
            return "error", f"webhook post exception: {e}"

        message_id = msg.get("id")
        if not message_id:
            return "error", "webhook response missing message id"
        channel_id = msg.get("channel_id")
        # Identity verification is only possible with a bot token + the
        # channel id from the webhook response.
        verify = bool(operator_id and bot_token and channel_id)

        fetch_url = (
            f"https://discord.com/api/webhooks/{webhook_id}/{webhook_token}"
            f"/messages/{message_id}"
        )

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            await asyncio.sleep(poll_interval)
            try:
                async with session.get(fetch_url) as resp:
                    if resp.status >= 300:
                        continue
                    data = await resp.json()
            except Exception as e:
                log.debug("approval poll error: %s", e)
                continue
            for r in data.get("reactions") or []:
                emoji = (r.get("emoji") or {}).get("name")
                count = r.get("count", 0)
                if count < 1 or emoji not in (APPROVE_EMOJI, DENY_EMOJI):
                    continue
                if verify:
                    ok = await _operator_reacted(
                        session, bot_token, str(channel_id),
                        str(message_id), emoji, str(operator_id),
                    )
                    if ok is None:
                        continue  # check failed — keep polling, don't accept
                    if not ok:
                        log.info(
                            "[%s] %s reaction %s ignored (not operator)",
                            agent_name, tool_name, emoji,
                        )
                        continue
                if emoji == APPROVE_EMOJI:
                    log.info("[%s] %s approved (%s)", agent_name, tool_name, APPROVE_EMOJI)
                    return "approve", "operator approved"
                log.info("[%s] %s denied (%s)", agent_name, tool_name, DENY_EMOJI)
                return "deny", "operator denied"

    log.info(
        "[%s] %s approval timeout (%.0fs) — default deny",
        agent_name, tool_name, timeout_seconds,
    )
    return "timeout", f"no reaction within {int(timeout_seconds)}s → default deny"


# ---- Pattern matching for "dangerous" commands ---------------------------

def compile_patterns(raw: list[str]) -> list[re.Pattern]:
    return [re.compile(p) for p in raw if isinstance(p, str) and p]


def match_dangerous(command: str, patterns: list[re.Pattern]) -> str | None:
    """Return the first matching pattern's source (for human explanation),
    or None if the command is clean."""
    for p in patterns:
        if p.search(command):
            return p.pattern
    return None
