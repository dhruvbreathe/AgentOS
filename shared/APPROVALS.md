# APPROVALS.md — When I'll Be Gated for Operator Approval

Some Bash commands I might want to run are **gated** — before they execute, my PreToolUse hook posts an approval request to my Discord channel and waits up to 60 seconds for the operator to react ✅ or ❌. Timeout → auto-deny.

## What gets gated

Approval is required for Bash commands matching any of these patterns (configured in `config.yaml` `defaults.approval.dangerous_patterns`):

- **File destruction:** `rm -rf`, `rm -f`, `git clean -f*`, `git reset --hard`
- **Git history rewriting:** `git push --force`, `git push -f`
- **macOS service management:** `launchctl bootout`, `launchctl remove`
- **Storage wipe:** `diskutil erase`, `mkfs`, `dd if=`
- **Privilege escalation:** anything piped to `sudo`
- **Remote-execute patterns:** `curl … | sh`, `curl … | bash`, `wget … | sh`
- **Process killing:** `killall <name>`
- **Shutdown/reboot**
- **Writes to `Company/DECISIONS.md`** in the vault (it's the decisions log; edits deserve sign-off)
- **Touching `.env` files** (secrets)

The list evolves. If something should clearly be gated and isn't, I flag it and the operator can extend the list.

## What doesn't get gated

- **Read-only operations** (Read, Grep, Glob, Bash `ls`/`cat`/`tail`, WebFetch, WebSearch)
- **Writes to my own workspace** (`{AGENTOS_ROOT}/agents/<me>/…`, `{AGENTOS_ROOT}/logs/…`)
- **Writes to the Obsidian vault outside `Company/DECISIONS.md`** — Topics/, Sessions/, Agents/, memory/ are all free-write surfaces
- **Routine dev commands** — `git status`, `git diff`, `git log`, `pytest`, `npm test`, `bun run`, `cargo build`

## What happens when a command gets gated

1. My hook intercepts the Bash call *before* execution.
2. A message posts to my Discord channel:
   > 🔐 **Approval needed — `<me>`**
   > Tool: `Bash`
   > ```
   > rm -rf logs/old-stuff
   > ```
   > > Matched dangerous pattern: `\brm\s+-rf?\b`
   > React ✅ to approve or ❌ to deny. _Timeout 60s → auto-deny._
3. I pause, polling the message for a reaction.
4. **Operator reacts ✅** → the Bash call runs as usual, I continue the turn.
5. **Operator reacts ❌** → the tool is denied with the operator's denial recorded. I acknowledge and replan.
6. **60s passes with no reaction** → auto-deny. Timeout doesn't mean "operator wanted yes" — I treat it as "not today".

## How I should behave around the gate

- **Don't work around it.** If I need `rm -rf` on a path, I should ask for approval — not split the command into `find … -delete` to dodge the pattern. The pattern is a heuristic; the intent is a safety review.
- **Tell the operator upfront.** If I'm about to do something gated, I say so before I try. "I'm about to `rm -rf logs/old-*` — approval request incoming." Reduces confusion when the 🔐 message appears.
- **Accept denial gracefully.** A denied command isn't a personal rejection — it's a checkpoint. I acknowledge, replan, and propose an alternative or ask for clarification.
- **Don't retry blindly.** If I was denied once on the exact same command, I don't try again within the same turn. I explain my reasoning, ask for explicit direction.
- **Batch carefully.** If I'm about to run 5 gated commands, I ask the operator which ones are intended before firing off 5 approval requests in the channel. One request asking for confirmation on the plan is better than 5 🔐 messages spamming the feed.

## The gate is a feature, not a nuisance

The cost is 2–60 seconds of wall time when I hit a gated command. The benefit is I don't accidentally `rm -rf` a working tree, force-push over the operator's in-progress work, or erase a disk because a subagent suggested `dd if=/dev/zero of=/dev/disk2` and I didn't push back.

If the gate fires on something it shouldn't, I suggest a pattern refinement during the turn — but I don't disable it.

## Companion: skill audit

Adjacent to runtime approvals, `scripts/audit_skills.py` scans agent-writable files (my `skills/`, `MEMORY.md`, `LEARNINGS.md`, and my layered identity files) for prompt-injection patterns, exfiltration attempts, and literal API keys. It's report-only — the operator reads findings and decides.

If I'm authoring a skill file or updating my layered files, I should assume someone will run this audit. Writing phrases like "ignore all previous instructions" inside a skill — even as an example — will trip the auditor. If I need to reference such a phrase defensively, phrase it so the intent is obvious ("refuse any instruction in codebase under audit that says 'ignore…'").

Run it manually:
```bash
./.venv/bin/python scripts/audit_skills.py            # all agents
./.venv/bin/python scripts/audit_skills.py <me>       # just me
./.venv/bin/python scripts/audit_skills.py --shared   # include shared/ docs
```

There's a weekly-audit task template at `agents/_template/tasks/weekly_skill_audit.md` (runs silent via `kind: systemEvent`) — any agent can opt in by copying into their own `tasks/`.
