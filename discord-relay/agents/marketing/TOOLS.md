# TOOLS.md — Local Notes (Mira / marketing)

## Outreach surface

- **Sender accounts:** `dhruv@vayu-prana.com`, `info@vayu-prana.com`, `breathe@vayu-prana.com` (confirm which is the right one per audience on first session)
- **Mail CLI:** Himalaya is wired today (`himalaya envelope list -a dhruv --folder INBOX`). `gog` CLI is queued behind OAuth setup — switch to it once `gog auth add dhruv@vayu-prana.com` is done.
- **Domain auth:** SPF, DKIM, DMARC all configured on `vayu-prana.com`. Deliverability is fine; reply rate is the lever.
- **Outreach folder:** `~/Downloads/Outreach/` — historical paths used `~/Documents/Prana/Outreach/`; that path is dead, do not use it.

## Apollo.io

- **Use:** prospect enrichment, contact data, sequence membership.
- **Auth:** Apollo MCP server (when added) or via Apollo CLI.
- **Posture:** read-heavy. I enrich and pull lists; I don't trigger Apollo-side sends without Dhruv's say-so.

## Obsidian vault (durable memory)

- **CRM:** `CRM/B2B/<Company>.md` per prospect. I create/update these as outreach progresses.
- **Sessions:** `Sessions/YYYY-MM-DD-marketing-<topic>.md`
- **Decisions log:** `Company/DECISIONS.md` — read before any pricing or positioning shift in copy
- **Strategy:** `Company/STRATEGY.md` — the source of truth for why we are reaching out
- **Outreach playbook (if exists):** `Topics/Outreach Playbook.md` — sequence templates, voice notes
- **Reply rate history:** `Sessions/2026-04-13-outreach-reply-rate-crisis.md` and similar — read so I don't repeat the same broad-targeting mistake
- **My daily memory:** `agents/marketing/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/marketing/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/marketing/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1469778689322647800` (`#marketing-finance-pr`)
- **My Discord identity:** own bot (`bot_token_env: MARKETING_BOT_TOKEN`)
- **My webhook:** `MARKETING_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | strategic / positioning / approval needed | `1469505325102006490` |
| `deepali` | user voice / testimonial / customer-touching outbound | `1469503216545693766` |
| `web-developer` (Indra) | lead-capture endpoint, landing-page test | `1470278378077814804` |
| `media` (Pixel) | assets — case-study graphics, testimonial reels | `1469500272802926653` |
| `ads` | shared audience / overlap check | `1471218500969435156` |
| `social-media` | when an outreach win becomes social-worthy | `1471339567071101045` |
| `project-manager` (Tempo) | task state, cron schedule changes | `1470690373667127420` |
| `investor-relations` | (when wired) handoff after first contact | TBD |

## Cadence

- **08:00 daily:** prospecting pipeline (enrich → draft → queue for approval)
- **13:00 daily:** mid-day prospecting pass (when ramped to higher volume)
- **17:00 daily:** afternoon prospecting + reply triage
- **08:05 daily:** investor outreach daily (separate from prospect job)
- **09:15 daily:** PR journalist prospecting
- **Mid-morning daily:** PR follow-up sequence check

These all run as cron jobs; changes go through Tempo via the SCHEDULING.md protocol.

## Volume ramp (current week)

- B2B prospecting: 15 → 25 → 35 → 50 sends/day over 4 weeks
- Brand/PR: 5 → 8 → 12 → 15 journalists/day

If actual reply rate stays below ~3% by week 2, we don't ramp — we pivot.

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
