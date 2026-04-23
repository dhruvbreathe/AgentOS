---
name: taskflow-inbox-triage
description: Classify and route an incoming task, question, or inbound message. Use when a message lands and you need to decide: do I own it, who owns it, is it urgent, does it need a task card. Outputs a routing verdict.
---

# taskflow-inbox-triage — classify before you act

## When to reach for this

- A message arrives in a channel and you're unsure if it's yours
- The operator dumps a list of things and wants them routed
- A cron-triggered digest has multiple items needing different owners
- You're about to "just handle it" but the right move might be to hand off

## The decision tree

```
INBOUND MESSAGE
     │
     ▼
┌─────────────────────────────────┐
│ Is it directed at me?           │
│ (name mention, channel match,   │
│ or clearly my domain)           │
└──────┬────────────────┬─────────┘
       │ yes            │ no
       ▼                ▼
  Do I own it?      Silent — not
  (check my         my message
  IDENTITY.md)
       │
   ┌───┴───┐
   │ yes   │ no
   ▼       ▼
 Act    Route via
        send_to_agent
```

## Classify by type

| Type | Signal | Route to |
|---|---|---|
| **Bug / broken** | "X is broken", "not working", stack trace | owning dev agent (ios/android/backend/web) |
| **Metric / analytics** | "numbers", "reply rate", "DAU" | marketing or market-intelligence-engine |
| **User voice / support** | customer quote, review, complaint | deepali |
| **Scheduling / logistics** | "when", "schedule X", "remind me" | main (me) or project-manager |
| **Creative / copy** | "draft an email", "write a post" | marketing / media / social-media |
| **Security / audit** | "audit", "leak", "key", "rotate" | security |
| **Research / intel** | "what's X doing", "who's in space" | market-intelligence-engine |

## Urgency ladder

- 🔥 **Now** — user-facing break, revenue-stopping, security leak. Drop whatever, handle, tell operator post-hoc.
- ⚠️ **Today** — deadline today, operator waiting. Acknowledge immediately with ETA.
- 📋 **This week** — planned work. Write a task card, schedule.
- ❄️ **Later** — nice-to-have. Ice Box in Trello or back of `TASKS.md`.

## Output shape

Produce a routing verdict the operator can scan:

```
📋 **Triage:**
- 🔥 Bug: Apple Sign-In broken on iOS — routing to @ios-developer
- ⚠️ Metric: today's DAU dip — routing to @market-intelligence-engine
- 📋 Post idea: Reddit thread r/breathwork — task for @social-media

Tracking these in `Agents/TASKS.md` under today's date.
💨
```

## When NOT to triage

- Single clear message with obvious owner — just route, don't narrate the triage
- Operator already directed it to someone — don't second-guess
- Message is pure ack/thanks — silent

## Writing task cards

If any item needs durable tracking, write a card to `Agents/TASKS.md` (Obsidian vault):

```markdown
## YYYY-MM-DD — <short title>
- **Owner:** @<agent>
- **Type:** bug / metric / creative / ops
- **Urgency:** 🔥/⚠️/📋/❄️
- **Context:** 1-2 sentences
- **Next step:** concrete action
- **Source:** Discord #channel @ timestamp (or vault note)
```
