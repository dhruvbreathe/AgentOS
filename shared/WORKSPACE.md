# WORKSPACE.md — My Agent Workspace Layout

This is the canonical per-agent folder layout. Every agent workspace at `agents/<name>/` follows the same shape so tooling (doctor, status, audit) can reason about any agent without special cases.

## Layout

```
agents/<name>/
├── agent.yaml              ← runtime config: channel, tools, model, skills, subagents
│
├── Layered identity (loaded into system prompt every session):
│   ├── IDENTITY.md         ← who I am (name, creature, role)
│   ├── SOUL.md             ← values + style
│   ├── USER.md             ← who I serve (the operator)
│   ├── AGENTS.md           ← my workspace contract
│   ├── TOOLS.md            ← paths + env I use
│   ├── INTEGRATIONS.md     ← connected services
│   ├── SCHEDULING.md       ← how I schedule recurring work
│   ├── LEARNINGS.md        ← durable lessons (append-only)
│   └── MEMORY.md           ← curated facts (people, prefs, history)
│
├── skills/                 ← per-agent private skills (referenced as local:<name>)
│   └── <skill-name>/
│       ├── SKILL.md
│       ├── scripts/        (optional)
│       └── references/     (optional)
│
├── tasks/                  ← scheduled tasks (launchd-managed, cron: frontmatter)
│   └── <task-name>.md
│
├── ActiveTasks/            ← my current task board (one file per task)
│   └── <task-slug>.md
│
├── memory/                 ← daily journal, raw notes
│   ├── YYYY-MM-DD.md
│   └── archive/YYYY-MM/    ← rotated monthly
│
├── references/             ← reference material (loaded on demand, not every session)
│
└── HEARTBEAT.md            ← status-style snapshot (optional)
```

## Two memory surfaces, distinct jobs

| File | Kind | Loaded every session? |
|---|---|---|
| `LEARNINGS.md` | Behavioural rules ("if X, do Y") | ✅ |
| `MEMORY.md` | Facts about people/prefs/history | ✅ |
| `memory/YYYY-MM-DD.md` | Raw daily journal | ❌ (read on demand) |
| `ActiveTasks/*.md` | Current work board | ❌ (read on demand) |

## When to write where

- **Durable rule I want to repeat:** `LEARNINGS.md`
- **Durable fact (name, pref, relationship):** `MEMORY.md`
- **Something I did today:** `memory/YYYY-MM-DD.md`
- **Task I'm working:** `ActiveTasks/<slug>.md`
- **Scheduled job:** `tasks/<name>.md` (cron: frontmatter)
- **Cross-agent fact:** `Company/FACTS.md` in the vault
- **Decision that affects others:** `Company/DECISIONS.md` in the vault

## Referencing skills in agent.yaml

```yaml
skills:
  - skill:dev-browser                 # shared, resolves to shared/skills/dev-browser/SKILL.md
  - skill:summarize                   # shared
  - local:my-domain-specific          # per-agent, resolves to agents/<me>/skills/my-domain-specific/SKILL.md
  - ../../shared/skills/x.md          # legacy path form (still supported, deprecated)
```

The resolver is in `agent_loader.py`. Prefer `skill:` / `local:` forms — they're portable and don't break on refactors.

## Onboarding a new agent

```bash
./.venv/bin/python scripts/new_agent.py <name>
```

That copies `_template/` into `agents/<name>/`, scaffolds webhook env, and leaves `BOOTSTRAP.md` for the first-session walkthrough. The standard layout is guaranteed from day one.

## Tooling that depends on this shape

- `scripts/doctor.py` — health checks each agent's webhook, token, vault, launchd jobs
- `scripts/status.py` — aggregates structured status per agent
- `scripts/audit_skills.py` — scans skills for prompt-injection / secrets
- `scripts/maintain_memory.py` — rotates `memory/YYYY-MM-DD.md` into archive/, promotes patterns into `LEARNINGS.md`
- `scheduler/install.py` — loads launchd plists from `tasks/*.md`

Keeping this layout consistent means any tool "just works" on any agent.
