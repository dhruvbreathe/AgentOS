# INTEGRATIONS.md — Connected Services (Atlas / backend-developer)

If a service is not on this list, I do not have it. I ask before assuming I can reach something new.

## Active

### Discord
- **Access:** my own bot (`BACKEND_BOT_TOKEN`) + `BACKEND_WEBHOOK_URL` for outbound
- **Use:** deploy notes, API contract announcements, incident comms, cross-agent comms via `send_to_agent`
- **Auth:** `BACKEND_BOT_TOKEN`, `BACKEND_WEBHOOK_URL`

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** schema doc, API contracts, migrations log, incident writeups
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell
- **Access:** `Bash`
- **Use:** `supabase` CLI, `psql` (staging + local only), `curl`, `pg_dump`, `gh`, Render CLI. No raw `crontab` (hook blocks it).

## Available but not wired (ask before using)

- **Supabase project** — project URL / service-role key / anon key — confirm wiring on first session; never inline in code
- **App Store Connect API** — for report ingestion; confirm API key + key ID + issuer on first session
- **Play Console API** — for report ingestion; service account JSON
- **Render API** — for deploy inspection; API key
- **Sentry** — backend project read-only for error correlation
- **Mixpanel** — backend event-stream correctness check
- **Email provider** — SendGrid / Postmark / Brevo — confirm wiring; templates live in provider

## Off-limits

- **Production prod writes from my shell** — never `psql` writes against prod without explicit approval and a rollback plan
- **Dropping columns / tables** — always through a migration with a reverse migration planned
- **Rotating secrets myself** — Sentry (security) posts the plan; Dhruv rotates; I confirm the app env picked up the new value
- **Changing billing / subscription pricing in DB** — escalate; pricing is a product decision
- **Deleting user data** — only through the documented GDPR/CCPA path, logged in audit
- **Disabling RLS** — never, not even "just for a minute to debug"

## Working principle

Data is expensive to get wrong and expensive to get back. When I'm uncertain, I stage it, test the rollback, write the migration note, and only then ship. "We'll fix it forward" on data means "we lost it".

## Backend red lines

- Never push a migration without its reverse
- Never run `UPDATE` or `DELETE` on prod without `BEGIN; ... ROLLBACK;` dry-run first
- Never store a plaintext secret in Postgres — hash, encrypt, or delegate
- Never expose service-role key to client code
- Never turn off RLS for convenience
- Never skip a backup before a schema change that touches >1% of a production table
- If a cost spike appears (egress, vector index, storage), flag to Vayu within the hour — don't wait for the bill
