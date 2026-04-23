# MEMORY.md — Curated Facts I Keep Across Sessions

_My personal long-term memory. Facts about people, places, projects, preferences, history. Loaded into my system prompt every session._

## What goes here (vs LEARNINGS.md vs memory/)

- **`memory/YYYY-MM-DD.md`** — raw daily journal. Everything that happened today. Eventually archived.
- **`LEARNINGS.md`** — behavioural rules. "If X, do Y." Things I want future-me to *do differently*.
- **`MEMORY.md`** (this file) — **facts and relationships**. People, preferences, context. Things I want future-me to *know*.

Example of the difference:

| In LEARNINGS.md | In MEMORY.md |
|---|---|
| "Always draft emails before sending — operator wants approval first." | "Operator's primary email is <email>." |
| "When the operator says 'ship it', don't ask twice." | "Operator's name is <Name>. Role: <role>." |
| "For long replies, link to a vault note instead of flooding Discord." | "Operator's writing preference: concise, metric-forward, dry humor." |

## Structure

Use H2 sections grouped by domain. Keep each entry one-line, factual, timestamped when useful.

```
## People
- <Name> — <role>, <org>. Discord id <numeric id>.

## Product
- <Product> is a <short description>. Pricing as of <date>.

## Integrations I actually use today
- Vault mounted as cwd.
- Discord inbound+outbound via bot + webhook.

## Preferences I've observed
- Operator prefers short Discord replies (under ~500 chars unless I genuinely need length).
- Operator dislikes: long headers, AI-style hedging.
```

## Rules for writing here

1. **Facts only.** If it's a rule or pattern, it belongs in LEARNINGS.md.
2. **Timestamp meaningful entries.** `as of 2026-04-16` lets me know when to re-verify.
3. **Update in place when facts change.** Don't keep history here — historical context lives in vault Sessions/.
4. **Never secrets.** Tokens, API keys, passwords never touch this file.
5. **Prune ruthlessly.** If it's not useful when I re-read it in a month, delete it. Short MEMORY beats bloated MEMORY.
6. **Cross-agent facts go to `Company/FACTS.md` in the vault**, not here. MEMORY.md is *my* curated memory; FACTS.md is team-wide.

## Maintenance

- I update MEMORY.md when I learn a durable fact the operator shouldn't need to re-tell me.
- During the weekly `maintain_memory.py` pass, I scan `memory/YYYY-MM-DD.md` entries for durable facts I should promote here.
- Quarterly: reread the whole file, prune stale entries, tighten.

---

<!-- memory:start -->

<!-- memory:end -->
