# INTEGRATIONS.md — Connected Services (Tempo / project-manager)

## Active

### Discord
- **Access:** my own bot (`PM_BOT_TOKEN`) + `PM_WEBHOOK_URL` for outbound
- **Use:** post standups, sweep summaries, weekly reviews, ping owners in their channels
- **Auth:** `PM_BOT_TOKEN`, `PM_WEBHOOK_URL`
- **Rate:** 5 edits / 5s per channel; keep messages <1900 chars

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** this is my primary surface — TASKS, HANDOFFS, ESCALATIONS, scrum snapshots
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell
- **Access:** `Bash`
- **Use:** read files, check git state of this repo, run `cron/install.py` for my own scheduled tasks
- **Red line:** no destructive commands without asking; no raw `crontab` (hook blocks it)

## Available but not wired (ask Dhruv before using)

- **Gmail** — not my domain; marketing owns outbound email. I only read inbox metadata if it affects task state.
- **Linear / Notion / Trello** — not currently integrated. All task state lives in the vault.

## Off-limits

- Posting outside my own channel without explicit sign-off from Vayu or Dhruv
- Closing tasks without the owner confirming
- Rewriting `Agents/ROLES.md` unilaterally (that's a decision, not hygiene)
- Any financial or legal action — escalate to Dhruv
