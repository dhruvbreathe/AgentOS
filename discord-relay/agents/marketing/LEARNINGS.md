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

## 2026-04-16 — CRM is a sent-log, not a lead list
- **Learned:** The `CRM/B2B/` and `CRM/Investors/` folders are "everyone Dhruv has already emailed", not "everyone available to email". Almost every file has a `last_contact` and `status: Sent|Sent-T1|Closed Loop|Bounced`.
- **Why:** My first shortlist picked 10 names from CRM thinking they were "untouched". All 10 had been contacted Feb–Mar. I had to re-pivot mid-task.
- **How to apply:** Before proposing any cold-touch candidate from the vault, grep the file's frontmatter for `last_contact` and `status`. Only `status: prospect` or empty status = truly fresh. Everything else is revival territory and needs different framing.

## 2026-04-16 — Sample Dhruv's voice before drafting
- **Learned:** First-pass drafts will sound like AI. Reading 2 of Dhruv's actual recent sent emails (one B2B, one investor) before drafting cuts the humanizer-pass workload by half.
- **Why:** v1 drafts had "circling back", formulaic triples, em-dash decoration, "happy to send a short deck" — all AI tells. v2 after voice-sampling was noticeably closer to Dhruv's actual cadence.
- **How to apply:** Every daily batch, before drafting, run `himalaya envelope list -a dhruv --folder "[Gmail]/Sent Mail"` and read one B2B-tier + one investor-tier email. Match subject-line style, proof-point phrasing (Dhruv uses "3x category average", "27,500 downloads", "4.8 stars", "near-zero CAC" — NOT "10x median"), and signature tier.

## 2026-04-16 — Signature tiers by audience
- **Learned:** Dhruv uses two signature styles. Yoga / local-partner tier gets short (`Dhruv` + `vayu-prana.com`). Investor / enterprise tier gets full (`Dhruv Adhia` / `CEO & Cofounder, Prana Labs Inc.` / URL + deck link).
- **Why:** Sampling One Yoga email vs Sungkwon Kang (LG Tech Ventures) email showed a clear split.
- **How to apply:** When drafting, match signature to recipient tier. Cold yoga outreach = short warm. Investor / insurance-broker / enterprise = full. Don't mix.

## 2026-04-16 — Daily 15/day needs a sourcing engine, not the vault
- **Learned:** A sustainable 15 fresh prospects/day can't come from the vault alone — the vault is historical. It needs Apollo (when stable) or structured web research per batch.
- **Why:** Option A (revivals + yoga fresh + insurance T2) works for today but exhausts revival-eligible names within a week or two.
- **How to apply:** When Apollo comes back online, the cron task prompt should prefer Apollo enrichment over CRM revivals. Keep the revival path as fallback only.

<!-- learnings:end -->
