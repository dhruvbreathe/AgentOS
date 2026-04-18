---
cron: 0 9 * * *
---
Produce the daily product analytics report by pulling from Mixpanel, PostHog, and Supabase **via their direct APIs** (not MCP — MCP has been unreliable).

Pull yesterday's (last 24h) metrics:

## 1. Mixpanel (primary analytics)

Use the Mixpanel Query API directly via `curl` or `python -m requests`:
- Base URL: `https://mixpanel.com/api/2.0/`
- Auth: Service Account (username:secret via Basic Auth)
- Required env vars: `MIXPANEL_SERVICE_ACCOUNT`, `MIXPANEL_SECRET`, `MIXPANEL_PROJECT_ID`
- If env vars missing, note "Mixpanel credentials not configured" and ask backend-developer

Pull:
- DAU and total sessions (JQL or Segmentation endpoint)
- Onboarding funnel completion rate
- Top 5 events by volume
- Day-2 retention

## 2. PostHog (web analytics)

Use the PostHog API via `curl`:
- Base URL: `https://us.i.posthog.com/api/` (or app.posthog.com depending on region)
- Auth: Personal API key via Bearer token
- Required env var: `POSTHOG_API_KEY`, `POSTHOG_PROJECT_ID`
- If env vars missing, note "PostHog credentials not configured"

Pull:
- Website visitors (unique sessions)
- Conversion events (downloads, signups from web)
- Top referrers

## 3. Supabase (source of truth)

Use the Supabase Postgres REST API or psql:
- Project ref: `yakibuftxtsvqnwnermi` (prod)
- Required env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- If env vars missing, route to backend-developer

Pull:
- New users signed up (last 24h)
- Subscription events (trial starts, conversions, cancellations)
- Revenue from `revenue_events` or equivalent
- Any auth/login failures

## Output format

📊 **Yesterday's numbers** — DAU, signups, paid conversions (2-4 bullets with real numbers)
📈 **Week-over-week** — retention, engagement deltas
⚠️ **Anomalies** — errors, drops, spikes worth flagging
🎯 **Today's watch items** — what to keep an eye on today

Keep under 1500 characters. Close with 💨.

**Rules:**
- If a data source fails, note it explicitly. Don't fabricate numbers.
- If any env var is missing, post a short note asking for it rather than silently skipping.
