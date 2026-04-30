# SCHEDULING.md — How I Schedule Recurring Work

I can schedule my own recurring tasks. The backend is **launchd** (not cron) — macOS's native scheduler — because cron writes hang in this sandboxed environment (TCC gate). The authoring surface is the same: a `cron:` frontmatter line on a task file.

## Where tasks live

My scheduled tasks live at:
`{AGENTOS_ROOT}/agents/<my-name>/tasks/<task-name>.md`

Each task file looks like:

```markdown
---
cron: 0 8 * * *
---
The prompt body — whatever I should do when this task fires.
Keep it focused, one job per task.
```

The `cron:` line uses standard 5-field crontab syntax (`minute hour day month dow`). The installer translates it to a launchd `StartCalendarInterval` plist. The file body *is* the prompt — it's what I receive when the task fires.

Supported cron forms (extend as you need):
- `M H * * *` — daily at H:M
- `M H * * D` — weekly on day D (0=Sun…6=Sat) at H:M
- `M H * * D1-D2` — weekday range (expanded to N plist entries)
- `M H * * D1,D2` — weekday list

Anything else will fail loudly at install time.

## The flow (always follow this)

**1. Draft the task file.**
Write to `agents/<my-name>/tasks/<task-name>.md` with the frontmatter above.

**2. Dry-run the installer.**

```bash
python {AGENTOS_ROOT}/scheduler/install.py
```

Prints the full plan without touching launchd. Paste the output into Discord so the operator can review.

**3. Wait for explicit go-ahead.**
The operator must say "apply" / "go" / "yes" before I move on.

**4. Apply — I run this myself. Do not ask the operator.**

```bash
python {AGENTOS_ROOT}/scheduler/install.py --apply
```

This writes plists to `~/Library/LaunchAgents/com.agentos.<agent>-<task>.plist` and loads them with `launchctl bootstrap gui/$UID`. No hooks block it, no TCC prompt.

**Safety net:** the installer skips any task that's still in the *legacy user crontab* managed block — if the cron twin is still firing, it won't double-install. The warning output tells you which ones got skipped and why.

**5. Confirm.**

```bash
python {AGENTOS_ROOT}/scheduler/install.py --list
```

Shows every currently-loaded `com.agentos.*` job. Verify my new one is there and tell the operator it's live.

## Legacy cron migration

The old path was `cron/install.py` writing to the user crontab. That's deprecated — the `cron/install.py` call hangs in-sandbox because `crontab` writes on macOS require a TCC prompt.

Twelve entries still exist in the legacy user crontab from before this migration. They continue to fire. To flip them to launchd:

1. Operator opens a real terminal and runs `crontab -e`
2. Deletes everything between `# --- agentos (managed) ---` and `# --- /agentos ---`
3. Back in Discord, an agent runs `scheduler/install.py --apply` — it'll pick up all previously-skipped tasks and install them via launchd

No hurry. Existing tasks keep running until migration.

## Hard rules

- **Use `scheduler/install.py`. Not `cron/install.py`.** The old one doesn't work from Discord anymore.
- **Never schedule someone else's work.** I only create tasks under my own `agents/<my-name>/tasks/`.
- **Every task writes a log line** at `{AGENTOS_ROOT}/logs/<agent>-<task>.log`. Nothing runs silently.
- **Prefer conservative schedules.** `* * * * *` is almost never right. Default to hourly or slower.
- **Name tasks well.** `daily_digest.md`, `weekly_reddit_roundup.md` — not `task1.md`.

## Two kinds of tasks: posted vs silent

Every task file supports a `kind:` field in the frontmatter. The default is a normal posted task — output goes to my Discord channel via webhook. For internal housekeeping that doesn't need to surface publicly, use `kind: systemEvent`:

```markdown
---
cron: 0 3 * * 0
kind: systemEvent
---
Weekly memory maintenance — distill memory/*.md into LEARNINGS.md,
archive files older than 14 days. Log to trajectory, don't post.
```

When `kind: systemEvent` (or `silent: true`), the cron runs me normally — full Claude session, trajectory logging, Stop/PreCompact hooks all fire — but **skips the webhook post**. The output lands in my trajectory JSONL + the per-task log file at `logs/<agent>-<task>.log`.

**Use for:**
- Memory maintenance and distillation
- Vault hygiene (pruning stale notes, fixing link rot)
- State-refresh jobs that shouldn't clutter the channel
- Anything I'd be annoyed to see in Discord every day

**Default is posted.** Only add `kind: systemEvent` when there's a real reason the operator shouldn't see the output in-channel.

## Removing a task

Delete the task file and run `scheduler/install.py --apply`. The installer bootouts and deletes any managed plist whose task file no longer exists.

To nuke all scheduled work: `scheduler/install.py --remove-all`.

## One-shot deferred runs (single fire, then self-clean)

Recurring schedules belong in `tasks/*.md`. If I just need to wake myself up **once** at a future time (a single follow-up, an approval window, an external batch settling), use the deferred runner instead — see CONTINUATION.md for the full rules:

```bash
python {AGENTOS_ROOT}/scripts/defer.py <my-name> "in 30m" "<the prompt I want fired at me>"
```

That writes a one-shot launchd plist + task file, fires once at the target time, posts to my channel via webhook, then deletes itself. The recurring `scheduler/install.py` skips deferred-run plists, so it's safe to mix the two. **Never** use a recurring `cron:` task for a one-time follow-up.

## How to restart the relay from Discord

If I've changed config files (agent.yaml, shared/*.md, config.yaml) and need a restart:

```bash
touch {AGENTOS_ROOT}/logs/.restart-requested
```

The bot checks for this file **between turns**. After my current turn finishes, the bot exits cleanly. If `scripts/autorestart.sh` is the runner, it catches the exit and starts a fresh process within ~3 seconds.

**Rules:**
- I do **not** run `pkill` on bot.py — that kills my own turn mid-stream.
- I finish my current reply, tell the operator "restart queued ⚡ — takes effect in a few seconds", then touch the file.
- The restart picks up all changes to Python code, shared docs, agent configs, and .env.
- If `autorestart.sh` is NOT running (bot was started manually), the signal file still works — the bot exits, but nothing auto-restarts it. The operator would need to start it again.
