---
name: checkpoint
description: Manually snapshot the current working state with a labeled message before doing something risky. Auto-checkpoints already fire on every Write/Edit — use this when you want a named save point, or when you're about to do a non-file-write risky operation (large refactor, migration, bulk vault rewrite).
invocation: Bash (`python -m checkpoint_gate create ...`)
---

# checkpoint — name a save point before risky work

Auto-checkpoints already fire on every Write/Edit/MultiEdit (PreToolUse hook), so most of the time you don't need to think about this. Use `/checkpoint` when:

- You're about to do a large multi-file rewrite and want one named savepoint covering the whole batch.
- You're running a migration script via Bash (auto-checkpoints don't cover non-edit tool calls).
- You want a human-readable label on the snapshot — `"before onboarding rewrite"` reads better than `ckpt-20260514-153022-a3f`.

## How to use

```bash
python -m checkpoint_gate create <agent-name> <cwd> [file1 file2 ...] --message "<your label>"
```

Concretely, from your agent's environment:

```bash
python -m checkpoint_gate create main "$VAULT_PATH" \
  Topics/Vayu.md Topics/Marketing.md \
  --message "before topic-note consolidation pass"
```

Mode is auto-detected:
- If `cwd` is a git repo → `git stash` captures the whole working tree.
- If `cwd` is the vault or any non-git dir → individual files are copied into `agents/<agent-name>/checkpoints/<id>/`.

## What you get back

```json
{"id":"ckpt-20260514-153022-a3f","agent":"main","mode":"file-snapshot","cwd":"/Users/celainc/Documents/Vayu/Vayu","files_touched":["Topics/Vayu.md","Topics/Marketing.md"],"snapshot_dir":"...","tool":"manual","message":"before topic-note consolidation pass","created_at":"2026-05-14T15:30:22-07:00"}
```

The `id` is what you pass to `/rollback to <id>` later.

## When NOT to use this

- For new files you're creating — there's no prior state to checkpoint.
- For tiny one-line edits — the auto-checkpoint already covered them.
- Inside a tight loop — debounce window is 60s, so rapid-fire calls bundle into one snapshot anyway.

## Pair with `/rollback`

If the work goes sideways:
- `/rollback list` — see your recent checkpoints
- `/rollback peek <id>` — preview the diff before restoring
- `/rollback to <id>` — restore (approval-gated, so the operator sees the request first)
