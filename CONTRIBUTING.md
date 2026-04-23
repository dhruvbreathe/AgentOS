# Contributing to AgentOS

AgentOS is a thin framework around the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
for running per-channel Discord ↔ Claude agents with Obsidian as the shared
memory layer. The design goal is to stay small, readable, and hackable —
contributions that align with that goal are welcome.

## Getting set up

```bash
git clone https://github.com/dhruvadhia1/AgentOS
cd AgentOS
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dashboard,dev]"
cp .env.example .env    # fill in DISCORD_BOT_TOKEN, VAULT_PATH, webhook URLs
```

See [README.md](README.md) for a full walk-through.

## What makes a good PR

- **One logical change per PR.** Refactors, features, and bug fixes should not
  be bundled.
- **Tests or a reproducible demo** for new behaviour.
- **No hardcoded personal paths.** Use `VAULT_PATH`, `{AGENTOS_ROOT}`, or
  `Path(__file__).parent` — never absolute paths that only work on your
  machine.
- **No private data.** Agent memory, session logs, and credentials live
  outside of version control by policy (see `.gitignore`). Do not commit
  anything under `agents/*/memory/`, `logs/`, `certs/`, or `.env*`.

## Adding an agent template

The `_template/` agent under `agents/` is the starting point. Its `.md` files
use `{AGENTOS_ROOT}` as a placeholder that gets resolved at runtime by
`agent_loader.py`. If your template references paths, use this placeholder
so it stays portable.

## Code style

- Python 3.10+, type hints where they add clarity, no comment spam.
- `ruff` and `ruff format` are the linters of record.
- Prefer small, pure functions over deep class hierarchies.

## Reporting bugs

Please include:
- OS + Python version
- The agent config (with secrets scrubbed)
- `logs/bot.log` tail covering the error
- A minimum reproduction if possible
