---
cron: 0 9 * * *
---
Daily Sentry web triage — 9am Pacific.

Fetch the last 24h of issues for the `javascript-nextjs` project in the `prana-labs` org:

```bash
curl -s -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
  "https://sentry.io/api/0/projects/$SENTRY_ORG/$SENTRY_PROJECT/issues/?statsPeriod=24h&limit=20"
```

Post a tight summary to my Discord channel:

- Opening signal line with issue count + severity
- Per-issue bullet: `shortId`, title (trimmed), `count`x / `userCount`u, last seen
- One-line read on what's noise vs real signal
- Next step only if something genuine needs action
- Sign off with 🌐

If zero issues: one-line "quiet 24h 🌐" and stop.

If a fingerprint's `count` spiked >2x vs yesterday or `userCount` >0: flag as 🔥 and recommend pulling the breadcrumb trail.

Keep it under ~600 chars. This is a pulse check, not a deep dive.
