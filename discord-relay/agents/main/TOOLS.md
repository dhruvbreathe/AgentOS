# TOOLS.md — Local Notes (Vayu / main)

## Obsidian vault (my memory)

- Root: `/Users/celainc/Documents/Vayu/Vayu` (this is my `cwd`)
- **Search vault FIRST** before web / cloning repos. Use Glob/Grep on:
  - `Sessions/` — 80+ session logs, mine and other agents'
  - `Topics/` — 40+ topic notes (people, projects, tools)
  - `Conversations/` — raw chat exports
- Team roster with live channel IDs: `Company/TEAM.md`
- Canonical: `Company/OPERATING-SYSTEM.md`, `Company/CONTEXT.md`, `Company/DECISIONS.md`, `Company/STRATEGY.md`
- Active control plane: `Agents/TASKS.md`, `Agents/HANDOFFS.md`, `Daily/YYYY-MM-DD.md`
- My daily memory: `agents/main/memory/YYYY-MM-DD.md` (this workspace)
- My durable lessons: `agents/main/LEARNINGS.md` (loaded into my system prompt)
- My trajectories (what I did, per session): `logs/trajectories/main/<session_id>.jsonl`
- My status log: `OpenClaw/Agent Notes/main/status/YYYY-MM-DD.md` (vault)

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1469505325102006490` (`#virtual-ceo-cto-dhruv`) — ONLY the 8 AM / 8 PM digest and direct operator conversation. Not a dumping ground.
- **My webhook:** `MAIN_WEBHOOK_URL` (from `.env`, not in this file)

### Team channels I route to

| Pod | Agent | Channel ID |
|---|---|---|
| Product | `ios-developer` | `1470499341763608681` |
| Product | `android-developer` (Ravi) | `1471023591033278484` |
| Product | `backend-developer` | `1471890585223954503` (Dhruv category) |
| Product | `web-developer` | `1470278378077814804` |
| Product | `qa` | `1470297479722565647` |
| Product | `ui-ux-designer` | `1472412741795840120` |
| Product | `project-manager` | `1470690373667127420` |
| Growth | `marketing` | `1469778689322647800` |
| Growth | `social-media` | `1471339567071101045` |
| Growth | `ads` | `1471218500969435156` |
| Growth | `media` (Pixel) | `1469500272802926653` |
| Growth | `reddit-crawler` | `1471675794844680212` |
| Intel | `market-intelligence-engine` | `1472864038122815602` |
| Intel | `research-labs` | `1477129158114152570` |
| Platform | `security` | `1471886526198714449` |
| Customer | `deepali` | `1469503216545693766` |

If a channel ID here drifts from `Company/TEAM.md` in the vault, **the vault wins** — reread and update this file.

## File paths I reach for

- `~/Developers/ClaudeAgentSDK/discord-relay/` — this relay
- `~/.openclaw/workspace/` — OpenClaw per-agent workspaces (reference only, we're our own system)
- `/Users/celainc/Documents/Vayu/Vayu/` — vault (my `cwd`)

## Runtime baseline

- Model: `claude-opus-4-6` (inherits Dhruv's Claude.ai subscription default)
- Timezone: Pacific (Vancouver)
