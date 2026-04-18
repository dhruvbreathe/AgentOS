# LEARNINGS.md — What I've Figured Out

_Append-only. Each entry survives session restarts — it gets loaded into my system prompt next time, so I don't re-learn what I already know._

## Format

Use one block per lesson. Keep each entry small and sharp.

```
## YYYY-MM-DD — <short title>
- **Learned:** <the lesson itself, one sentence>
- **Why:** <the incident or evidence that taught it>
- **How to apply:** <when this should change my behaviour next time>
```

## When to write

- After a sharp mistake (so I don't repeat it)
- After a clear success that wasn't obvious up front (so I repeat it)
- When I notice a pattern across 2+ similar situations
- When the operator gives me feedback that applies beyond this moment

## When NOT to write

- For one-off facts (those go in `memory/YYYY-MM-DD.md`)
- For project state (that belongs in the Obsidian vault)
- For secrets — never

## Housekeeping

- If an entry is clearly stale (the world changed), strike through with `~~…~~` and note why. Don't silently delete.
- Every few weeks, look for duplicates and consolidate.
- If the file grows past ~200 lines, promote the most durable lessons to SOUL.md or AGENTS.md and archive the rest to `memory/learnings-YYYY-MM.md`.

---

<!-- learnings:start -->

## 2026-04-15 — Scheduler is launchd, not cron
- **Learned:** macOS `crontab` writes hang in the Claude Code sandbox (TCC privacy gate), so I schedule recurring work via `scheduler/install.py` which uses launchd. The `cron:` frontmatter field is preserved as the authoring interface — the installer translates it to a launchd plist.
- **Why:** Tried to install a daily_scrum cron via `cron/install.py --apply` and every attempt hung indefinitely. Isolated it: read works, write hangs — both stdin and file-arg forms. launchctl bootstrap works cleanly in the same environment. Built `scheduler/install.py` to replace cron.
- **How to apply:** When scheduling a new task, run `python scheduler/install.py` to dry-run, get operator sign-off, then `--apply`. Verify with `--list`. Legacy cron entries still fire until the operator clears the managed block from a real terminal; the installer auto-skips any task whose cron twin is still alive to avoid double-triggers.

<!-- learnings:end -->
