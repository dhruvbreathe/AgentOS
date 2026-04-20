---
cron: 0 17 * * 5
---
Weekly reply-rate report — Fridays 17:00 Pacific.

Goal: honest readout of what's working across outreach. Post in-channel so Dhruv can steer. No silent — this one needs eyes.

Steps:

1. **Read the ledger.** `/Users/celainc/Documents/Vayu/Vayu/CRM/_contacted.md` is the source of truth for every send. Parse it into rows keyed by `email`, `category`, `date`, `status`.

2. **Scan the inbox for replies.** `himalaya envelope list -a dhruv --folder INBOX --page-size 200`. For each sent address in the ledger, check for any `Re:` or `RE:` matching the subject line (fuzzy match on the subject stem — strip "Re:" prefix and compare). Count a reply as: any human response (not auto/OOO/bots like Vercel, Sentry, LinkedIn, Calendly, Intercom auto-acks). Bounces count as a separate category, not a reply.

3. **Compute per-category metrics over the last 14 days:**
   - `yoga` — sent, replies, reply rate, bounces
   - `investor` — sent, replies, reply rate, bounces
   - `insurance` — sent, replies, reply rate, bounces
   - overall blended reply rate

4. **Identify the weakest category.** If any category is below 2% reply rate over a sample of ≥20 sends, flag it for pause or rewrite. If reply rate is 0 across all three at ≥40 sends blended, the problem is campaign design, not audience — flag to Dhruv with a concrete pivot proposal (new angle, shorter email, different subject line pattern, alternate proof points).

5. **Highlight live replies from this week.** Any human response since last Friday — name, company, category, one-line gist of what they said. Surface them even if already handled, so Dhruv can confirm follow-up state.

6. **Post a tight report to #marketing-finance-pr** with:
   - 📊 per-category table (sent / replies / rate / bounces)
   - 🎯 blended reply rate + prior week delta
   - 🔥 live replies worth seeing this week (if any)
   - ⚠️ any category flagged for pause or rewrite — concrete proposal, not vague concern
   - 📋 ask: "hold this campaign / pivot copy / keep ramping?" — one-line decision prompt

7. **Write the full detail to vault.** `Sessions/YYYY-MM-DD-marketing-weekly-reply-report.md` — full table, all replies classified, any bounces listed, raw numbers so next week can show delta.

Red lines:
- Never fabricate a reply count. If parsing is ambiguous, show the raw candidates and ask.
- Never hide a failing category. If yoga is at 0/25, say so plainly — Dhruv wants the honest number, not a hedge.
- Never recommend more volume when reply rate is in the red. More sends on a broken sequence just wastes domain reputation.
- Bounces are not replies. Count them separately and surface them explicitly.
