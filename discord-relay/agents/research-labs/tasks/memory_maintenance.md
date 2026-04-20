---
cron: 0 3 * * 0
kind: systemEvent
---
# Weekly memory maintenance (silent)

Run the weekly distillation pass:

1. Run `python /Users/celainc/Developers/ClaudeAgentSDK/discord-relay/scripts/maintain_memory.py research-labs --archive-days 14 --summary-days 7` and read its output.
2. For each pattern worth keeping forever, add a block to my `LEARNINGS.md` (Learned / Why / How to apply).
3. If any lesson belongs in `SOUL.md` or `AGENTS.md`, promote it there (don't duplicate).
4. If `LEARNINGS.md` has grown past ~200 lines, archive oldest entries to `memory/learnings-<YYYY-MM>.md`.
5. This is a systemEvent — no webhook post. Log to trajectory, stay silent. Quiet is a valid report.
