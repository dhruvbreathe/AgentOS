# TOOLS.md — Local Notes (Deepali)

## Obsidian vault (my research surface)

Durable user-facing signal lives here. I read these every session and write to them when patterns emerge.

- **Topic notes:** `Topics/<feature-or-theme>.md` — where I distill recurring user signal
- **User research:** `User-Research/YYYY-MM-DD-<topic>.md` (if it doesn't exist yet, I can create the folder)
- **Sessions:** `Sessions/YYYY-MM-DD-<topic>.md` — my own session logs (what I did, what I found)
- **Team roster:** `Company/TEAM.md` — always the source of truth for routing
- **Decisions that affect UX:** `Company/DECISIONS.md` — scan before drafting responses that reference roadmap
- **My daily memory:** `agents/deepali/memory/YYYY-MM-DD.md` (this workspace)
- **My durable lessons:** `agents/deepali/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/deepali/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1469503216545693766` (`#deepali-cdo`)
- **My Discord identity:** separate bot (`bot_token_env: DEEPALI_BOT_TOKEN`) — distinct name and avatar
- **My webhook:** `DEEPALI_WEBHOOK_URL`

## Cross-agent routes I use

From `Company/TEAM.md` (vault wins on any drift):

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | user signal that would change priorities | `1469505325102006490` |
| `project-manager` (Tempo) | something is blocked and users are feeling it | `1470690373667127420` |
| `ios-developer` | user-reported bug in iOS app | `1470499341763608681` |
| `android-developer` (Ravi) | user-reported bug in Android app | `1471023591033278484` |
| `backend-developer` | account / sync / subscription issue | `1471890585223954503` |
| `qa` | repro steps for a reported bug | `1470297479722565647` |
| `marketing` | tone check on outbound, or a user whose message became a testimonial | `1469778689322647800` |
| `media` (Pixel) | a user story worth turning into content (with consent) | `1469500272802926653` |

## Response drafting vs. direct reply

- **Routine inbound** (app-store review thanking us, simple "how do I reset my breath pace" question): reply directly.
- **Anything referencing pricing, subscription, refund, bug, or unhappy user:** draft and post in the channel for Dhruv/Deepali-the-human to review before sending.
- **Anything with potential press / legal / investor exposure:** always draft-for-approval. No exceptions.

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
