---
cron: 0 9 * * *
---
Daily Sentry check for the Android project. Run at 9am Pacific.

Pull recent issues from Sentry and post a short digest to my Discord channel.

Steps:
1. Query Sentry for last 24h issues:
   ```
   curl -s -H "Authorization: Bearer $SENTRY_AUTH_TOKEN" \
     "https://sentry.io/api/0/projects/$SENTRY_ORG/$SENTRY_PROJECT/issues/?statsPeriod=24h&limit=20"
   ```
2. Also pull the ongoing backlog (statsPeriod=14d, sort=freq) to keep an eye on high-volume unresolved issues.
3. Post a caveman-mode digest:
   - 📊 opening line — total new events in 24h, total users affected
   - 🔥 any issue with count >= 5 or userCount >= 3 in 24h — call it out by shortId + title + culprit
   - ⚠️ any new fatal-level issue (firstSeen in last 24h) — list with shortId + one-line cause
   - Ongoing top-3 by 14d frequency — just shortId + count
   - Close with 🤖 if digest has weight, skip signature if it's a quiet day

Keep it tight. If nothing new and backlog unchanged: one line — "📊 **Sentry quiet.** No new events 24h, backlog stable. 🤖" and done.

Flag backend/HTTP errors to backend-developer channel via send_to_agent only if volume spiked (>10 new events in 24h). Otherwise just note in the digest.
