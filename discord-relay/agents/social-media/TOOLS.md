# TOOLS.md — Local Notes (Echo / social-media)

## Platforms

| Platform | Account | Voice |
|---|---|---|
| LinkedIn | Vayu company page + Dhruv personal (with sign-off) | professional, opinionated, long-form posts OK |
| Twitter / X | `@vayu-prana` (confirm handle on first session) | short, punchy, real-time; threads when warranted |
| Instagram | `@vayu.prana` (confirm handle) | visual-first, reels > static |
| Threads | same handle as IG | conversational, feed-feel |
| (deferred) YouTube | `media` owns uploads of long video; I adapt clips |
| (deferred) Pinterest / Tumblr | not live |

Confirm handles and access paths on first session — record here so future me doesn't ask twice.

## Scheduling stack

- **Primary:** Buffer / Later / Metricool / native scheduling — confirm which on first session
- **Fallback:** native scheduling in each platform's composer
- **Calendar of record:** `Topics/Social Calendar.md` in the vault (weekly, per platform)

## Platform output specs

| Surface | Character / size limits |
|---|---|
| LinkedIn post | ~3000 chars; first 200 chars pre-expand matter most |
| LinkedIn article | long-form; different beast |
| Twitter / X | 280 chars (free); threads OK |
| Instagram caption | 2200 chars, first 125 pre-expand |
| Instagram reel | 90s sweet spot; pinned hook in first frame |
| Threads | 500 chars |

## Cadence baseline

- **LinkedIn:** 2–4× per week, mid-morning weekday
- **Twitter / X:** 3–5× per week, spread across the day
- **Instagram:** 2–3 posts + 1–2 reels per week
- **Threads:** piggyback on Twitter content when it translates

Exact cadence recorded in `Topics/Social Calendar.md`; confirm on first session.

## Obsidian vault (durable memory)

- **Calendar:** `Topics/Social Calendar.md` — the living weekly plan
- **Voice guide:** `Topics/Social Voice.md` (create if missing) — per-platform tone rules
- **Campaign archive:** `Topics/Campaigns/YYYY-MM-<slug>.md` (shared with `media` / `marketing`)
- **Post performance:** `Topics/Social Performance.md` — weekly snapshot
- **Sessions:** `Sessions/YYYY-MM-DD-social-<topic>.md`
- **My daily memory:** `agents/social-media/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/social-media/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/social-media/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1471339567071101045` (`#social-media`)
- **My Discord identity:** own bot (`bot_token_env: SOCIAL_BOT_TOKEN`)
- **My webhook:** `SOCIAL_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `media` (Pixel) | asset request, platform-correct spec | `1469500272802926653` |
| `marketing` (Mira) | shared-audience coordination; a post tied to an outreach moment | `1469778689322647800` |
| `ads` (Ember) | avoid repeating paid creative organically | `1471218500969435156` |
| `reddit-crawler` (Rook) | cross-platform timing so Vayu isn't everywhere at once | `1471675794844680212` |
| `deepali` (CDO) | weekly brand-voice review; anything with Deepali's face | `1469503216545693766` |
| `main` (Vayu) | launches, milestones, any fundraising-adjacent post | `1469505325102006490` |
| `project-manager` (Tempo) | task state | `1470690373667127420` |

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
