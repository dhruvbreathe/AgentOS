"""In-process MCP server that exposes agent-to-agent communication to
Claude. One tool: `send_to_agent(agent, message)`.

The server is built fresh per turn with the sender's name and current hop
count closure-captured. This gives us hop-limit enforcement at the tool
layer: if the caller is at max_hops, the tool refuses to route further.

Transport: each agent's outbound webhook (from agent_loader).
Routing header injected at the top of the message so downstream bots can
parse sender/hop/max and humans can see who asked whom.
"""
from __future__ import annotations

import logging
import re

import aiohttp
from claude_agent_sdk import create_sdk_mcp_server, tool

from agent_loader import load_all_agents

log = logging.getLogger("agent-comms")

# `chain` is an optional `>`-joined list of every agent that has already
# routed this message (e.g. "main>backend-developer"). It is what makes true
# loop protection possible: we refuse routing to any agent already in the
# chain, so A→B→A is blocked instantly regardless of depth. The numeric
# hop/max pair is now only a safety ceiling on chain DEPTH, not the loop
# guard. Old messages without a chain segment still parse (group is None).
ROUTING_HEADER_RE = re.compile(
    r"^📡\s*@(?P<target>[\w-]+)\s*\(via\s*@(?P<sender>[\w-]+),\s*"
    r"hop\s*(?P<hop>\d+)/(?P<max>\d+)"
    r"(?:,\s*chain:\s*(?P<chain>[\w>-]+))?\)\s*\n?",
    re.MULTILINE,
)


def format_routing_header(
    target: str, sender: str, hop: int, max_hops: int, chain: str = ""
) -> str:
    chain_part = f", chain: {chain}" if chain else ""
    return f"📡 @{target} (via @{sender}, hop {hop}/{max_hops}{chain_part})\n"


def parse_routing_header(text: str) -> dict | None:
    """If `text` starts with a routing header, return its fields and strip
    it from `text`. Otherwise return None."""
    m = ROUTING_HEADER_RE.match(text)
    if not m:
        return None
    return {
        "target": m.group("target"),
        "sender": m.group("sender"),
        "hop": int(m.group("hop")),
        "max": int(m.group("max")),
        "chain": m.group("chain") or "",
        "body": text[m.end() :],
    }


# Cloudflare blocks UA-less / default-aiohttp-UA posts with error 1010 → 403
# (same lesson as scripts/doctor.py). Always send an explicit UA.
_WEBHOOK_UA = "DiscordBot (PranaAgentOS, 1.0)"
_WEBHOOK_TIMEOUT = aiohttp.ClientTimeout(total=30)
_CHUNK_AT = 1900  # Discord hard cap is 2000; leave headroom


def _split_chunks(text: str, limit: int = _CHUNK_AT) -> list[str]:
    """Split on paragraph, then line, then hard boundaries. Never silently
    drop the tail — long handoffs used to truncate mid-sentence at 1990."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
        while len(para) > limit:
            cut = para.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = para.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(para[:cut])
            para = para[cut:].lstrip()
        cur = para
    if cur:
        chunks.append(cur)
    return chunks


async def _post_to_webhook(webhook_url: str, content: str, username: str) -> str:
    # Strip em-dashes / en-dashes before posting (operator directive 2026-05-19).
    from text_lint import sanitize as _strip_emdash
    content = _strip_emdash(content, agent=username, surface="send_to_agent")
    chunks = _split_chunks(content)
    async with aiohttp.ClientSession(
        timeout=_WEBHOOK_TIMEOUT, headers={"User-Agent": _WEBHOOK_UA}
    ) as session:
        for i, chunk in enumerate(chunks):
            if len(chunks) > 1:
                chunk = f"{chunk}\n-# part {i + 1}/{len(chunks)}"
            async with session.post(
                webhook_url, json={"content": chunk[:1990], "username": username}
            ) as resp:
                if resp.status >= 300:
                    body = await resp.text()
                    return f"webhook {resp.status} on part {i + 1}/{len(chunks)}: {body[:200]}"
    return "ok" if len(chunks) == 1 else f"ok ({len(chunks)} parts)"


def build_comms_server(
    sender_name: str,
    current_hop: int,
    max_hops: int,
    chain: str = "",
):
    """Build an SDK MCP server scoped to the current turn.

    Two independent guards on outbound routing:

    1. Cycle detection (the real loop guard). `chain` is the `>`-joined list
       of every agent that already routed this message. Routing to an agent
       already in the chain (or to yourself) is refused as a true loop, at
       any depth. This is what lets legitimate deep fan-outs proceed: a chain
       of fresh agents never trips it.
    2. Depth ceiling (safety backstop). `current_hop + 1 > max_hops` refuses
       pathological runaway depth even when every agent is distinct. The
       ceiling is generous now (see config.yaml `max_hops`) precisely because
       cycle detection, not the number, is doing the loop-prevention work.
    """
    chain_list = [c for c in chain.split(">") if c]

    @tool(
        "send_to_agent",
        "Route a message to another AgentOS agent by posting into their "
        "Discord channel via webhook. Use for delegation, handoffs, or "
        "targeted questions in another agent's domain. The receiving "
        "agent sees the message and may respond. Loop-protected: "
        "respects hop limits automatically.",
        {"agent": str, "message": str},
    )
    async def send_to_agent(args):
        target_name = args["agent"].strip().lstrip("@")
        message = args["message"].strip()

        outgoing_hop = current_hop + 1
        # the agent now routing joins the chain the next agent will see
        new_chain = ">".join(chain_list + [sender_name])

        # Guard 1 — cycle detection (the real loop guard).
        if target_name == sender_name:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Refused: you cannot route to yourself.",
                    }
                ]
            }
        if target_name in chain_list:
            path = " → ".join(chain_list + [sender_name, target_name])
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Refused: routing to @{target_name} would form a loop "
                            f"({path}). @{target_name} already handled this chain — "
                            f"if you need their input again, respond in your own "
                            f"channel and let them pick it up fresh, or pick a "
                            f"different agent."
                        ),
                    }
                ]
            }

        # Guard 2 — depth ceiling (safety backstop against runaway fan-out).
        if outgoing_hop > max_hops:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Refused: chain-depth ceiling reached "
                            f"({current_hop}/{max_hops}). This chain has gone deep "
                            f"enough that it's likely runaway. Respond in your own "
                            f"channel or stay silent."
                        ),
                    }
                ]
            }

        all_agents = load_all_agents()
        target = next(
            (a for a in all_agents.values() if a.name == target_name), None
        )
        if target is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown agent '{target_name}'.",
                    }
                ]
            }
        if not target.webhook_url:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"No webhook configured for agent '{target_name}'. "
                            f"Ask Dhruv to add {target.raw.get('webhook_url_env')} "
                            f"to .env."
                        ),
                    }
                ]
            }

        header = format_routing_header(
            target=target_name,
            sender=sender_name,
            hop=outgoing_hop,
            max_hops=max_hops,
            chain=new_chain,
        )
        full_message = header + message

        result = await _post_to_webhook(
            target.webhook_url,
            full_message,
            username=f"{sender_name} → {target_name}",
        )
        if result != "ok":
            log.warning("send_to_agent webhook post failed: %s", result)
            return {"content": [{"type": "text", "text": f"Post failed: {result}"}]}

        # NOTE: we don't mirror into target's web_chat here. The webhook post
        # will cause Discord to deliver the message to the target's channel,
        # where bot.py's on_message handler mirrors it. Mirroring in both
        # places produced duplicates in the web UI.

        # Phase-1 task ledger (2026-07-05): first-hop routes are handoffs —
        # auto-create a ledger entry so aging work is visible to Tempo's
        # sweep instead of vanishing into chat history. Reply-routes deeper
        # in a chain (hop 2+) are answers, not new work — skip those.
        # Fail-open by contract: the message already delivered.
        ledger_note = ""
        if outgoing_hop == 1:
            from task_ledger import aledger_create_handoff

            task_id = await aledger_create_handoff(
                sender_name, target_name, message
            )
            if task_id:
                ledger_note = (
                    f" Ledger task `{task_id}` created — the receiver (or you) "
                    f"can close it with: ./.venv/bin/python task_ledger.py "
                    f"update {task_id} --status done"
                )

        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Sent to @{target_name} (hop {outgoing_hop}/{max_hops}). "
                        f"They will see it in their channel and decide whether to respond."
                        + ledger_note
                    ),
                }
            ]
        }

    return create_sdk_mcp_server(
        name="agent_comms", version="0.1.0", tools=[send_to_agent]
    )
