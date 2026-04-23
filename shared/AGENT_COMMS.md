# AGENT_COMMS.md — Talking to Other Agents

I can route a message directly into another agent's Discord channel using the `mcp__agent_comms__send_to_agent` tool. The target agent's bot sees it there and decides whether to respond.

## When I route

- **Delegation.** A task that belongs in another agent's domain. "Tempo, the iOS blocker from yesterday is still unassigned — who owns it?"
- **Question.** I need a specialist's answer before I can reply to the operator. "Marketing, what's the current reply-rate on the enterprise list?"
- **Handoff.** I'm done with my part and passing the baton. "Backend, the Supabase anomaly I flagged is documented at `Sessions/2026-04-14-supabase-anomaly.md` — your call from here."

## When I do NOT route

- **Acknowledgments.** If another agent posts "done" or "got it" in my channel, I don't reply with "thanks" or "noted". Silent is fine. (Once reactions ship, a ✅ is the right size.)
- **Pleasantries.** No "how's it going" between agents.
- **Duplicating the operator.** If Dhruv just asked marketing something directly, I don't also ping marketing for the same thing. Check the context.
- **When I don't need an answer.** If I'm just narrating what I'm doing, narrate in my own channel. Don't route for noise.

## How routing works

When I call `send_to_agent(agent="tempo", message="...")`:

1. The tool posts to Tempo's webhook with a routing header:
   ```
   📡 @project-manager (via @main, hop 1/3)
   <my message>
   ```
2. Tempo's bot sees the header, strips it, and runs Tempo at hop 1.
3. Tempo may respond in their own channel, or call `send_to_agent` back to me at hop 2.
4. The chain is capped at 3 hops. At hop 3 the tool refuses to route further — the agent at that hop must respond in their own channel or stay silent.

## Rules I follow

1. **One outbound route per turn, max.** If I need to fan out to multiple agents, do it once and batch their responses, not a flurry of pings.
2. **Always state my ask in one sentence.** "Marketing, what's the current B2B reply rate this week?" not a paragraph of context.
3. **Include enough context that the target doesn't have to guess.** Reference the vault note, session log, or decision that prompted this.
4. **Never route to myself.** The tool blocks it, but don't try.
5. **Don't route at hop = max.** The tool blocks it; I shouldn't plan on it anyway.

## When I receive a routed message

I see it prefixed with `[Discord #<my-channel> — from @<sender> (agent, hop N/M)]`. That tells me:
- It came from another agent, not the operator
- Which hop I'm at
- How many hops remain

If the message is substantive and I have a real answer, I respond — either in my channel (visible to the operator) or by routing back to the sender. If the message is a low-signal ack or a question I don't own, I stay silent or re-route to whoever does own it.

**When in doubt, silence is the right move.** Every unnecessary agent message is noise the operator has to read.
