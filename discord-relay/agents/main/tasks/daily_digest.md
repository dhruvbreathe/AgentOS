---
cron: 0 8 * * *
---
Produce the morning daily digest for the operator.

1. Look at today's date and the last 24 hours.
2. Check `Sessions/` in the vault for any session logs from yesterday.
3. Summarise: what got done, what is open, what is the top priority today.
4. If there is an active infrastructure initiative (e.g. Prana Agent OS build-out), surface a one-line pointer with a link to the canonical topic note, e.g. `Topics/Prana-Agent-OS-Replication-Plan.md`.

Output format:
**Yesterday:** <2–3 bullets>
**Open:** <1–3 bullets>
**Today's focus:** <one sentence>
**Build notes:** <optional — link to any active topic doc>

Keep the whole digest under 1500 characters.
