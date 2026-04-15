# TOOLS.md — Local Notes (Atlas / backend-developer)

## Data + services

- **Supabase project** — confirm project ID / URL on first session and record here
- **Hosting** — Render (service names + env TBD on first session)
- **Primary DB** — Postgres via Supabase; pgvector for any embedding work
- **Storage** — Supabase Storage (buckets TBD)
- **Auth** — Supabase Auth; social providers + magic link status TBD
- **Edge functions** — Deno runtime via Supabase
- **Store reports** — App Store Connect reports, Play Console reports; ingested → normalized → dashboard
- **Email provider** — for transactional; confirm SendGrid / Postmark / Brevo on first session
- **Payments** — App Store IAP + Play Billing; B2B invoicing TBD

## Environments

- `local` — docker-compose + Supabase CLI, or direct against the dev project
- `staging` — if we have one; confirm
- `production` — live

Rule: nothing runs on prod that hasn't first run on staging (or at minimum local with a prod-shaped snapshot).

## Obsidian vault (durable memory)

- **Schema doc:** `Topics/Supabase Schema.md` (create if missing) — tables, columns, relationships, RLS per table
- **API contracts:** `Topics/API Contracts.md` (create if missing) — per endpoint: method, path, request, response, auth
- **Migrations log:** `Topics/Migrations.md` (create if missing) — each migration with date, rollback plan, shipped-at
- **Store reports pipeline:** `Topics/Store Reports Pipeline.md`
- **Incidents:** `Sessions/YYYY-MM-DD-backend-<incident>.md`
- **Decisions:** `Company/DECISIONS.md` — anything that touches data durability or cost
- **My daily memory:** `agents/backend-developer/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/backend-developer/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/backend-developer/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1471890585223954503` (`#backend-developer`, Dhruv category — confirmed current per 2026-02-13 rebinding)
- **My Discord identity:** own bot (`bot_token_env: BACKEND_BOT_TOKEN`)
- **My webhook:** `BACKEND_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `ios-developer` (Aria) | API contract change, breaking or pending | `1470499341763608681` |
| `android-developer` (Ravi) | API contract change, breaking or pending | `1471023591033278484` |
| `web-developer` (Indra) | dashboard API, lead-capture endpoints | `1470278378077814804` |
| `qa` (Kestrel) | contract repro / fix-confirm | `1470297479722565647` |
| `security` (Sentry) | RLS review, service-account scope, data handling | `1471886526198714449` |
| `deepali` | subscription/account bug needing DB trace | `1469503216545693766` |
| `main` (Vayu) | material decisions, migration risk, cost spike | `1469505325102006490` |
| `project-manager` (Tempo) | task state updates | `1470690373667127420` |

## Local environment habits

- `supabase` CLI (confirm version on first session)
- `psql` connected to staging + local (never prod without explicit approval)
- `curl` for endpoint sanity checks; `httpie` when I want it readable
- Render CLI or dashboard for service ops
- `pg_dump` + pinned retention for backups

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
