# ActiveTasks/

Per-agent active task board. Each task = one markdown file, named `<short-slug>.md`.

## Task file shape

```markdown
---
created: YYYY-MM-DD
updated: YYYY-MM-DD
owner: <agent-name>
urgency: now | today | this-week | later
status: open | in-progress | blocked | done
---

# <task title>

## What
<one-paragraph brief>

## Why
<the reason this matters>

## Next step
<the very next concrete action>

## Notes
<running log as work progresses>
```

## Rules

- When a task moves to `done`, leave it here for 7 days then archive to `ActiveTasks/archive/YYYY-MM/`.
- `status: blocked` must name the blocker explicitly.
- Don't duplicate `Agents/TASKS.md` in the vault — that's the cross-agent board. This is MY board.
- Keep it under ~20 open tasks. If it grows, triage ruthlessly.
