---
name: healthcheck
description: Run a self-diagnostic on the agent stack. Use when the operator asks "is everything healthy", when a cron job has been failing, when a webhook stops responding, or when you're about to do something load-bearing and want to verify the substrate. Wraps scripts/doctor.py.
---

# healthcheck — verify the substrate before you act

## When to reach for this

- Operator asks: "is everything up?", "is X broken?", "check the agents"
- A scheduled task logged a failure and you're diagnosing
- You're about to run a migration, deploy, or something load-bearing
- Weekly sanity sweep (wire this into a `kind: systemEvent` cron)

## How it works

One command:

```bash
cd {AGENTOS_ROOT}
./.venv/bin/python scripts/doctor.py           # all agents
./.venv/bin/python scripts/doctor.py --agent main  # one agent
./.venv/bin/python scripts/doctor.py --fix     # attempt auto-fix where safe
```

Output is structured JSON so you can parse it, or pretty text if stdout is a TTY.

## What it checks

Per agent (and globally):

- **Webhook URL** — env var set, DNS resolves, Discord endpoint returns 2xx on GET
- **Bot token** — present, validates against Discord API (`/users/@me`)
- **Vault mount** — `$VAULT_PATH` exists, agent can Read a known marker file
- **launchd jobs** — every `tasks/*.md` has a loaded `com.agentos.<agent>-<task>` plist
- **Trajectory log** — recent session JSONL exists, not zero-bytes
- **Last turn** — agent produced output within expected cadence (configurable)
- **.env completeness** — every `<X>_WEBHOOK_URL` referenced in any agent.yaml has a value

## Reporting in Discord

When you run this and post back, follow EXPRESSION rules:

```
📋 **healthcheck — all green**
- ✅ 14 agents alive, webhooks 2xx, bot tokens valid
- ✅ launchd: 28 jobs loaded, no drift
- ✅ vault mounted, 0 missing env vars

💨
```

If something's wrong:

```
⚠️ **healthcheck — 2 issues**
- ❌ `ads` webhook returns 401 — token rotated?
- ❌ `reddit-crawler` last turn 18h ago, expected <6h

Running `--fix` would: rotate webhook, trigger Rook manually. Want me to?

💨
```

## What `--fix` will do

Safe auto-fixes only. If in doubt, it reports instead of fixing:

- Reload a launchd plist that exists on disk but isn't loaded
- Recreate a webhook if `create_webhooks.py` has credentials
- Touch `logs/.restart-requested` if bot.py has been unresponsive >5min

It will NOT:
- Rotate bot tokens (operator-only)
- Modify .env files (operator-only)
- Delete trajectories or memory files
- Restart agents mid-turn

## Scheduling

Recommended: daily `kind: systemEvent` at 03:00 local, only post to Discord if issues found. Template:

```markdown
---
cron: 0 3 * * *
kind: systemEvent
---
Run scripts/doctor.py. If all green, log and exit silent.
If any red, post summary to #<my-channel> with the issues + proposed fix.
```
