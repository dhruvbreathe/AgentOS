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

## 2026-04-21 — Check integrations list before saying "not connected"
- **Learned:** Before answering "am I connected to X?", grep my own system prompt for the service name. Integrations are listed in the "Integrations available via HTTP API (env-key)" block with exact env vars + example curls.
- **Why:** Dhruv asked twice if I was connected to Sentry. I said no both times. Wrong — `$SENTRY_AUTH_TOKEN`, `$SENTRY_ORG`, `$SENTRY_PROJECT` are in my prompt. A daily_sentry_digest cron I'd already scheduled fired and confirmed the connection works. Made me look sloppy.
- **How to apply:** On any "do I have X?" question, scan my own prompt first (INTEGRATIONS.md + the shared HTTP-API integrations block). Only say "no" after that's confirmed empty.

<!-- learnings:end -->
