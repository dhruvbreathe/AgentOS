# TOOLS.md — Local Notes (Rook / reddit-crawler)

## Reddit account + surface

- **Account:** `u/Icy_Imagination_5040` (Vayu-representing account, one voice only)
- **Auth:** confirm OAuth / password flow on first session; record env var names only, never the values
- **API:** Reddit OAuth API. Rate limits: 60 req/min for authenticated. Respect it.
- **Monitored subreddits:** 10 subs, list TBD — confirm current list on first session and record here. Historical context: breathwork, anxiety, mindfulness, stress, mental-health adjacent.

## Approved action matrix

| Action | Default posture |
|---|---|
| Monitor / read threads | unrestricted within approved subs |
| Post a comment | draft-first for any new sub for 30 days; direct after trust |
| Post a submission | never without Dhruv's explicit approval per-submission |
| DM any user | never |
| Upvote / downvote | neutral — don't vote from this account |
| Expand subreddit list | per-addition approval from Dhruv |

## State files

- **Opportunity JSON:** where I refresh the daily shortlist (path TBD — confirm on first session)
- **Engagement state:** per-thread `posted_at`, `last_checked_at`, `subreddit` to prevent dupes. Maintained by the engagement cron; I must read before posting.
- **Response drafts:** `~/Downloads/Reddit-drafts/YYYY-MM-DD/<thread-slug>.md` (confirm path)

## Obsidian vault (durable memory)

- **Subreddit playbook:** `Topics/Reddit Playbook.md` (create if missing) — per-sub rules, cadence, voice
- **Pattern signal log:** `Topics/Reddit User Signals.md` (create if missing) — recurring themes worth routing to deepali
- **Daily digests:** `Sessions/YYYY-MM-DD-reddit-monitor.md`
- **Engagement attempts:** `Sessions/YYYY-MM-DD-reddit-engagement-cron.md`
- **Dedupe notes:** `Sessions/2026-04-05-main-reddit-monitor-dedupe.md` — historical lessons on avoiding multi-agent double-posts
- **My daily memory:** `agents/reddit-crawler/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/reddit-crawler/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/reddit-crawler/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1471675794844680212` (`#reddit-crawler`)
- **My Discord identity:** own bot (`bot_token_env: REDDIT_BOT_TOKEN`)
- **My webhook:** `REDDIT_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | thread pattern would affect priorities; approval on new sub | `1469505325102006490` |
| `deepali` | user-pain pattern surfaced; possible research lead | `1469503216545693766` |
| `media` (Pixel) | thread asking for visual / explainer | `1469500272802926653` |
| `social-media` | cross-platform timing coordination | `1471339567071101045` |
| `project-manager` (Tempo) | task state | `1470690373667127420` |

## Cadence

- **Daily 09:00:** monitor run — refresh subreddit opportunities, shortlist top threads, post to Discord for review
- **1–2× daily:** engagement cron — select approved threads, draft + (for trusted subs) post comments
- **Weekly:** summary report — posts, reply rate, upvote rate, any pattern worth escalating

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
