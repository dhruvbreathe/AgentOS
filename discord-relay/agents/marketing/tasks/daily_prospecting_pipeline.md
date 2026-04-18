---
cron: 0 9 * * 1-5
---
Daily prospecting pipeline — 09:00 Pacific, weekdays.

Goal: produce 15 approval-ready email drafts for Dhruv every morning — 5 yoga prospects, 5 investor/angel/family-office targets, 5 insurance providers. Do NOT send. Draft → post to #marketing-finance-pr → wait for Dhruv's "send all" / "send except X" / specific edits.

Steps:

1. **Read the dedup ledger first.** `/Users/celainc/Documents/Vayu/Vayu/CRM/_contacted.md` is the source of truth for who has already been hit. Every candidate I consider today must be checked against it. Hard rule: no repeat within 90 days unless Dhruv overrides.

2. **Scan the inbox for replies.** `himalaya envelope list -a dhruv --folder INBOX --page-size 100`. Filter for Re: / RE: on subjects matching outreach templates. Surface real replies (not auto/OOO/bots) in the drafts post so Dhruv can prioritise responding.

3. **Source 5 yoga prospects.** Prefer fresh names not in `CRM/B2B/` (the B2B folder is effectively a sent-log). If web-research yields pattern-guessed emails, flag with ⚠️ in the drafts post and note that Dhruv may want to verify on LinkedIn before send. If Apollo MCP is stable, prefer Apollo-verified emails.

4. **Source 5 investor/angel/family-office candidates.** Two paths:
   - (A) Revivals: `CRM/Investors/*.md` with `status: Sent` and `last_contact` >= 60 days old, no reply recorded, and NOT in `status: Closed Loop / Bounced / Revival Sent x2`. Exclude Bob Kocher (closed loop), Halle Tecco (bounced), Morgan Cheatham (saturated).
   - (B) Fresh: if Apollo is live, pull untouched health-tech angels or family offices.

5. **Source 5 insurance/broker T2 follow-ups.** `CRM/B2B/*.md` with insurance-related industry and `status: Sent-T1` and `last_contact` 21–45 days old. T2 framing = "coming back on my X note" not "just making sure this didn't go to spam".

6. **Sample Dhruv's voice before drafting.** `himalaya envelope list -a dhruv --folder "[Gmail]/Sent Mail" --page-size 20` and read one recent yoga-tier and one recent investor-tier email. Match tone, signature style, proof-point phrasing.

7. **Draft 15 emails.** 3–5 sentences each, subject line concrete, straight quotes, no em-dash decoration, no "circling back" / "I hope this finds you well" / "excited to" / "leverage" / "crucial". Run the HUMANIZER pass before posting. Signatures:
   - Yoga / local partner tier → short: `Dhruv` + `vayu-prana.com`
   - Investor / enterprise tier → full: `Dhruv Adhia` / `CEO & Cofounder, Prana Labs Inc.` / `https://vayu-prana.com` (add deck link for investors)

8. **Write drafts to vault.** `CRM/_drafts/YYYY-MM-DD-batch.md` with frontmatter: date, status: awaiting-approval, sender: dhruv@vayu-prana.com, count: 15.

9. **Post a tight summary to Discord** with shortlist + sample drafts (one per category) + link to the vault drafts file + the four approval cues ("send all" / "send except X" / "hold yoga" / specific edits). The full 15 emails should be posted inline only if Dhruv explicitly asks ("share all the emails here"). Default: link + sample.

10. **STOP.** Do not send. Sends only happen on Dhruv's explicit approval in-channel. When approval arrives, a subsequent turn runs the send script with 20s gaps via himalaya and logs to `CRM/_contacted.md`.

Red lines:
- Never send from this cron run. Drafts only.
- Never propose a candidate already in the dedup ledger within 90 days.
- Never fabricate Apollo-verified when it was pattern-guessed — flag with ⚠️.
- If reply rate on the last 50 sends drops below 2%, stop the campaign and propose a pivot rather than queuing more drafts.
- If Dhruv's inbox has unanswered real replies (not bots/auto) from prospects, surface those BEFORE drafting new outreach.
