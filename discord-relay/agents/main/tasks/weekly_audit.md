---
cron: 0 4 * * 0
kind: systemEvent
---
Weekly security sweep. Sundays at 4am Pacific.

1. Shell to `./.venv/bin/python scripts/audit.py --history --json` and parse.
2. If zero findings across both audits, log the clean result and stop.
3. If there are findings:
   - Write a detailed report to `OpenClaw/Agent Notes/main/status/YYYY-MM-DD-audit.md` in the vault — one section per category, list `path:line` + snippet.
   - Post a 4-line summary to the channel: what was found, where, severity, link to vault note.
   - For any `discord-webhook` / `discord-bot` / `anthropic-key` / `openai-key` category hit, mark it as **rotate now** in the summary.
4. Never paste the actual secret into Discord. Redact to first 4 chars + `…`.

Expected runtime: up to 2 minutes for the git-history scan.
