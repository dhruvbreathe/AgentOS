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

## 2026-04-19 — Trust the successful output, not the stale log tail
- **Learned:** When diagnosing a scheduled-job failure, read the Discord post the user just saw FIRST, then the log. If the post contains real content, the job succeeded — any failure text above it is either (a) a duplicate run from a second scheduler firing the same slot, or (b) old history I'm misreading.
- **Why:** Three `Not logged in` posts fired this morning from legacy cron. I force-migrated to launchd. At 18:00 a full working `daily_prep` digest posted alongside a "Not logged in" from the still-active legacy cron. I ignored the working post, diagnosed off the failure tail alone, and publicly corrected myself (wrongly) to say "launchd is also broken." It wasn't. The migration had already fixed it; the legacy cron was just double-firing noise.
- **How to apply:** When two concurrent schedulers could be firing the same slot: (1) check for the SUCCESS post in Discord before writing the verdict, (2) grep the log for the full event sequence around a timestamp, not just `tail`, (3) if both a success and a failure landed at the same minute, that IS the diagnosis — double-fire, not auth breakage. Don't whiplash the operator twice in five minutes on the same issue.

## 2026-04-18 — Secrets pasted in Discord are already burned
- **Learned:** When the operator pastes a raw API key/secret into Discord chat, the secret is already leaked — it's in Discord history, my trajectory JSONL, and any channel-scraping tooling. Refusing to write it to `.env` doesn't undo the leak.
- **Why:** Dhruv pasted a Mixpanel service account secret in #virtual-ceo-cto-dhruv. I initially refused and told him to rotate. He re-approved the write. The secret worked, but rotation is still needed.
- **How to apply:** (1) Accept the write if the operator insists — the damage is already done. (2) Always flag the leak explicitly in the first reply and push for rotation. (3) Tag `.env` with a comment next to any Discord-leaked key (e.g. `# ROTATE, leaked in Discord YYYY-MM-DD`) so rotation doesn't get forgotten. (4) Proactively tell operators up-front when they're about to share creds: **"paste into `.env` yourself, not Discord — then touch `logs/.restart-requested`."** The rule: creds-to-disk, never creds-to-chat.

<!-- learnings:end -->
