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

## 2026-04-18 — Apollo-verified is a hard requirement, not a preference
- **Learned:** Every queued email must come back `email_status: verified` from Apollo. No LinkedIn guesses, no `firstname@company.com` patterns, no ⚠️ flagged sends.
- **Why:** Apr 16 batch had 4 yoga sends on pattern-guessed addresses. Dhruv explicitly ruled that out on Apr 18: "make sure those emails exist from apollo". Bounces damage sender reputation on `vayu-prana.com` and show up as zero replies for reasons unrelated to campaign quality.
- **How to apply:** If Apollo returns no verified address for a candidate, drop them — never substitute a guess. If Apollo MCP is down for a batch, post a name-only shortlist and ask Dhruv how to proceed. Re-verify any CRM-sourced address (revivals, T2s) before queueing — addresses on file may be stale.

## 2026-04-19 — Send autonomy granted; escalation gate is the shape of trust
- **Learned:** Dhruv delegated send authority for outreach on 2026-04-19: "please dont ask me from now on, you send email with proper research and body and signature, you check my emails and loop to make sure we have more and more replies". Daily 9 AM batch sends without approval. Inbox replies I can handle alone get handled. But the trust has a boundary — anything binding (pricing, contracts, equity, intros to third parties, calendar holds) still escalates.
- **Why:** Approval-per-email was the bottleneck limiting outreach velocity. Dhruv trusts the shape of the pipeline (Apollo-verified + voice-sampled + humanizer pass + red lines) enough to let it run. But he still owns the business decisions that ride on replies.
- **How to apply:** (1) The cron sends autonomously. (2) For each inbound reply: if the answer is already in `Company/FACTS.md` / `STRATEGY.md` / the deck, I reply in Dhruv's voice. If it's a commitment, intro request, pricing, terms, or scheduling — escalate to #marketing-finance-pr with a one-line flag, no reply sent. (3) Every send + reply goes in `CRM/_contacted.md` with status so Dhruv can audit. (4) Weekly report holds me accountable — if reply rate dips below 2% in any category, I pause and propose a pivot, not more volume.

## 2026-04-20 — Apollo is the send channel, not Gmail drafts
- **Learned:** Outreach goes out through Apollo emailer campaigns (enroll contact id into sequence → Apollo sends from the connected mailbox on its schedule). Gmail `create_draft` is for one-off inbound replies, not the daily batch.
- **Why:** I was modeling the pipeline as "draft in Gmail → Dhruv sends". Dhruv corrected: Apollo API is wired, sends happen via sequence enrollment, not drafts.
- **How to apply:** Daily batch flow = `apollo_mixed_people_api_search` → `apollo_people_match` to verify (`email_status: verified`) → `apollo_contacts_create/update` → `apollo_emailer_campaigns_add_contact_ids`. Log campaign id + contact id in `CRM/_contacted.md`. Use Gmail `create_draft` only when a conversation has moved out of the sequence into a 1:1 reply.

## 2026-04-20 — Mechanical slop = Dhruv killed batch mid-flight
- **Learned:** Copy-pasting the same 4-stat block across 15 emails + missing our own platform reality (wrote iOS-only when Android is live) = obvious AI slop. Dhruv killed the batch after 2/15 had already sent.
- **Why:** v1 batch had "27,500 downloads, 64% 30-day retention, 4.8 stars, peer-reviewed 40% anxiety reduction" in nearly every email verbatim. Also framed Vayu as iOS-only when Android shipped. Dhruv: "It's feels very mechanical and also we are on android as well, apply humanizer skill while writing email".
- **How to apply:** (1) Never repeat the same proof-point block across a batch — pick 1–2 stats per email that match that recipient's specific angle. (2) Vary openers, asks, and sentence structure per email; don't template. (3) Always say "iOS and Android" — both platforms are live. (4) Run HUMANIZER final pass on every draft before any send: "what makes this obviously AI?" → rewrite. (5) If I can't find a concrete reason this person should care, drop the candidate — don't fill the slot with a generic email.

## 2026-04-20 — Stats go stale; FACTS.md is the source of truth
- **Learned:** Using "27,500 downloads" in today's batch was already stale — real number is 50,000+ (iOS + Android). Hardcoded proof-point numbers in my task prompts and LEARNINGS drift against reality.
- **Why:** Dhruv corrected after the v2 batch sent: "We have over 50k downloads at this point". I had 27,500 baked into cron prompt and most drafts.
- **How to apply:** (1) `Company/FACTS.md` is the source of truth for every stat I cite. Re-read it at draft time, every time. (2) If a number isn't in FACTS.md, don't cite it. Ask or drop the line. (3) When I learn a new number, update FACTS.md inline with a date stamp — don't just change one draft.

## 2026-04-20 — Send-autonomy reversed: draft-first, green-signal, Dhruv owns replies
- **Learned:** Dhruv reversed the 2026-04-19 autonomy grant after today's v1 mechanical-slop batch. New flow: 08:00 daily, 15 drafts ready + posted to #marketing-finance-pr, wait for green signal, then send. Dhruv monitors inbound replies himself.
- **Why:** Autonomy + weak humanizer pass = mechanical batch that Dhruv had to kill mid-flight. Trust has to be re-earned on quality before I get send authority back.
- **How to apply:** (1) Cron time moved to `0 8 * * *` (daily, including weekends). (2) Drafts posted to Discord with `status: awaiting-approval`. (3) Valid green signals: `send` / `send all` / `send 1 3 5-7` / `skip 2 4`. (4) Silence = don't send; drafts expire 20:00 Pacific. (5) Inbound replies: surface them, do NOT reply on Dhruv's behalf. Auto-replies/bounces/unsubs = I log and move on.

## 2026-04-21 — Investor list quality > volume; gate on check-size + category + partner
- **Learned:** 2 replies on ~28 sends over 5 days validated the engine, but both were pass-soft (Tiffany: $4–7M Series A, wrong stage; other: out-of-category). Dhruv: "we want high intent leads, in case of investors it should be about the cheques they can write... and also the category wise. This deep research part is important." Quality of the investor list, not volume, drives conversion.
- **Why:** A pass from a $4–7M Series A fund is a soft signal we sent to the wrong filter, not that the pitch is bad. 5 wrong-stage investors a day wastes 25 emails a week on people who can't write our check size.
- **How to apply:** Investor sourcing gates on 4 hard filters before Apollo verify — (1) Fund AUM $50M–$500M, (2) led/co-led seed in last 12 months, (3) category fit (consumer digital health / preventive mental health / digital therapeutics / wearable-adjacent / women's wellness / health subscription), (4) partner-level contact who led the category-adjacent deal, never generic info@ or analyst. Per-candidate research must include 2 portfolio companies with thesis thread, one recent 2025–2026 signal, and check-size range. Email states "$2.5M USD seed, looking for lead at $750K–$1.5M" so they self-qualify fast.

## 2026-04-21 — 4-gate + deep-research applied to all stakeholder categories
- **Learned:** Dhruv: "and do this for all other stake holders as well." Same quality-over-volume bar applies to yoga + insurance, not just investors. Generic "yoga studio" or "insurance broker" targeting wastes sends the same way generic "health-tech VC" does.
- **Why:** A tiny pop-up studio with no programming surface can't integrate Vayu. A P&C-only broker has no benefits book to sell into. Sending to candidates who literally cannot transact wastes impressions and drags down reply rate per category.
- **How to apply:** Every category now gates on 4 hard filters + per-candidate research before Apollo verify:
  - **Yoga**: scale (2+ locations / 15+ weekly classes / active TT / 10K+ IG), integration surface (TT / corporate wellness / member app / branded workshops / newsletter), active last 90 days, partner-level contact (owner / partnerships / TT director). Per-candidate: style/lineage, one recent offering, concrete integration angle.
  - **Insurance**: book fit (10+ groups or 5K+ covered lives, wellness service line), group size 100–5K employees, mental-health already in stack (preferred), decision-maker (VP/Director Benefits / Benefits Consultant / Wellness Lead). Per-candidate: industry book signal, recent wellness-adjacent activity, concrete integration angle matched to book.
  - **Investors** (already applied earlier today): AUM $50M–$500M, active at seed last 12mo, category fit, partner-level. Per-candidate: 2 portfolio rhymes, recent thinking signal, explicit check-size self-qualify line.
  - Future tracks (BC govt, PR) get the same 4-gate + deep-research structure.

## 2026-04-21 — Second inbox sweep at 16:00 keeps learning loop tight
- **Learned:** Single 08:00 sweep means same-day replies don't feed tomorrow's drafts until 24h later. Added 16:00 Pacific afternoon sweep — read-only, classifies by tone, logs, surfaces only if new replies landed. Silent if empty.
- **Why:** Dhruv: "keep going through my emails every once in awhile". Feedback loop is only as tight as scan frequency.
- **How to apply:** Two cron fires — 08:00 drafts (full pipeline) + 16:00 inbox watch (read-only, learn, log). Afternoon sweep never drafts or sends.

<!-- learnings:end -->
