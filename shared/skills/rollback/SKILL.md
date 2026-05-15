---
name: rollback
description: Restore the agent's working state to a previous checkpoint. List recent checkpoints, peek at the diff before committing, then restore. Approval-gated — the operator sees a ✅/❌ request before the restore executes.
invocation: Bash (`python -m checkpoint_gate {list,peek,restore} ...`)
---

# rollback — undo what you just did

Checkpoints get created automatically on every Write/Edit (PreToolUse hook), and manually via `/checkpoint`. Use this skill when work has gone sideways and you want to restore state.

## Subcommands

### List recent checkpoints
```bash
python -m checkpoint_gate list <agent-name> --limit 10
```
Returns JSON array of the last N checkpoints, newest first. Each has `id`, `mode`, `created_at`, `message`, `files_touched`. Use the `id` for `peek` and `restore`.

### Peek (read-only — no gate)
```bash
python -m checkpoint_gate peek <agent-name> <id>
```
Shows a diff summary between current state and the checkpoint. Use this **before** restoring to confirm you're rolling back to the right place.

### Restore (destructive — approval-gated)
```bash
python -m checkpoint_gate restore <agent-name> <id>
```

> Yes, this command will pause and ask the operator for ✅ in Discord before executing. That's the safety gate. **Don't try to work around it.**

Restore behaviour:
- **Auto pre-rollback checkpoint.** Your current state gets snapshotted before the restore, so undo-undo works. You're never one keystroke from permanent loss.
- **Git-stash mode** (code agents): `git stash apply <stash-of-target>` after resetting working tree. The target stash stays in the stash list, so the restore is repeatable.
- **File-snapshot mode** (vault agents): files copied back from the snapshot dir to their original paths.

If something goes wrong mid-restore, the operation aborts and reports — never leaves you in a half-restored state.

### Force restore (skip safety check)
```bash
python -m checkpoint_gate restore <agent-name> <id> --force
```
Only use when you understand the "uncommitted changes since checkpoint" warning and want to proceed anyway. Still goes through the approval gate.

## Typical flow

```bash
# 1. List what's available
python -m checkpoint_gate list main --limit 5

# 2. Pick an id from the output, preview the diff
python -m checkpoint_gate peek main ckpt-20260514-153022-a3f

# 3. If the diff looks right, restore. Wait for operator ✅ in Discord.
python -m checkpoint_gate restore main ckpt-20260514-153022-a3f
```

## What `/rollback` doesn't do

- Doesn't roll back files that weren't part of the checkpoint (it only restores what was snapshotted).
- Doesn't undo external side effects — sent emails, pushed commits, posted Discord messages stay where they are.
- Doesn't restore deleted checkpoint snapshots (run `prune` carefully; default retention is 20 checkpoints or 7 days).

## Pair with `/checkpoint`

Create named savepoints before risky operations, then restore them later:
```bash
# Before a big rewrite
python -m checkpoint_gate create main "$VAULT_PATH" Topics/*.md --message "before topic merge"

# ... work happens, goes sideways ...

# Roll back
python -m checkpoint_gate restore main <the-id-it-printed>
```
