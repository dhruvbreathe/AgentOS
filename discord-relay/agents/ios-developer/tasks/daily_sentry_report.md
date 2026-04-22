---
cron: 0 9 * * *
---
Daily Sentry triage for the iOS project. Run at 9am Pacific.

Pull recent activity from Sentry and post a tight report to my channel:

1. Hit `https://sentry.io/api/0/projects/$SENTRY_ORG/$SENTRY_PROJECT/issues/?statsPeriod=24h&limit=20` with `Authorization: Bearer $SENTRY_AUTH_TOKEN` — list new/unresolved issues in the last 24h.
2. Hit `https://sentry.io/api/0/projects/$SENTRY_ORG/$SENTRY_PROJECT/stats/?stat=received&resolution=1d&since=$(date -v-7d +%s)` for the 7-day event count trend.
3. Post a report with:
   - 🍎 signal line: issue count + 24h event total
   - Top offenders (level, shortId, count, users affected, title, culprit) — max 5
   - 7-day trend as a one-line sparkline of received counts
   - ⚠️ callout if any new issue has `userCount >= 5` or `level == fatal`
   - Close with 🍎

If zero issues and zero events: one-liner `🍎 Sentry iOS clean — 0 issues, 0 events 24h.` + 7-day trend. Don't pad.

If the API returns an error (auth failure, 5xx), post `⚠️ Sentry API unreachable: <status>` and stop. Don't invent data.
