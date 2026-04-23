<!--
cron: 0 3 * * 0
-->
# Weekly memory maintenance

Run the weekly distillation pass for me:

1. Run `python {AGENTOS_ROOT}/scripts/maintain_memory.py <my-name> --archive-days 14 --summary-days 7` and read its output.
2. For each pattern you notice across the 7-day summary that's worth keeping forever:
   - Add a new block to my `LEARNINGS.md` (Learned / Why / How to apply)
3. If any lesson is so durable it belongs in `SOUL.md` or `AGENTS.md`, promote it there (don't duplicate).
4. If my `LEARNINGS.md` has grown past ~200 lines, archive the oldest entries to `memory/learnings-<YYYY-MM>.md` in my workspace.
5. Post a one-line summary of what you distilled to my Discord channel. Nothing if there was nothing worth surfacing.

No noise. Quiet is a valid report.

---

**To enable:** copy this file into `agents/<my-name>/tasks/memory_maintenance.md`, uncomment the cron frontmatter line, dry-run `python cron/install.py`, get operator approval, then `--apply`.
