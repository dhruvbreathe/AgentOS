# TOOLS.md — Local Notes (Tempo / project-manager)

## Obsidian vault (my primary surface)

My job mostly happens inside these files. I read them every session and write to them constantly.

- **Task state:** `Agents/TASKS.md` — the canonical work queue. Every task has owner, output, due horizon, dependency, status.
- **Handoffs:** `Agents/HANDOFFS.md` — what changed, where output lives, next step, whether Dhruv is blocked.
- **Escalations:** `Agents/ESCALATIONS.md` — stale/blocked items that need founder attention.
- **Roles:** `Agents/ROLES.md` — who owns what. Source of truth for routing.
- **Scrum snapshots:** `Agents/scrum-YYYY-MM-DD.md` — daily or tri-weekly.
- **Weekly review:** `Weekly/YYYY-MM-DD-operating-review.md` — Friday 09:00.
- **Strategy drift check:** `Company/STRATEGY.md`, `Company/DECISIONS.md` — referenced during weekly review.
- **My daily memory:** `agents/project-manager/memory/YYYY-MM-DD.md` (in this workspace)
- **My durable lessons:** `agents/project-manager/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/project-manager/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1470690373667127420` (`#project-manager`)
- **My Discord identity:** separate bot (`bot_token_env: PM_BOT_TOKEN`) — my avatar and name are distinct from Vayu's.
- **My webhook:** `PM_WEBHOOK_URL` (for cron-triggered scrum snapshots and weekly reviews)

## Cross-agent routes I use

From `Company/TEAM.md` (always re-read if drift is suspected — vault wins):

| Pod | Agent | Channel |
|---|---|---|
| Product | `ios-developer` | `1470499341763608681` |
| Product | `android-developer` | `1471023591033278484` |
| Product | `backend-developer` | `1471890585223954503` |
| Product | `web-developer` | `1470278378077814804` |
| Product | `qa` | `1470297479722565647` |
| Growth | `marketing` | `1469778689322647800` |
| Growth | `social-media` | `1471339567071101045` |
| Growth | `ads` | `1471218500969435156` |
| Growth | `media` | `1469500272802926653` |
| Intel | `research-labs` | `1477129158114152570` |
| Platform | `security` | `1471886526198714449` |
| Customer | `deepali` | `1469503216545693766` |
| **Escalation path** | `main` (Vayu) | `1469505325102006490` |

## Cadence

- **08:00 daily:** scan TASKS + HANDOFFS; post standup snapshot if anything changed.
- **14:00 daily (optional):** mid-afternoon sweep of stale items.
- **Fri 09:00:** weekly operating review.
- **Tue + Fri mornings:** twice-weekly coordination review (cross-pod dependencies).

## Runtime baseline

- Model: `claude-opus-4-6` (inherits CLI default)
- Timezone: Pacific (Vancouver)
