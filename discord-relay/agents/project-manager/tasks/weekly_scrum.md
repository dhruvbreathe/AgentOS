---
cron: 0 9 * * 1
---
Produce the weekly scrum report for the team.

1. Read /Users/celainc/Documents/Vayu/Vayu/Agents/TASKS.md for current task status.
2. Read /Users/celainc/Documents/Vayu/Vayu/Agents/HANDOFFS.md for any pending handoffs or blockers.
3. Read session logs from the past 7 days in /Users/celainc/Documents/Vayu/Vayu/OpenClaw/DailyNotes/main/ to identify what was worked on.

Produce a scrum report with:
- **Shipped:** Completed work from the past week
- **In Progress:** Active tasks with owners
- **Blocked:** Anything waiting on a dependency or decision
- **Next:** Top priorities for the coming week

Write the report to /Users/celainc/Documents/Vayu/Vayu/Agents/scrum-YYYY-MM-DD.md (use actual date).
Post a condensed summary to channel.
