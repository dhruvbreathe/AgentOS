---
cron: 0 8 * * *
---
Produce the morning daily digest for the operator.

1. Look at today's date and the last 24 hours.
2. Check `Sessions/` in the vault for any session logs from yesterday.
3. Summarise: what got done, what is open, what is the top priority today.

Output format:
**Yesterday:** <2–3 bullets>
**Open:** <1–3 bullets>
**Today's focus:** <one sentence>

Keep the whole digest under 1500 characters.
