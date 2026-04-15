# discord-relay

A bidirectional relay between Discord bots and the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).
Each Discord channel is mapped to a distinct **agent** with its own system
prompt, tools, skills, and scheduled cron tasks. The relay uses the bundled
Claude CLI under the hood, so it runs on your Claude.ai subscription — no
API key required.

## Layout

```
discord-relay/
├── bot.py                  # long-running Discord listener
├── relay.py                # SDK runner + streaming sinks
├── cron_trigger.py         # cron entry: `python cron_trigger.py <agent> <task>`
├── agent_loader.py         # loads agents/*/agent.yaml into ClaudeAgentOptions
├── config.yaml             # global defaults
├── .env                    # secrets (copy from .env.example)
├── agents/
│   └── main/
│       ├── agent.yaml
│       ├── system_prompt.md
│       ├── skills/          # *.md appended to system prompt
│       └── tasks/           # *.md → cron-triggered prompts
├── cron/install.py         # generate/install crontab entries
└── logs/                   # session resume IDs, cron output
```

## Setup

```bash
cd discord-relay
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # fill in DISCORD_BOT_TOKEN, guild/webhook URLs
```

The SDK ships with the Claude CLI bundled. If you prefer your system-wide
install, set `cli_path` in `agent.yaml` (see the SDK README).

## Add an agent

1. `mkdir agents/<name>/{skills,tasks}` and copy `agents/main/agent.yaml`.
2. Fill in `channel_id` (right-click channel in Discord → Copy ID).
3. Set `webhook_url_env` to a new var like `<NAME>_WEBHOOK_URL`, add that
   var to `.env` with the channel's webhook URL.
4. Write `system_prompt.md` — role, style, constraints.
5. (Optional) Drop skill files in `skills/` — they are concatenated onto
   the system prompt at load time.
6. (Optional) Add MCP servers under `mcp_servers:` in `agent.yaml`.

## Run the bot (inbound: Discord → Claude → Discord)

```bash
python bot.py
```

Live streaming: the bot posts a `…` placeholder, then edits it every ~1.2s
as tokens arrive. Per-channel session IDs are stored in `logs/sessions.json`
so follow-ups in the same channel keep context.

## Outbound (cron → Claude → Discord webhook)

Each task is a markdown file under `agents/<agent>/tasks/<task>.md`. Add
a YAML frontmatter `cron:` line to schedule it:

```markdown
---
cron: 0 8 * * *
---
Produce the 8am daily digest...
```

Then:

```bash
python cron/install.py            # preview crontab block
python cron/install.py --apply    # merge into `crontab -l`
```

To run a task once manually:

```bash
python cron_trigger.py main daily_digest
```

## Memory (Obsidian)

Every agent's `cwd` defaults to `VAULT_PATH` from `.env`. Claude's built-in
`Read`/`Write`/`Glob`/`Grep` tools *are* the memory layer — there's no
bespoke store. System prompts reference `Sessions/` and `Topics/` so agents
naturally log and retrieve notes.

## Cross-agent comms

`allow_bots: true` in each agent's config lets agents reply to each other's
webhook posts — mirrors the OpenClaw pattern. Encourage agents to prefix
replies with `@<agent-name>` so routing stays legible.
