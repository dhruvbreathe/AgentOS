---
name: skill-creator
description: Scaffold or revise a skill bundle. Use when the operator asks to create a new skill, tidy up an existing one, or audit a skill for the AgentOS spec. Triggers on phrases like "create a skill", "author a skill", "tidy up this skill", "improve this skill".
---

# skill-creator — build and maintain AgentOS skills

## What a skill is

A skill is a scoped markdown bundle appended to an agent's system prompt, giving the agent specialized procedural knowledge. Two tiers:

- **Shared skill:** `shared/skills/<name>/SKILL.md` — reusable across agents, referenced as `skill:<name>` in `agent.yaml`.
- **Per-agent skill:** `agents/<agent>/skills/<name>/SKILL.md` — private to one agent, referenced as `local:<name>`.

## Bundle layout

```
<name>/
├── SKILL.md         (required — frontmatter + body)
├── scripts/         (optional — executable code the skill references)
├── references/      (optional — docs to load on demand via Read)
└── assets/          (optional — templates, fonts, fixtures)
```

## SKILL.md contract

```markdown
---
name: <kebab-case-name>
description: <one-sentence trigger description — what it does + when to use it>
---

# <Human Title>

## When to reach for this
- concrete trigger 1
- concrete trigger 2

## How it works
<short, opinionated body. Code-ready examples beat prose.>

## What NOT to do
<failure modes. Be specific.>
```

## Principles (non-negotiable)

1. **Concise beats comprehensive.** Every paragraph competes with the rest of the system prompt. Cut what the model already knows.
2. **Show, don't explain.** Code blocks and concrete examples > abstract rules.
3. **Tell the agent when to skip you.** A skill that never defers is a skill that bloats every prompt.
4. **One job per skill.** If you're tempted to name it `utilities` or `helpers`, split it.

## Authoring flow

```bash
# Scaffold a new skill
./.venv/bin/python scripts/skill_create.py <name> --shared
./.venv/bin/python scripts/skill_create.py <name> --agent <agent-name>   # per-agent

# After editing SKILL.md:
./.venv/bin/python scripts/audit_skills.py   # runs the prompt-injection / exfil scan
```

Then reference in the target agent's `agent.yaml`:

```yaml
skills:
  - skill:<name>        # shared
  - local:<name>        # per-agent
```

## Audit checklist before shipping

- [ ] Frontmatter has `name:` (kebab-case) and `description:` (one sentence, under 200 chars)
- [ ] Description starts with a verb phrase the model can match against intent
- [ ] Body has a `When to reach for this` section with concrete triggers
- [ ] Body has at least one code example if the skill involves shell/tool use
- [ ] No "you are an expert" filler — the model already is
- [ ] No duplicate content already in `shared/HUMANIZER.md` / `EXPRESSION.md` / etc.
- [ ] Under ~300 lines (split into `references/` if longer)
- [ ] Any scripts in `scripts/` are marked executable and have shebangs

## Editing an existing skill

When asked to improve a skill, follow this order:

1. **Read the current SKILL.md** — identify what's there
2. **Run `scripts/audit_skills.py <name>`** — check it passes the audit
3. **Ask yourself: which parts never trigger?** Cut them
4. **Which concrete examples are missing?** Add them
5. **Rewrite the description** if it doesn't match real trigger phrases
6. **Preserve frontmatter `name:`** — changing it breaks every `skill:<old-name>` reference
