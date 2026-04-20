---
name: coding-agent
description: Delegate focused coding work to a subagent or shell out to a fresh Claude Code session for an isolated task. Use when a coding task is large enough to deserve its own session (long refactor, multi-file migration, complex debug) or when the current agent is focused on something else and shouldn't context-switch.
---

# coding-agent — farm out coding work cleanly

## When to reach for this

- A coding task that would take 20+ turns in the current session
- Work that should happen on a branch, isolated from what you're doing now
- Cross-cutting refactor touching many files — burn a fresh context, not yours
- Code review of a specific diff or PR
- A bug investigation where you don't want your current work contaminated

## When NOT to use this

- A 2-file edit — just do it yourself
- Something that needs your in-session context (this conversation's decisions, operator preferences just stated)
- Anything requiring the current agent's specific identity/voice (writing, customer-facing content)

## Two invocation paths

### Path A — in-process subagent (fast, scoped)

Use the SDK's `Task` tool with a `subagent_type` declared in your `agent.yaml`:

```yaml
# agent.yaml
subagents:
  code-reviewer:
    description: "Review specific files/diffs for bugs, missing error handling, security issues."
    prompt_file: skills/code-reviewer-prompt.md
    tools: [Read, Grep, Glob, Bash]
    model: claude-sonnet-4-6
    max_turns: 10
```

```python
Task(
    subagent_type="code-reviewer",
    description="Review auth middleware change",
    prompt="Review the diff in src/auth/middleware.py vs main. Flag bugs, missing error handling, race conditions. Under 300 words.",
)
```

Good for: focused code review, scoped refactor that returns a summary.

### Path B — shell a fresh Claude Code session (isolated worktree)

```bash
# Create an isolated worktree so changes don't collide with your main tree
cd /Users/celainc/Developers/ClaudeAgentSDK/discord-relay
git worktree add ../_worktrees/feature-x -b feature-x

# Launch a one-shot claude CLI session in it
cd ../_worktrees/feature-x
claude -p "Refactor the auth middleware to use the new token validator. All changes in src/auth/. Run tests when done." \
       --permission-mode bypassPermissions \
       --output-format stream-json \
       > /tmp/coding-session-$(date +%s).log 2>&1 &

# Monitor progress
tail -f /tmp/coding-session-*.log

# When done: review, merge, clean up
git worktree remove ../_worktrees/feature-x
```

Good for: long refactors, migrations, anything where you want to review a complete diff before merging.

## Handoff contract

If you're delegating, be specific:

```
✅ Clear brief
- What file(s) to touch
- What "done" looks like (tests pass / specific output)
- What NOT to change
- Budget (turns or time)

❌ Vague brief
"Clean up the auth code"
"Make it better"
"Fix the bug"
```

## Reporting back

When the sub-task finishes, the orchestrator (you) summarizes in Discord:

```
✅ **Auth middleware refactor landed**
- 3 files changed: `src/auth/{middleware,validator,types}.py`
- All 12 existing tests pass
- Branch: `feature-x` (ready to merge)
- Diff: <link or path>
💨
```

Do not dump the full diff into Discord. The operator reads summaries, clicks through to diffs when they want detail.
