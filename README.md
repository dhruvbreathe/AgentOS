# AgentOS

A per-channel Discord ↔ Claude agent relay built on the
[Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).
Runs on a Claude.ai subscription (no API key required), with any folder of
markdown files (Obsidian works well) as the shared memory layer and macOS
launchd as the scheduler.

> **Status: early public release (v0.1).** Works on macOS today. The bundled
> launchd scheduler is macOS-only; everything else is portable.

## What it gives you

- **One Discord channel per agent.** Each `agents/<name>/` folder is a
  self-contained agent — YAML config, layered system prompt, per-agent
  skills, per-agent tools/integrations, and optional scheduled tasks.
- **Live-streaming replies.** The bot posts a `…` placeholder and edits it
  every ~1.2s as tokens arrive, so long answers feel instant.
- **Cross-agent comms.** `allow_bots: true` lets agents reply to each
  other's webhook posts. Encourage `@<agent-name>` prefixes to keep routing
  legible.
- **Markdown-as-memory.** Every agent's `cwd` points at your vault. Claude's
  built-in `Read`/`Write`/`Glob`/`Grep` tools *are* the memory layer — no
  bespoke store.
- **Scheduled tasks.** Drop a `cron:` frontmatter line on any
  `agents/<name>/tasks/*.md` file and the scheduler installs a launchd
  plist that fires the task and posts its output to the agent's webhook.
- **Operator-reaction gates.** React `💾` on any message to save the turn
  to the vault. Dangerous `Bash` commands trigger a `✅/❌` approval prompt
  before executing.
- **An optional web dashboard** for watching agents live (`dashboard.py`),
  plus a trajectory log per session for post-hoc review.

## Quickstart

```bash
git clone https://github.com/dhruvadhia1/AgentOS.git
cd AgentOS
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dashboard]"
cp .env.example .env        # fill in DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, VAULT_PATH
```

The Claude Agent SDK ships with the Claude CLI bundled. If you prefer your
system-wide install, set `cli_path` in an agent's `agent.yaml`.

### Run the bot (inbound: Discord → Claude → Discord)

```bash
python bot.py
# or, to auto-restart on crash / restart-request:
scripts/autorestart.sh
```

Per-channel session IDs are stored in `logs/sessions.json` so follow-ups in
the same channel keep context.

### Outbound (scheduler → Claude → Discord webhook)

Each scheduled task is a markdown file under `agents/<agent>/tasks/<task>.md`
with a `cron:` frontmatter line:

```markdown
---
cron: 0 8 * * *
---
Produce the 8am daily digest...
```

Install / refresh all schedules:

```bash
python scheduler/install.py             # dry-run — print plan
python scheduler/install.py --apply     # install & load launchd plists
python scheduler/install.py --list      # show currently-loaded jobs
python scheduler/install.py --remove-all
```

To run a task once manually:

```bash
python cron_trigger.py <agent> <task>
```

## Adding an agent

1. `cp -r agents/_template agents/<name>` and edit `agent.yaml`.
2. Fill in `channel_id` (right-click channel in Discord → Copy ID).
3. Set `webhook_url_env` to a var like `<NAME>_WEBHOOK_URL`; add that var to
   `.env` with the channel's webhook URL (`Channel → Integrations → Webhooks
   → New`).
4. Edit the system-prompt layers (`IDENTITY.md`, `SOUL.md`, `AGENTS.md`,
   `TOOLS.md`, `INTEGRATIONS.md`, `SCHEDULING.md`, `LEARNINGS.md`,
   `MEMORY.md`) — or point `system_prompt_file` at a single file.
5. Drop skill files in `skills/` (each `skills/<name>/SKILL.md` is appended
   to the system prompt at load time).
6. Optional: add MCP servers under `mcp_servers:` in `agent.yaml`.

Any path in an agent's `.md` docs that needs the repo root should use the
`{AGENTOS_ROOT}` placeholder — `agent_loader.py` resolves it to the current
install path at load time, keeping docs portable across machines.

## Layout

```
.
├── bot.py                  # long-running Discord listener
├── relay.py                # SDK runner + streaming sinks
├── cron_trigger.py         # one-shot task runner
├── agent_loader.py         # loads agents/*/agent.yaml + layered prompts
├── agent_tools.py          # SDK-custom tools (post-to-discord, etc.)
├── approval_gate.py        # Bash pre-tool-use approval hook
├── save_marker.py          # 💾 reaction → save-turn-to-vault handler
├── events.py               # cross-process event bus
├── dashboard.py            # optional FastAPI UI for watching agents live
├── tasks.py, tasks_routes.py
├── web_chat.py             # optional browser-based chat UI
├── transcribe.py           # audio-message transcription helper
├── config.yaml             # global defaults + save/stream/approval gates
├── .env.example
├── agents/
│   ├── _template/          # copy this to seed a new agent
│   └── ...                 # your agents
├── shared/                 # cross-agent prompt fragments + skills
├── scripts/                # doctor, audit, seed_tasks, launch_dashboard, etc.
├── scheduler/install.py    # launchd plist generator
├── cron/install.py         # legacy crontab installer (kept for reference)
└── connectors/             # per-integration HTTP wrappers
```

## Requirements

- Python 3.10+
- macOS (for the launchd scheduler; everything else is cross-platform)
- A Claude.ai account (the SDK authenticates via the bundled CLI)
- A Discord server you can create bots and webhooks in

## Credits

Built on the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
from Anthropic. The humanizer / caveman / expression prompt fragments under
`shared/` are adapted from their respective authors — see the file headers
for provenance.

## License

MIT — see [LICENSE](LICENSE).
