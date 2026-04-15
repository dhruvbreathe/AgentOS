# AgentOS

A per-channel Discord ↔ Claude agent relay built on the
[Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python).
Runs on a Claude.ai subscription (no API key required), with Obsidian as
the shared memory layer and macOS cron as the scheduler.

## Components

- **[discord-relay/](discord-relay/)** — the relay itself. One Discord channel
  per agent, each with its own system prompt, tools, skills, and cron tasks.
  Live-streams replies by editing a placeholder message as tokens arrive.

## Design

- **Channels as agents.** Each `agents/<name>/` folder is a self-contained
  agent — YAML config, system prompt, optional skill markdown files, optional
  MCP servers, optional scheduled tasks.
- **Claude CLI under the hood.** The SDK shells out to the bundled Claude
  CLI, which inherits your Claude.ai subscription auth.
- **Obsidian as memory.** Every agent's `cwd` points at the vault. Claude's
  built-in `Read`/`Write`/`Glob`/`Grep` tools *are* the memory layer — no
  bespoke store.
- **Cron = outbound.** Tasks under `agents/<name>/tasks/*.md` with a `cron:`
  frontmatter line are installed via `cron/install.py` and post their output
  to the agent's webhook.
- **Cross-agent comms.** `allow_bots: true` lets agents reply to each
  other's webhook posts.

See [discord-relay/README.md](discord-relay/README.md) for setup and usage.
