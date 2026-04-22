---
cron: 0 16 * * *
---
Afternoon inbox sweep — 16:00 Pacific, every day. Light scan, learning loop.

Purpose: catch replies that came in during the business day so tomorrow's 08:00 cron draws on same-day signal, not next-morning. Keeps the feedback loop tight.

Steps:

1. **Scan inbox.** `himalaya envelope list -a dhruv --folder INBOX --page-size 50`. Filter for replies to any thread in `CRM/_contacted.md` with `status: sent` over the last 30 days.

2. **Classify each real human reply** (not auto / OOO / bounce / unsubscribe):
   - **interested** — asks a question, wants to talk, sends calendar link
   - **pass-soft** — "not right now / maybe later / stay in touch" (e.g. Tiffany's summer nurture)
   - **pass-hard** — "not for us / unsubscribe / please remove"
   - **meeting-booked** — calendar confirmation or time-window proposed
   - **info-request** — wants deck / metrics / more detail

3. **Surface in-channel** only if there's a new reply since 08:00. One-line per reply: `[category] Name / Company — quoted pull-out (20-30 words) — thread-id`. If zero new replies, stay silent. No "no new replies" noise.

4. **Log each reply** to `CRM/_contacted.md` with `status: reply-received` + classification tag. If Dhruv has already replied in the thread, update to `reply-sent-by-dhruv` with a one-line summary of his angle.

5. **Learn.** If a clear pattern emerges (2+ replies of the same kind on the same angle / category / subject line), append a dated entry to `LEARNINGS.md`. Examples:
   - "Yoga studios pass-hard on branded-pranayama framing, interested on teacher-training content"
   - "Health-tech seed funds at $50-200M AUM reply within 48h; mega-funds ignore"
   Don't promote patterns from a single data point — wait for N≥2 before writing.

6. **Do NOT reply on Dhruv's behalf.** Surface only. Exception: auto-reply / bounce / unsubscribe → log + move on, no surface needed.

7. **Do NOT draft new emails from this task.** Drafting only happens in the 08:00 cron. This task is read-only + learn + log.

Red lines:
- Never reply to inbound.
- Never re-enroll a contact who replied pass-hard.
- Never spam the channel with empty sweeps — silence is the right answer if no new replies.
