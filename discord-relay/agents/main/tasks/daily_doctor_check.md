---
cron: 30 7 * * *
kind: systemEvent
---
Daily self-diagnostic. Runs 30 minutes before the 8am digest so failures surface with breakfast.

1. Shell to `./.venv/bin/python scripts/doctor.py --json` and parse the output.
2. If every agent reports `worst: ok`, log the summary line and stop — no Discord post.
3. If ANY agent reports `worst: warn` or `worst: fail`:
   - Write a short report into `OpenClaw/Agent Notes/main/status/YYYY-MM-DD-doctor.md` in the vault with the failing agents + specific checks.
   - Ping the channel via the normal reply path with a 3-line summary and a pointer to the vault note.
4. If a `launchd_tasks` warning is the only issue, suggest `python scripts/doctor.py --fix` as the next step.

Keep it quiet on green days. Loud only when something's actually wrong.
