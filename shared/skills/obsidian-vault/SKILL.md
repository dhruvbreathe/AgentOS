---
name: obsidian-vault
description: Read and write the Prana Labs Obsidian vault — session logs, topic notes, company facts and decisions. Use BEFORE any external research (web search, cloning repos, exploring codebases); the answer is usually already documented here. Also use when asked to record a decision, save a session, or look up what was decided about a person, project, or tool.
---

# Obsidian Vault

The vault at `$VAULT_PATH` (`/Users/celainc/Documents/Vayu/Vayu`) is the durable
memory for Prana Labs. It outlives any single agent session. Treat it as the
source of truth for decisions, history, and context.

## Search the vault FIRST

Before a web search, before cloning a repo, before exploring a codebase: check
the vault. Past sessions almost always documented the answer — what was done,
why, and what was decided. Going external when the vault has the answer wastes
time and produces worse answers.

```bash
# fast keyword sweep across the memory-bearing directories
rg -l "<keyword>" "$VAULT_PATH/Sessions" "$VAULT_PATH/Topics" \
   "$VAULT_PATH/Company" "$VAULT_PATH/Conversations" 2>/dev/null | head -20

# then read the most relevant hits
rg -n "<keyword>" "$VAULT_PATH/Topics" | head -30
```

Search order, cheapest first:

1. `Topics/` — one note per person, project, tool, org. Quick orientation.
2. `Sessions/` — dated session logs, the detailed record of what happened.
3. `Company/` — `FACTS.md`, `DECISIONS.md`, `OPERATING-SYSTEM.md`, plans, specs.
4. `Conversations/` — raw exports. Last resort; large and noisy.

## Layout

| Path | Holds |
|---|---|
| `Sessions/` | `YYYY-MM-DD-<project>-<topic>.md` — what happened, decisions, next steps |
| `Topics/` | One note per named entity. Proper nouns only, never generic concepts |
| `Company/` | `FACTS.md`, `DECISIONS.md`, `OPERATING-SYSTEM.md`, plans, specs |
| `Conversations/` | Raw conversation exports |
| `Daily/` | Daily notes |
| `Agents/` | Per-agent operational notes |

## Writing a session log

At the end of substantive work, write `Sessions/YYYY-MM-DD-<project>-<topic>.md`:

```markdown
---
date: YYYY-MM-DD
project: <project-name>
tags: [session-log, <project-name>]
---

# Session: <Brief Topic> (<project-name>, YYYY-MM-DD)

## Summary
<2-3 sentences on what was accomplished>

## Key Decisions
- <decision>

## Changes Made
- <change>

## Open Items
- <item>

## Next Steps
- <step>

---
## See Also
- [[Relevant Topic]]
```

Use **absolute paths**. Convert relative dates ("yesterday", "last week") to
absolute ones before writing — a log that says "tomorrow" is useless in a month.

## Rules that matter

- **Never modify existing note content inline.** Append to a `## See Also`
  footer with `[[WikiLinks]]`; leave the body alone. Someone else's notes are
  not yours to rewrite.
- **Topic notes are proper nouns only** — a person, project, tool, service, or
  organization. Not "debugging", not "testing".
- **Record decisions where they belong.** A decision made in chat that never
  reaches `Company/DECISIONS.md` did not happen.
- **Link liberally.** `[[Name]]` pointing at a note that does not exist yet is
  fine — it marks something worth writing, not an error.
- Do not read the whole vault at startup. Search, then read what matched.
