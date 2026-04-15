# SUBAGENTS.md — Scoped Specialists I Can Spawn

Sometimes I need a focused helper for one task without pinging another agent's channel. For that, I have **subagents** — temporary, in-process specialists defined in my `agent.yaml`. They run under my turn, report back once, then go away. No hop burn, no channel noise.

## When a subagent is right

- A scoped piece of research I don't want to route to `market-intelligence-engine` (too small) or do myself (too distracting).
- A code review pass on a specific file before I hand off.
- A synthesis step that needs a different voice or stricter prompt than mine.
- Anything that would otherwise make me run a long reasoning chain in my own turn.

## When NOT to spawn a subagent

- When another full agent (main, marketing, qa, etc.) is the right owner — route to them via `send_to_agent` instead, and let them take it from there in their own channel.
- For trivial questions I can answer directly.
- For anything that requires writing/sending something externally — those should go to the agent that owns that surface.

## How to invoke

The SDK's `Task` tool takes a `subagent_type` matching a name declared in my `agent.yaml`:

```python
Task(
    subagent_type="research",
    description="Quick research on Oura Gen 4 API",
    prompt="Check whether Oura Gen 4 exposes a public health API and summarise in 3 bullets with source links.",
)
```

I get back a single message with the subagent's findings. That's it.

## Declaring subagents in my agent.yaml

```yaml
subagents:
  research:
    description: "Focused research subagent — one question, sourced answer."
    prompt: |
      You are a research specialist. Answer ONE question in under 200 words
      with 3 bullets and source links. No caveats, no "based on my search".
    tools: [WebSearch, WebFetch, Read, Grep, Glob]
    model: claude-haiku-4-5-20251001   # cheap + fast for scoped lookups
    max_turns: 5

  code-reviewer:
    description: "Reviews a specific file/diff for obvious problems."
    prompt: |
      Review what I give you. Flag: bugs, missing error handling, security
      issues, obvious performance problems. Skip style. Under 200 words.
    tools: [Read, Grep, Glob]
    model: claude-sonnet-4-6
    max_turns: 3
```

Use `prompt_file:` instead of inline `prompt:` to pull a longer prompt from a skill file in my own workspace.

## Rules

1. **One subagent call per turn, usually.** If I find myself spawning three subagents, I should probably be routing to a real agent instead.
2. **Subagents don't have my identity.** Don't give them tools that would speak as me in Discord. `send_to_agent` is not in any subagent's tool list by default.
3. **Keep prompts tight.** A subagent with a 2-page prompt defeats the purpose. 5–10 lines of instruction, clear output shape, go.
4. **Prefer cheap models for scoped work.** Haiku or Sonnet, not Opus, unless the task genuinely needs deep reasoning.
