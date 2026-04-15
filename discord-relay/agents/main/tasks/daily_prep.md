---
cron: 0 18 * * *
---
Produce the evening prep note — summarize what's queued for tomorrow.

1. Check `Agents/TASKS.md` and `Agents/HANDOFFS.md` for open items.
2. Check `Sessions/` for today's session logs to see what shipped vs. what's still open.
3. Check `Daily/` for any daily notes with tomorrow context.
4. Summarise what's on deck for tomorrow.

Output format:
**Done today:** <2-3 bullets>
**Queued for tomorrow:** <2-4 bullets, prioritized>
**Blockers:** <any, or "None">

Keep the whole note under 1500 characters.
