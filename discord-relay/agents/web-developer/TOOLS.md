# TOOLS.md — Local Notes (Indra / web-developer)

## Codebases

- **`vayu-prana.com`** — Next.js (App Router), TypeScript, Tailwind. Path TBD; record on first session.
- **`vayu-dashboard`** — internal metrics surface, separate Vercel project. Path TBD.
- **Repo host:** GitHub (org/repo to confirm)
- **Build / deploy:** Vercel
- **Domain:** `vayu-prana.com` (apex + `www`); `dashboard.vayu-prana.com` for the internal surface (confirm)

## Vercel

- I never push to production without a preview link reviewed first.
- Env vars live in Vercel project settings; never inline secrets in code or commit `.env.*` files.
- Preview deploys are public by default unless protected — check before sharing externally.
- Production promotions go through `vercel promote` or the dashboard; never `vercel --prod` from a dirty branch.

## Sentry

- Web project: `vayu-prana-com` (confirm slug on first session)
- Triage rule: error rate spike > 2x baseline within 1h of a deploy → revert first, investigate second.

## Obsidian vault (durable memory)

- **Topics:** `Topics/Vayu Web.md` (create if missing) — design decisions, deploy gotchas, dependency choices
- **Sessions:** `Sessions/YYYY-MM-DD-web-<topic>.md`
- **Decisions log:** `Company/DECISIONS.md` — read before shipping anything material
- **Blog publishing pipeline:** docs in `Topics/Blog Pipeline.md` (or create one)
- **My daily memory:** `agents/web-developer/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/web-developer/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/web-developer/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1470278378077814804` (`#web-developer`)
- **My Discord identity:** own bot (`bot_token_env: WEB_BOT_TOKEN`)
- **My webhook:** `WEB_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | scope / risk decisions, deploy timing | `1469505325102006490` |
| `ui-ux-designer` | spec ambiguity, design deviation | `1472412741795840120` |
| `ios-developer` (Aria) | universal-link contract, deep-link state | `1470499341763608681` |
| `android-developer` (Ravi) | app-link contract, deep-link state | `1471023591033278484` |
| `backend-developer` | API contract, lead-capture endpoints | `1471890585223954503` |
| `security` | CVE alert, header config, WAF | `1471886526198714449` |
| `marketing` | tone check on outbound copy I'm putting on the site | `1469778689322647800` |
| `media` (Pixel) | OG images, hero assets | `1469500272802926653` |
| `qa` | regression checks across browsers/devices | `1470297479722565647` |
| `project-manager` (Tempo) | task state updates only | `1470690373667127420` |

## Local environment habits

- Node LTS via `mise` / `nvm` (confirm)
- `pnpm` (assumed; confirm on first session)
- Lighthouse CLI / Chrome DevTools for vitals checks
- `vercel` CLI authenticated as Dhruv's account
- Browser test matrix: Chrome (latest), Safari (latest), Firefox (latest), iOS Safari, Chrome Android

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
