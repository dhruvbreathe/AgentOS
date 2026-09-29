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
    task_ctx: dict | None = None,
    ctx: dict | None = None,
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

    # ---- Task board (2026-09-29, kanban.py) ------------------------------
    # Durable handoffs: the card runs in the background in the assignee's
    # fresh session and the result comes BACK to this agent automatically.
    # One batch per turn, so the requester is woken once with all results.
    import secrets as _secrets

    task_ctx = task_ctx or {}
    # `ctx` is a mutable per-channel holder when the server lives in a warm
    # session across turns (session_pool.py); relay sets ctx["batch"] each
    # turn so one turn's delegations still form one batch.
    _own_batch = f"{sender_name}:{_secrets.token_hex(4)}"
    # Inside a worker, the card's own chain (+ this agent) is the loop guard.
    board_chain = [c for c in (task_ctx.get("chain") or chain).split(">") if c]
    depth = int(task_ctx.get("depth", -1)) + 1

    def _text(t: str) -> dict:
        return {"content": [{"type": "text", "text": t}]}

    @tool(
        "delegate_task",
        "Hand a piece of WORK to another agent via the task board. Unlike "
        "send_to_agent (a chat message), the task runs in the background in "
        "the assignee's own session and its result is delivered back to you "
        "automatically, in one turn, once every task you delegate this turn "
        "has finished. Don't wait or poll. Use depends_on (comma-separated "
        "task ids) to run a task only after others are done.",
        {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "assignee agent name"},
                "title": {"type": "string", "description": "one-line task title"},
                "instructions": {"type": "string", "description": "everything the assignee needs; they won't see this conversation"},
                "done_when": {"type": "string", "description": "acceptance criteria (optional)"},
                "depends_on": {"type": "string", "description": "comma-separated task ids that must finish first (optional)"},
            },
            "required": ["agent", "title", "instructions"],
        },
    )
    async def delegate_task(args):
        import kanban

        target = str(args.get("agent", "")).strip().lstrip("@")
        if target == sender_name:
            return _text("Refused: you cannot delegate to yourself.")
        if target in board_chain:
            return _text(
                f"Refused: @{target} is already upstream of this task "
                f"({' → '.join(board_chain + [sender_name])}); that would loop."
            )
        if depth > kanban.MAX_DEPTH:
            return _text(f"Refused: delegation depth ceiling ({kanban.MAX_DEPTH}) reached.")
        if not any(a.name == target for a in load_all_agents().values()):
            return _text(f"Unknown agent '{target}'.")
        parents = [p for p in str(args.get("depends_on") or "").replace(" ", "").split(",") if p]
        try:
            card = kanban.create(
                from_agent=sender_name, to_agent=target,
                title=str(args["title"]), body=str(args["instructions"]),
                done_when=str(args.get("done_when") or ""), parents=parents,
                chain=">".join(board_chain + [sender_name]),
                batch=(ctx or {}).get("batch") or _own_batch, depth=depth,
            )
        except ValueError as e:
            return _text(f"Refused: {e}")
        try:
            from task_ledger import aledger_create_handoff

            lid = await aledger_create_handoff(
                sender_name, target, f"[{card['id']}] {args['title']}\n\n{args['instructions']}"
            )
            if lid:
                kanban.set_ledger_id(card["id"], lid)
        except Exception as e:  # noqa: BLE001 — ledger mirror is best-effort
            log.warning("kanban ledger mirror failed: %s", e)
        waiting = f" (waits on {', '.join(parents)})" if card["status"] == "todo" else ""
        return _text(
            f"Created task {card['id']} for @{target}{waiting}. It runs in the "
            f"background; the result comes back to you here automatically. "
            f"Don't wait or poll: finish your turn."
        )

    @tool(
        "task_status",
        "Look up a task-board card by id: status and result summary.",
        {"task_id": str},
    )
    async def task_status(args):
        import kanban

        card = kanban.get(str(args["task_id"]).strip())
        if not card:
            return _text("No such task.")
        return _text(
            f"{card['id']} @{card['from_agent']} → @{card['to_agent']}: {card['status']}\n"
            f"{card['title']}\n{card.get('summary') or ''}"
        )

    @tool(
        "complete_task",
        "Finish the task-board card you are working on: status `done` or "
        "`blocked`, plus a summary the requester can act on.",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "enum": ["done", "blocked"]},
                "summary": {"type": "string"},
            },
            "required": ["task_id", "status", "summary"],
        },
    )
    async def complete_task(args):
        import kanban

        tid = str(args["task_id"]).strip()
        status = str(args.get("status") or "done")
        if status not in ("done", "blocked"):
            status = "done"
        card = kanban.complete(tid, status, str(args["summary"]), by=sender_name)
        if not card:
            return _text(f"{tid} is not a running task assigned to you.")
        return _text(f"Recorded {tid} as {status}. The requester will get your summary.")

    return create_sdk_mcp_server(
        name="agent_comms", version="0.2.0",
        tools=[send_to_agent, delegate_task, task_status, complete_task],
    )
