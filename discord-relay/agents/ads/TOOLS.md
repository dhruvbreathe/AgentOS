# TOOLS.md — Local Notes (Ember / ads)

## Channels

- **Apple Search Ads** — primary iOS acquisition surface. Rules live in Search Ads itself.
- **Meta (Facebook + Instagram)** — demographic + interest targeting. Automated rules under Campaigns → Rules.
- **TikTok Ads** — confirm account setup on first session
- **Google Ads** — if/when we test search
- **Reddit Ads** — not live; mention only if `reddit-crawler` pattern data suggests demand

## Attribution

- **Mixpanel** — primary product event store. Confirm that ad-source UTMs / attribution IDs land as event properties.
- **PostHog** — funnel / Core Web Vitals for ad-driven web traffic
- **MMP** (AppsFlyer / Adjust / Branch) — if wired; confirm on first session. Apple Search Ads integrates natively with App Store Connect.
- **Post-install attribution window:** 24h click / 1-day view as baseline; longer windows are not usually meaningful for a $12/mo SaaS

## Budget posture

- **Pre-approved daily budget ceiling:** TBD (Dhruv confirms on first session, record here)
- **Per-campaign default cap:** no campaign over the pre-approved per-day cap without explicit approval
- **Auto-pause rule:** CPA > 1.5× target for 3 consecutive days → pause
- **Weekly review:** Monday morning. Wrap the prior week, propose the coming one.

## Obsidian vault (durable memory)

- **Campaign archive:** `Topics/Campaigns/YYYY-MM-<slug>.md` — setup, creative, results, lessons
- **Attribution notes:** `Topics/Attribution.md` (create if missing) — how events map to channels
- **CAC history:** `Topics/CAC History.md` (create if missing) — per-channel, per-month, plotted against MRR
- **Kill rules:** `Topics/Ads Kill Rules.md` (create if missing) — the canonical auto-pause catalog
- **Decisions:** `Company/DECISIONS.md` — anything that changes spend strategy materially
- **Loop scoring context:** `Sessions/2026-04-05-main-autoresearch-loop-orchestrator.md` — my results feed the 7 AM loop scoring
- **My daily memory:** `agents/ads/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/ads/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/ads/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1471218500969435156` (`#ads`)
- **My Discord identity:** own bot (`bot_token_env: ADS_BOT_TOKEN`)
- **My webhook:** `ADS_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | weekly wrap, new-channel proposal, spend/product conflict | `1469505325102006490` |
| `media` (Pixel) | creative variant request for a test | `1469500272802926653` |
| `marketing` (Mira) | shared-audience overlap, copy request | `1469778689322647800` |
| `web-developer` (Indra) | landing-page variant, CWV check on an ad-driven page | `1470278378077814804` |
| `backend-developer` (Atlas) | event instrumentation, attribution pipeline | `1471890585223954503` |
| `qa` (Kestrel) | conversion-flow regression from an ad path | `1470297479722565647` |
| `project-manager` (Tempo) | task state | `1470690373667127420` |

## Cadence

- **Daily:** spend + CPA pass. Report only if something changed materially; don't noise up the channel.
- **Weekly Monday:** full wrap. Spend, revenue contribution, CAC by channel, next-week plan.
- **7 AM daily loop orchestrator:** my numbers feed into the autoresearch loop that `main` runs.

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
