---
cron: 0 10 * * 1-5
---
Perform a daily QA sweep and post status to channel.

1. Check Sentry for any new errors or regressions in the last 24 hours. Note error count, top issues, and affected platforms (iOS, Android, web).

2. Scan recent session logs in /Users/celainc/Documents/Vayu/Vayu/OpenClaw/DailyNotes/ for any bug reports or QA notes from the past 24 hours.

3. Check if any deployments happened since the last sweep. If yes, flag for smoke test.

Post a QA status summary:
- New errors (count + top 3)
- Reported bugs from sessions
- Deploy activity
- Overall status: GREEN / YELLOW / RED
