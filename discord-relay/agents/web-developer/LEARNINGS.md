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

## 2026-04-16 — Never merge on an ambiguous "go"
- **Learned:** "Just forward with PR" / "proceed" / "go ahead" can mean *open the PR* OR *merge the PR*. If the verb is ambiguous and the action is irreversible (merge → production), I must ask before acting.
- **Why:** Read "Just forward with PR" as "merge the PR" and squash-merged #8 into main. Dhruv meant "open the PR and stop." Had to open a revert PR (#9) and queue a re-review, which thrashed the deploy pipeline and wasted his time.
- **How to apply:** For merge / deploy / send-external / commit-to-main actions, the operator's green light must be unambiguous. "Merge it" / "ship it" / "deploy" are clear. "Forward," "go," "proceed," "continue," "do it" after a multi-option message → ask which option. Default to the less destructive read when in doubt, and check.

<!-- learnings:end -->
