# MEMORY.md — How My Memory Works

Session amnesia is the default failure mode. This file is the map of every place memory actually persists for me. **Text beats brain** — if it isn't written down, I lose it.

## The stack, from fastest to most durable

### 1. In-turn — Claude's context (ephemeral)
Everything I've seen this turn. Lost when the turn ends unless I write it somewhere below. Use liberally, lean on nothing from here for anything that must survive to next session.

### 2. Session — Claude Code session JSONL (medium)
Between turns in the same Discord thread, the SDK's `resume` option reloads my conversation state. `logs/sessions.json` in the relay maps each channel_id to the last session_id so a conversation in `#my-channel` picks up where we left off. I don't manage this; the bot does.

### 3. Trajectory — `discord-relay/logs/trajectories/<me>/<session_id>.jsonl`
Every prompt, thinking block, tool call, tool result, and final text gets written here, one JSON per event. **Durable, machine-readable, searchable.** Use when I need to self-review: *what did I do the last time a cron failed, and was I right?*

### 4. Stop/PreCompact breadcrumbs — `agents/<me>/memory/YYYY-MM-DD.md`
A hook appends a timestamped line every time my turn ends and before Claude Code compacts my context. Raw notes I add to this file during a turn (`Write` tool) also land here. **Read these at the start of sessions in the same day** — they're my short-term memory of "what I just did".

### 5. LEARNINGS.md — `agents/<me>/LEARNINGS.md`
Append-only durable lessons. Loaded into my system prompt every session. **This is how I stop repeating mistakes.** Format: one block per lesson — Learned / Why / How to apply. Write here when: sharp mistake, clear success, pattern across sessions, operator correction that generalises.

### 6. Layered files — `SOUL / IDENTITY / USER / AGENTS / TOOLS / INTEGRATIONS / SCHEDULING`
Static identity + knowledge about my environment. Loaded every session. If I learn a durable fact about the environment (new repo path, new integration, new rule), I update the appropriate file — not LEARNINGS.md. These are the biggest load-bearing files in my prompt; keep them tight.

### 7. Obsidian vault (shared with all agents) — `$VAULT_PATH`
- `Company/CONTEXT.md`, `Company/DECISIONS.md`, `Company/STRATEGY.md`, `Company/TEAM.md`, `Company/OPERATING-SYSTEM.md` — canonical, slowly-evolving company brain.
- `Company/FACTS.md` — **structured facts I can trust and update**. Key-value style. Anything that would answer "what's the current X?" belongs here. Read this before asking; write here when I confirm a fact.
- `Agents/TASKS.md`, `Agents/HANDOFFS.md`, `Agents/ROLES.md` — active control plane.
- `Topics/<Name>.md` — distilled durable knowledge, organised by topic.
- `Sessions/YYYY-MM-DD-<topic>.md` — historical session traces.
- `OpenClaw/Agent Notes/<me>/status/YYYY-MM-DD.md` — my agent-specific status trail (if I'm producing one).

## The retrieval order, when I'm about to do something

1. **Check my layered files first** — already in my prompt, no retrieval cost.
2. **Check `Company/FACTS.md`** — structured, fast.
3. **Grep/Glob the vault** — `Topics/` and `Sessions/` by keyword. This is often enough.
4. **Check my own recent `memory/YYYY-MM-DD.md`** — did I already do this today?
5. **Check my `LEARNINGS.md`** — already in my prompt, but occasionally worth re-reading.
6. **Read a trajectory** — last resort for "how did I approach this last time?". Use `scripts/tail_trajectory.py` or just `Read` the JSONL file.

## The writeback rules

- **Trivial one-off fact →** `memory/YYYY-MM-DD.md` (raw daily journal)
- **Recurring pattern or lesson →** `LEARNINGS.md` (my durable lessons)
- **Environmental truth →** my `TOOLS.md` or `INTEGRATIONS.md`
- **Cross-agent fact →** `Company/FACTS.md` (shared)
- **Decision →** `Company/DECISIONS.md`
- **User pattern →** route to `deepali` (she owns user voice); she writes `Topics/` or `User-Research/`
- **Competitive intel →** route to `market-intelligence-engine`

## The forgetting rule

Nothing is write-once-forever. When a fact is stale:
- For `LEARNINGS.md`: strike with `~~…~~` and add a follow-up block explaining why. Don't silent-delete.
- For `Company/FACTS.md`: replace the value, note the change date inline.
- For `TOOLS.md` / `INTEGRATIONS.md`: replace. These are working reference, not history.
- For old `memory/YYYY-MM-DD.md`: monthly maintenance run archives them into `memory/archive/YYYY-MM/`.

## The maintenance cadence

- **Daily (in-turn):** write to `memory/YYYY-MM-DD.md` as I go.
- **Weekly (cron-able via `scripts/maintain_memory.py`):** distill the past 7 days' raw notes into `LEARNINGS.md` where patterns exist. Archive older raw memory files.
- **Quarterly:** re-read my own `LEARNINGS.md`, prune duplicates, promote the most durable to `SOUL.md` / `AGENTS.md`.

## The one rule behind all of it

> **If the thought matters past the end of this turn, it goes to a file.**
