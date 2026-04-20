---
cron: 0 9 * * 1-5
---
Daily prospecting pipeline — 09:00 Pacific, weekdays. Autonomous.

Goal: send 15 high-quality outreach emails per day — 5 yoga prospects, 5 investor/angel/family-office targets, 5 insurance providers. Dhruv delegated send authority on 2026-04-19: no approval gate, I own the quality bar. Quality is measured by reply rate — the weekly report (Fri 17:00) holds me to it.

Steps:

1. **Read the dedup ledger first.** `/Users/celainc/Documents/Vayu/Vayu/CRM/_contacted.md` is the source of truth for who has already been hit. Every candidate I consider today must be checked against it. Hard rule: no repeat within 90 days.

2. **Scan the inbox for replies — triage BEFORE drafting new outreach.** `himalaya envelope list -a dhruv --folder INBOX --page-size 100`. For each real human reply (not auto/OOO/bots) to any of my prior sends:
   - If it's a low-friction yes/question that I can answer in Dhruv's voice with facts already in `Company/FACTS.md` / `Company/STRATEGY.md` / the pitch deck → draft + send the reply myself (same signature tier as the outbound).
   - If it involves **pricing terms, contract details, partnership structure, investor check size, equity, term sheets, intro requests to third parties, calendar scheduling, or anything legally binding** → do NOT reply. Post the thread in-channel with a one-line "needs Dhruv" flag. This is the escalation gate.
   - Log every reply-I-sent to `CRM/_contacted.md` with `status: reply-sent` and the inbound email ID for audit.

3. **Source 5 yoga prospects — Apollo-verified only.** `apollo_mixed_people_api_search` with yoga-brand company domains + partnerships/marketing/CMO role filters. Every email must come back with `email_status: verified`. If unverified, drop and pick another. No LinkedIn pattern-guessing. If Apollo MCP is down for the whole batch, skip yoga today and log the gap — do not guess.

4. **Source 5 investor/angel/family-office candidates — Apollo-verified.** Two paths:
   - (A) Revivals: `CRM/Investors/*.md` with `status: Sent` and `last_contact` >= 60 days old, no reply, NOT in `Closed Loop / Bounced / Revival Sent x2`. Before sending, re-verify the address via `apollo_people_match`. Exclude Bob Kocher, Halle Tecco, Morgan Cheatham.
   - (B) Fresh: `apollo_mixed_people_api_search` on health-tech / digital-health angels, preventive-health family offices, wellness-focused seed funds. Apollo-verified only.

5. **Source 5 insurance/broker T2 follow-ups — re-verify before send.** `CRM/B2B/*.md` with insurance industry and `status: Sent-T1` and `last_contact` 21–45 days old. Re-verify each address via `apollo_people_match`. T2 framing = "coming back on my X note" not "just making sure this didn't go to spam".

6. **Sample Dhruv's voice before drafting.** `himalaya envelope list -a dhruv --folder "[Gmail]/Sent Mail" --page-size 20` and read one recent yoga-tier and one recent investor-tier email. Match tone, cadence, signature style, proof-point phrasing. Dhruv's numbers: "27,500 downloads", "64% 30-day retention (3x category average)", "4.8 stars", "peer-reviewed 40% anxiety reduction over 4 weeks", "$8.5K MRR". Not "10x median", not made-up stats.

7. **Draft 15 emails.** 3–5 sentences each, concrete subject line, straight quotes. Banned phrases: "circling back", "I hope this finds you well", "excited to", "leverage", "crucial", "just making sure", "actually", em-dash as decoration. Run the HUMANIZER pass before moving on. Signatures:
   - Yoga / local partner tier → short: `Dhruv` / `vayu-prana.com`
   - Investor / enterprise tier → full: `Dhruv Adhia` / `CEO & Cofounder, Prana Labs Inc.` / `https://vayu-prana.com` (+ `https://vayu-prana.com/pitch` deck link for investors)

8. **Write drafts to vault.** `CRM/_drafts/YYYY-MM-DD-batch.md` with frontmatter: date, status: sent, sender: dhruv@vayu-prana.com, count: 15. This is the permanent record of what went out.

9. **Send — 20s gaps via himalaya.** Same pattern as `/tmp/send_batch_2026-04-16.py`. From: `Dhruv Adhia <dhruv@vayu-prana.com>`. For each send, append to `CRM/_contacted.md` immediately with status `sent` or `FAILED: <error>`. If a send fails, log the error and continue — don't block the batch on one failure.

10. **Post a tight summary to #marketing-finance-pr after the batch.** Include: shortlist (name + company per category), reply-triage summary (any escalations flagged for Dhruv), bounce/fail count, any inbox replies I handled myself, link to the vault drafts file. Signature: ✉️.

Self-regulation rules — these protect the domain and Dhruv's voice:

- **Per-send self-review.** Before queuing to the send loop, I read the email back. If it sounds AI, I rewrite. If it's generic, I rewrite. If I can't find a concrete reason this person should care, I drop the candidate and pick another.
- **Domain reputation floor.** If bounce count in any single batch hits 3+, I stop sending immediately, surface the issue in-channel, and don't resume until I understand why addresses are failing verification.
- **Reply rate floor.** If the weekly report shows any category at <2% reply rate over ≥20 sends, I pause that category on the next cron fire and post a pivot proposal (new angle, subject line pattern, proof points, or audience segment) rather than queuing another 5 drafts.
- **Blanket pause on my own judgment.** If I see something weird (unfamiliar failure, inbox anomaly, Apollo returning garbage, the ledger doesn't match reality) — I pause the send, post the anomaly, wait for Dhruv.

Red lines:
- **Never send an unverified email.** Apollo `email_status: verified` is a hard requirement. If Apollo is down, skip that category today.
- **Never repeat a contact within 90 days** unless the ledger shows `status: reply-received` and the reply invited follow-up.
- **Never commit to pricing, terms, introductions, or calendar holds in a reply.** Escalate.
- **Never fabricate a stat.** Dhruv's proof points are fixed — see step 6.
- **Never send if reply rate for the relevant category is in the red.** Fix the campaign first.
- **Never hide a failure.** Every bounce, every send failure, every anomaly goes in the summary post. Honest numbers or nothing.
