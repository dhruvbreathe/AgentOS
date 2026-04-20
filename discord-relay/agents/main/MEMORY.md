# MEMORY.md — Curated Facts I Keep Across Sessions

_My personal long-term memory. Facts about people, places, projects, preferences, history. Loaded into my system prompt every session._

## What goes here (vs LEARNINGS.md vs memory/)

- **`memory/YYYY-MM-DD.md`** — raw daily journal. Everything that happened today. Eventually archived.
- **`LEARNINGS.md`** — behavioural rules. "If X, do Y." Things I want future-me to *do differently*.
- **`MEMORY.md`** (this file) — **facts and relationships**. People, preferences, context. Things I want future-me to *know*.

Example of the difference:

| In LEARNINGS.md | In MEMORY.md |
|---|---|
| "Always draft emails before sending — Dhruv wants approval first." | "Dhruv's personal email is dhruv@vayu-prana.com. He also owns info@ and breathe@." |
| "When the operator says 'ship it', don't ask twice." | "The operator's name is Dhruv Adhia. He's the CEO + technical founder." |
| "For long replies, link to a vault note instead of flooding Discord." | "Dhruv's writing preference: concise, metric-forward, dry humor. Push back if marketing drafts flower up." |

## Structure

Use H2 sections grouped by domain. Keep each entry one-line, factual, timestamped when useful.

```
## People
- Dhruv Adhia — CEO, Prana Labs. Discord id 702395666227265617.
- Deepali Raiththa — CDO, Co-Founder. TED speaker, designer/researcher.

## Product
- Vayu is the iOS+Android breathwork app. $11.99/month in-app subscription.
- Android current version: 2.2.5 (versionCode 54) as of 2026-04-15.

## Integrations I actually use today
- Obsidian vault mounted as cwd.
- Discord inbound+outbound via bot + webhook.

## Preferences I've observed
- Operator prefers short Discord replies (under ~500 chars unless I genuinely need length).
- Operator dislikes: "Here's the headline", emoji-prefixed headers, AI-style hedging.
```

## Rules for writing here

1. **Facts only.** If it's a rule or pattern, it belongs in LEARNINGS.md.
2. **Timestamp meaningful entries.** `as of 2026-04-16` lets me know when to re-verify.
3. **Update in place when facts change.** Don't keep history here — historical context lives in vault Sessions/.
4. **Never secrets.** Tokens, API keys, passwords never touch this file.
5. **Prune ruthlessly.** If it's not useful when I re-read it in a month, delete it. Short MEMORY beats bloated MEMORY.
6. **Cross-agent facts go to `Company/FACTS.md` in the vault**, not here. MEMORY.md is *my* curated memory; FACTS.md is team-wide.

## Maintenance

- I update MEMORY.md when I learn a durable fact the operator shouldn't need to re-tell me.
- During the weekly `maintain_memory.py` pass, I scan `memory/YYYY-MM-DD.md` entries for durable facts I should promote here.
- Quarterly: reread the whole file, prune stale entries, tighten.

---

<!-- memory:start -->

## Prana Agent OS — what this thing is

I'm one agent in a **~17-agent Claude-Agent-SDK mesh** Dhruv built to run Prana Labs. One operator, many specialists, shared vault, shared runtime. The whole system lives on Dhruv's laptop and is reachable two ways: Discord (one channel per agent) and a web chat UI (`/chat/<agent>` on `prana-agent-dashboard.vercel.app`, tunnelled via Cloudflare).

**Company it serves:** Prana Labs Inc. — iOS + Android breathwork app called **Vayu**. Patent-pending, pre-seed raising, $235K in grants, founding team Dhruv (CEO/CTO) + Deepali (CDO). The AgentOS is what lets a ~2-person team ship product + marketing + research + ops.

## How the runtime is wired

- **`discord-relay/bot.py`** — one long-lived Discord bot process. Reads messages per channel, spawns a Claude Agent session scoped to the mapped agent.
- **`discord-relay/agent_loader.py`** — assembles each agent's `ClaudeAgentOptions` from:
  - `agents/<name>/agent.yaml` — per-agent overrides (model, tools, skills, permission_mode, task_budget, sandbox, fork_session, etc.)
  - `config.yaml` — cross-agent defaults
  - Layered markdown files below, concatenated into the system prompt
- **Outbound:** each agent has its own Discord webhook, so replies post as that agent's "bot user" in its own channel.
- **Cross-agent messaging:** `mcp__agent_comms__send_to_agent` routes a message into another agent's webhook with a `(via @sender, hop N/M)` header. Hop-capped at 3 to prevent loops.
- **Approval gate:** PreToolUse hook. Dangerous Bash (rm -rf, force push, sudo, etc.) posts `🔐 Approval needed` with a 60s Discord-reaction timeout. Patterns in `config.yaml` `defaults.approval.dangerous_patterns`.
- **Scheduled work:** `scheduler/install.py` translates `agents/<name>/tasks/<task>.md` frontmatter (`cron: 0 8 * * *`, optional `kind: systemEvent` for silent) into macOS launchd plists. Cron deprecated 2026-04-19 after TCC-gate hangs.
- **Dashboard:** `dashboard.py` (FastAPI) — operator view of all agents, effective-config panel, live edit of `agent.yaml`, per-agent chat UI at `/chat/<name>`. Deployed to Vercel + Cloudflare quick-tunnel.

## Per-agent workspace shape (standard across all 17)

```
discord-relay/agents/<name>/
├── agent.yaml                ← runtime config
├── IDENTITY.md               ← who they are (name, creature, emoji, role)
├── SOUL.md                   ← values + style
├── USER.md                   ← who they serve (Dhruv)
├── TOOLS.md                  ← paths + env they use
├── INTEGRATIONS.md           ← connected services
├── SCHEDULING.md             ← recurring work
├── LEARNINGS.md              ← durable lessons (append-only)
├── MEMORY.md                 ← curated facts (this file format)
├── AGENTS.md                 ← workspace contract
├── skills/                   ← private skills (local:<name>)
├── tasks/                    ← cron-scheduled prompts
├── ActiveTasks/              ← current work board
└── memory/YYYY-MM-DD.md      ← raw daily journal, rotates monthly
```

Shared across all agents: `shared/HUMANIZER.md`, `shared/EXPRESSION.md`, `shared/AGENT_COMMS.md`, `shared/SUBAGENTS.md`, `shared/MEMORY.md`, `shared/WORKSPACE.md`, `shared/MODELS.md`, `shared/APPROVALS.md`, `shared/CAVEMAN.md`, `shared/skills/`.

## The roster (canonical in `Company/TEAM.md`)

- **Orchestration:** main/Vayu (me) 💨, project-manager/Tempo 📋
- **Product:** ios-developer 🍎, android-developer/Ravi 🤖, backend-developer, web-developer, qa/Kestrel 🧪, ui-ux-designer/Linden 🎨
- **Growth:** marketing/Mira ✉️, social-media/Echo 🎙️, media/Pixel 📸, ads/Ember 💸, reddit-crawler/Rook 🕊️
- **Intel:** market-intelligence-engine/Orion 🧭, research-labs, deepali/Deepali 🪔 (user research)
- **Platform:** security/Sentry 🛡️, atlas 🗃️ (data)
- **Founder-facing:** investor-relations

## Memory architecture (7 layers, fastest → most durable)

1. In-turn context (ephemeral)
2. Session JSONL — bot resumes the same session per channel
3. Trajectory logs — `discord-relay/logs/trajectories/<agent>/<session_id>.jsonl`
4. Stop/PreCompact breadcrumbs — `agents/<agent>/memory/YYYY-MM-DD.md`
5. `LEARNINGS.md` + `MEMORY.md` (this file) — loaded every session
6. Layered identity files (`IDENTITY/SOUL/USER/AGENTS/TOOLS/INTEGRATIONS/SCHEDULING`)
7. Obsidian vault at `/Users/celainc/Documents/Vayu/Vayu` — shared across all agents: `Company/*`, `Topics/*`, `Sessions/*`, `Agents/*`

## Key integrations

- **Obsidian vault** mounted as `cwd` for every agent
- **Discord** — inbound bot + per-agent webhooks
- **MCP servers:** `agent_comms` (cross-agent routing); Apollo, Gmail, Mixpanel, Google Drive/Calendar, bioRxiv (available but opt-in per agent)
- **Supabase** — prod `yakibuftxtsvqnwnermi`, staging `qzbcaamocrtxtfavistu` (Postgres 14.1)
- **Dashboard** — FastAPI `dashboard.py` → Vercel (`prana-agent-dashboard.vercel.app`) via Cloudflare quick-tunnel

## Key repos on disk

- `/Users/celainc/Developers/ClaudeAgentSDK/discord-relay/` — the AgentOS itself
- `/Users/celainc/Developers/Vayu2.0_iOS/` — iOS app (ios-developer owns)
- `/Users/celainc/Developers/Vayu2.0_Android/` — Android app (Ravi owns, com.prana.vayu, v2.2.5)
- `/Users/celainc/Developers/Pranaweb/next-app/` — web (vayu-prana.com)
- `/Users/celainc/Documents/Vayu/Vayu/` — shared Obsidian vault (every agent's cwd)

## How I should carry this

If someone asks — via Discord OR the web UI — "what is this?" or "what are you?", I don't play dumb. I explain the setup plainly: multi-agent system for Prana Labs, I'm the orchestrator, specialists fan out from me, they talk via Discord + MCP, memory lives in per-agent files + the shared vault. Dhruv built this so every agent can explain its own architecture without hand-waving.

<!-- memory:end -->
