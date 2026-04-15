# SCHEDULING.md — How I Create Cron Jobs

I can schedule my own recurring work. Here's the protocol — do not deviate.

## Where tasks live

My scheduled tasks live at:
`/Users/celainc/Developers/ClaudeAgentSDK/discord-relay/agents/<my-name>/tasks/<task-name>.md`

Each task file looks like:

```markdown
---
cron: 0 8 * * *
---
The prompt body — whatever I should do when this task fires.
Keep it focused, one job per task.
```

The `cron:` line uses standard crontab syntax (`minute hour day month dow`).
The file body *is* the prompt — it's what I receive when the task fires.

## The flow (always follow this)

**1. Draft the task file.**
Write to `agents/<my-name>/tasks/<task-name>.md` with the frontmatter above.

**2. Dry-run the installer.**

```bash
python /Users/celainc/Developers/ClaudeAgentSDK/discord-relay/cron/install.py
```

This prints every managed cron entry without touching the system crontab. Paste the output into Discord so the operator can review.

**3. Wait for explicit go-ahead.**
The operator must say "apply" / "go" / "yes" before I move on. If they don't, I stop here and leave the task file in place for later.

**4. Apply — I run this myself. Do not ask the operator to run it.**
Once the operator has said yes, I execute this from my Bash tool:

```bash
python /Users/celainc/Developers/ClaudeAgentSDK/discord-relay/cron/install.py --apply
```

**Important:** the PreToolUse hook explicitly whitelists `cron/install.py` — it passes through the Bash guard cleanly. I do **not** need the operator to run it from their terminal. If I find myself typing "I need you to run..." at this step, I'm wrong — I run the command. The only thing I needed from them was the "apply" nod in step 3.

This rewrites only the managed block between `# --- discord-relay (managed) ---` and `# --- /discord-relay ---`. It does not touch unmanaged cron lines.

**5. Confirm.**
After applying, run `crontab -l` (read-only is allowed and passes the hook) and confirm the new line is in the managed block. Tell the operator it's live.

## Hard rules

- **Never run `crontab -e`, `crontab -r`, or pipe into `crontab -` directly.** The PreToolUse hook will block those with a message pointing to the installer. `crontab -l` (list/read) is allowed.
- **`python cron/install.py [--apply]` is the safe path.** It is whitelisted by the hook regardless of what it calls internally. Use it.
- **Never schedule someone else's work.** I only create tasks under my own `agents/<my-name>/tasks/` folder.
- **Every cron task writes a log line.** The installer already redirects stdout/stderr to `discord-relay/logs/<agent>-<task>.log` so nothing runs silently.
- **Prefer conservative schedules.** `* * * * *` (every minute) is almost never right. Default to hourly or slower unless the operator asks.
- **Name tasks well.** `daily_digest.md`, `weekly_reddit_roundup.md` — not `task1.md`.

## When to remove a task

If a task is obsolete: delete the file and run the installer again (`--apply`). The managed block regenerates from scratch each run, so removing the file removes the cron entry.
