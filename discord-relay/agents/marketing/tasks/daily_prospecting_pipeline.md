---
cron: 0 8 * * *
---
Daily prospecting pipeline — 08:00 Pacific, every day. **Drafts only — Dhruv green-lights before send.**

Goal: 15 fresh high-quality cold-email drafts sitting in #marketing-finance-pr by 08:00. 5 yoga partnerships, 5 health-tech investors/angels/family offices, 5 insurance brokers. Dhruv reviews + replies "send" / "send batch" → I fire the send loop. No autonomous send. Dhruv monitors the replies.

**Style target: YC-style cold email.** Specific, direct, 3–5 sentences, one concrete ask, no flattery-stacking, no em-dash decoration, no "I hope this finds you well", no "circling back", no "excited to". Paul Graham's rule: the email should look like a smart person typing fast.

Steps:

1. **Read the dedup ledger first.** `/Users/celainc/Documents/Vayu/Vayu/CRM/_contacted.md` — no repeat within 90 days. Hard rule.

2. **Scan the inbox for replies — triage + learn BEFORE drafting new.** `himalaya envelope list -a dhruv --folder INBOX --page-size 100`. This is the feedback loop, not just triage. For each real human reply (not auto/OOO/bots) to any prior send:
   - **Surface** in-channel: one-line summary + quoted pull-out + link to thread (ID). Group by tone: interested / pass-soft / pass-hard / meeting-booked / info-request.
   - **Don't reply myself.** Dhruv monitors + replies. Exception: obvious unsubscribe / bounce / auto-reply → log + move on.
   - **Log every reply** to `CRM/_contacted.md` with `status: reply-received`. If Dhruv replied himself, update to `reply-sent-by-dhruv` with a one-line summary of his angle so tomorrow's sourcing knows what positioning is working.
   - **Learn.** Before drafting today's 15, ask: what patterns are the replies showing? Which angle (proof point, subject line, opener) is pulling response — and from which category? Which categories are silent and need a pivot? Bubble findings up into the "deep research" step (step 6) so today's drafts reflect yesterday's signal. If a clear pattern emerges (e.g., "YC top 10% signal works on preventive-health seed funds", "yoga studios don't bite on branded pranayama, do bite on teacher-training content"), append to `LEARNINGS.md` as a dated entry. The reply stream is how this agent gets better over time — don't let it flow past untreated.

3. **Source 5 yoga candidates — high-intent only, deep research per candidate.** Quality over volume. Every candidate must clear all four gates BEFORE Apollo verify. If candidate fails any gate, drop and find another. Target: 5 studio owners / partnerships / teacher-training leads who can actually green-light an integration.

   **Gate 1 — scale fit.** Must be at least one of:
   - 2+ physical locations, or
   - 15+ weekly classes on their public schedule, or
   - Active teacher-training program (200hr / 300hr / 500hr advertised in last 12 months), or
   - 10,000+ Instagram followers with weekly posting cadence

   Drop: single-teacher pop-ups, dormant studios, personal-brand accounts with no programming surface.

   **Gate 2 — integration surface.** Must have somewhere Vayu can actually slot in:
   - Teacher-training curriculum (pranayama module supplement)
   - Corporate wellness / B2B offering
   - Member app or content library
   - Branded workshop series
   - Newsletter with 5K+ subs

   If they have no surface, there's nothing to partner on. Drop.

   **Gate 3 — active in last 90 days.** Recent Instagram posts, new workshops on the calendar, new teacher bios, blog posts. Dormant = drop.

   **Gate 4 — partner-level contact.** Studio owner, founder, partnerships lead, director of TT, or lead teacher trainer. Not the front-desk `info@`, not a junior social manager.

   **Per-candidate research (mandatory before drafting):**
   - **Style / lineage**: Iyengar, Ashtanga, Jivamukti, Kundalini, secular power yoga, etc. Framing changes by lineage — pranayama framing lands differently in a Sivananda studio vs a CorePower.
   - **One recent offering**: a specific upcoming workshop, TT cohort, or corporate partnership announced in the last 60 days. Cite it naturally.
   - **Concrete integration angle**: not "can we partner" — "3-minute pranayama protocol supplement for your 200hr grads" or "co-branded breathwork track in your corporate wellness offering". If I can't name a specific angle, drop.

   **Sourcing paths (in order):**
   - (A) Hand-sourced fresh: YogaAlliance registered studios, Mindbody-listed studios filtered by size, top 100 IG yoga-studio accounts. Find partner contact, verify via `apollo_people_match`.
   - (B) Apollo fresh: `apollo_mixed_people_api_search` with organization filter on specific studio names matching gates 1–3, title filters (Owner, Founder, Director, Partnerships, Teacher Training).
   - (C) Revivals: `CRM/B2B/*.md` with yoga industry + `status: Sent` + `last_contact` >= 90 days + no reply. Only revive if still clears all 4 gates. Re-verify address.

   Every email must come back `email_status: verified`. No LinkedIn pattern-guessing. If Apollo is down, try direct REST via `APOLLO_API_KEY` in `.env`. If both fail, skip yoga today and log the gap.

4. **Source 5 investor candidates — high-intent only, deep research per candidate.** Quality over volume. Every candidate must clear all four gates BEFORE Apollo verify. If candidate fails any gate, drop and find another. Target: 5 partner-level contacts who can write a meaningful chunk of our $2.5M USD seed.

   **Gate 1 — check size fit.** Fund AUM $50M–$500M = writes $500K–$3M checks at seed. Verify from fund website, Crunchbase, or Signal. Exclude:
   - Mega-funds ($1B+) — check sizes too big, we're noise
   - Micro-funds (<$25M) — can't move the round meaningfully
   - Rolling funds / solo GPs unless they've led a seed in our category in last 6 months

   **Gate 2 — active at seed in last 12 months.** Confirm via their announced portfolio additions (not their thesis page). Must have led or co-led at least one seed round in the last year. A fund that "does seed" on their website but hasn't led one in 18 months is not active.

   **Gate 3 — category fit.** Must invest in at least one of:
   - Consumer digital health (Calm, Headspace, Oura-adjacent)
   - Preventive / mental health / stress / sleep
   - Digital therapeutics (FDA-cleared or adjacent)
   - Wearable-adjacent software
   - Women's health / wellness subscription
   - Consumer health subscription (not one-off commerce)

   Exclude: generalist mega-funds, biotech/pharma-pure funds, enterprise-only health IT, pure B2B SaaS, fintech, Web3/crypto. A fund whose last 10 deals are B2B SaaS is wrong even if their deck mentions "health".

   **Gate 4 — partner-level contact.** Find the specific partner who led the category-adjacent deal, not `info@fund.com`, not the analyst, not the ops lead. If the fund has no partner with a clear category-match deal in last 18 months, drop the fund.

   **Per-candidate research (mandatory before drafting — write findings into draft notes, not just the body):**
   - **2 portfolio companies** they backed that rhyme with Vayu: company, round, year, one-line thesis thread to Vayu. "Backed [X] Series Seed 2024 — consumer mental health subscription, same retention-driven model" beats "invests in health".
   - **One recent signal** of current thinking: a 2025–2026 essay, tweet thread, podcast, or LP update if public. Reference the specific thing they said, not "saw your recent post".
   - **Check-size signal**: "typically writes $X–$Y at seed" pulled from announced rounds (Crunchbase / Pitchbook / announcement press). Logged in draft notes even if not cited in body.

   **Sourcing paths (in order):**
   - (A) Hand-sourced fresh: Signal.vc / Crunchbase / fund portfolio pages. Find partner, confirm category + check size, verify via `apollo_people_match`.
   - (B) Apollo fresh: `apollo_mixed_people_api_search` with title filters (Partner, General Partner, Principal) AND organization filter on specific fund names matching gates 1–3. Never use generic "VC" title without fund filter.
   - (C) Revivals: `CRM/Investors/*.md` with `status: Sent` and `last_contact` >= 60 days old, no reply, NOT `Closed Loop / Bounced / Pass`. Only revive if they still clear all 4 gates today. Re-verify via `apollo_people_match`. Exclude Bob Kocher, Halle Tecco, Morgan Cheatham, Tiffany Yu (7wire — explicit pass + summer nurture).

   **Email must include explicit check-size ask.** Don't make them guess. One line like "raising $2.5M USD seed, looking for a lead at $750K–$1.5M" tells them in 10 seconds whether this fits. Self-qualifies fast = fewer soft passes, more real conversations. For funds that write smaller ($250K–$750K) the line shifts to "open to strong participation checks in a $2.5M seed".

5. **Source 5 insurance / broker candidates — high-intent only, deep research per candidate.** Quality over volume. Every candidate must clear all four gates BEFORE Apollo verify. Mix of fresh T1 and T2 follow-ups is fine, but same bar applies to both.

   **Gate 1 — book fit.** Must be a commercial employee-benefits broker or consultant with at least one of:
   - 10+ active group-benefits clients
   - 5,000+ covered lives across book
   - Wellness / mental-health named as a service line on their site
   - Public case study of a benefits program rollout in last 24 months

   Drop: pure P&C, pure personal lines, individual / Medicare-only, pure life-insurance brokers.

   **Gate 2 — group-size fit.** Their typical client should be **100–5,000 employees**. That's the PEPM sweet spot for our $4–8 pricing and our ability to support the rollout. Skip mega-clients (unit economics break) and sub-50-employee shops (no dedicated benefits lead on client side).

   **Gate 3 — mental-health / wellness already in stack (preferred) OR clear interest.** Look for:
   - EAP vendor listed in services
   - Mental-health app or digital-health partnership already promoted
   - Wellness / well-being blog posts or LinkedIn thought leadership in last 90 days
   - Benefits consultant who posts about mental health or preventive care

   A broker with zero wellness surface is a cold pitch of the category itself — drop or deprioritize.

   **Gate 4 — decision-maker contact.** VP / Director Employee Benefits, Benefits Consultant, Wellness Practice Lead, or Principal/Partner on the benefits side. Not junior account manager, not P&C-only contact, not generic `info@`.

   **Per-candidate research (mandatory before drafting):**
   - **Book signal**: a specific industry or client they've publicly named (tech, hospitality, manufacturing, healthcare). Framing changes — a broker whose book is 60% hospitality cares about shift-worker burnout; one in manufacturing cares about safety-related stress.
   - **Recent wellness-adjacent activity**: a workshop they hosted, a LinkedIn post, a case study, or a webinar in last 90 days. Cite the specific thing.
   - **Concrete integration angle**: how Vayu slots into a benefits package. "$4–8 PEPM for groups of 500+, 5-minute daily protocols their employees can do at their desk, peer-reviewed 40% anxiety reduction." Match the angle to their book.

   **Sourcing paths (in order):**
   - (A) T2 follow-ups: `CRM/B2B/*.md` with insurance industry + `status: Sent-T1` + `last_contact` 21–45 days old, still clearing all 4 gates. Re-verify address. T2 framing = "coming back on my X note", not "just making sure this didn't go to spam".
   - (B) Fresh hand-sourced: top broker lists (BenefitsPro, Business Insurance "Largest Brokers"), NABIP member directories, regional BGAs. Confirm gates, verify via `apollo_people_match`.
   - (C) Apollo fresh: `apollo_mixed_people_api_search` with organization filter on specific broker names matching gates 1–3, title filters (Employee Benefits, Benefits Consultant, Wellness, Principal). Never generic "broker" or "agent".

   Every email must come back `email_status: verified`. Same bounce rules as other categories.

6. **Deep research per recipient.** For each of the 15, before drafting:
   - Yoga: what makes THIS studio different (programming, location, teacher lineage, content surface). No generic "your studio is great".
   - Investor: 2 specific portfolio companies + the thesis thread that connects them to Vayu. Don't name-drop; use it to frame the ask.
   - Insurance: what's their book (regional, size, industry focus) and why Vayu would slot into that specific benefits package.
   - One concrete angle per recipient. If I can't find a specific reason this person should care, drop and pick another.

7. **Cross-check stats + signals against `Company/FACTS.md` before drafting.** That's the source of truth. Dhruv's proof points as of 2026-04-20:
   - "50,000+ downloads (iOS + Android)"
   - "64% 30-day retention (3x category average)"
   - "4.8 stars"
   - "peer-reviewed 40% anxiety reduction over 4 weeks"
   - "$8.5K MRR"
   - "Selected into top 10% of YC applicants"
   - "Currently in Next AI accelerator"
   - **Fundraise framing by geo:** US investors → `$2.5M USD seed`. Canadian / other → `$500K pre-seed`. Pick by investor HQ, not mailbox domain.
   Never cite a number not in FACTS.md. Never repeat the same stat block across 15 emails — pick 1–2 proof points per email that match that recipient's specific angle. YC + Next AI signals land hardest with investors and enterprise; less relevant for yoga studios.

8. **Sample Dhruv's voice + YC examples.** `himalaya envelope list -a dhruv --folder "[Gmail]/Sent Mail" --page-size 20` — read one recent yoga-tier + one investor-tier email. Match cadence. YC cold-email references: http://paulgraham.com/cold.html — short, one ask, no filler, direct subject.

9. **Draft 15 emails.** YC-style rules:
   - 3–5 sentences, max 60 words body ideal.
   - Subject line: specific, lowercase or sentence-case, no clickbait, no emojis. Examples: "quick thought for Yoga Pod members", "Vayu — 64% day-30 retention".
   - Opening sentence: the concrete thing. No weather, no flattery.
   - One stat at most per email (the one that matches their angle). Never a stat block.
   - Straight quotes. No em-dashes as decoration. Em-dashes allowed if Dhruv actually uses them.
   - Banned: "circling back", "I hope this finds you well", "excited to", "leverage", "crucial", "just making sure", "actually", "would love to".
   - iOS and Android framing (both live). Never iOS-only.
   - Signatures:
     - Yoga / local partner tier → short: `Dhruv` / `vayu-prana.com`
     - Investor / enterprise tier → full: `Dhruv Adhia` / `CEO & Cofounder, Prana Labs Inc.` / `https://vayu-prana.com` (+ `https://vayu-prana.com/pitch` deck link for investors)
   - Run HUMANIZER final pass on every draft: "what makes this obviously AI?" → rewrite. If I can't name the tell and fix it, the draft isn't done.

10. **Write drafts to vault.** `CRM/_drafts/YYYY-MM-DD-batch.md` with frontmatter: date, status: awaiting-approval, sender: dhruv@vayu-prana.com, count: 15. Permanent record.

11. **Post all 15 drafts to #marketing-finance-pr.** Grouped by category (YOGA / INVESTOR / INSURANCE). Per draft: recipient name + company + email + subject + full body in code fences. Relay auto-splits at 2000 chars. Close the post with: "✉️ **15 drafts ready — reply 'send' or 'send <n>' or 'skip <n>' to green-light.**"

12. **Wait for Dhruv's green signal.** Options Dhruv can reply with:
    - `send` or `send all` → fire the send loop on all 15.
    - `send 1 3 5-7 10` → send only those indices (1-based in the posted order).
    - `skip 2 4` → send everything except those indices.
    - Edit requests → Dhruv pastes a revised body, I swap it in, re-post the updated draft, wait again.
    - Silence → do not send. Drafts expire at 20:00 Pacific. Log as "unshipped" in ledger.

13. **On green signal, send via himalaya.** 20s gaps. From: `Dhruv Adhia <dhruv@vayu-prana.com>`. Append to `CRM/_contacted.md` per send with status `sent` or `FAILED: <error>`. Bounce cap 3 = stop batch, surface in-channel.

14. **Post final tally.** Sent/failed count, any bounces, link to vault drafts file. Signature: ✉️.

Self-regulation rules:
- **Per-draft self-review.** Read each draft back before posting. If it sounds AI, rewrite. If it's generic, rewrite. If I can't find a concrete reason this person should care, drop the candidate and pick another.
- **Domain reputation floor.** If bounce count in any single send hits 3+, stop immediately, surface in-channel, don't resume until I understand why.
- **Reply rate floor.** If weekly report shows any category at <2% over ≥20 sends, pause that category on next cron fire and post a pivot proposal.

Red lines:
- **Never send without green signal.** Dhruv reversed send-autonomy on 2026-04-20. Drafts only.
- **Never send an unverified email.** Apollo `email_status: verified` required.
- **Never repeat a contact within 90 days** unless ledger shows `status: reply-received` and reply invited follow-up.
- **Never fabricate a stat.** `Company/FACTS.md` is the source of truth — re-read it every draft pass.
- **Never reply to inbound on Dhruv's behalf.** Surface → he replies.
- **Never hide a failure.** Every bounce, every send failure, every anomaly goes in the summary post. Honest numbers or nothing.
