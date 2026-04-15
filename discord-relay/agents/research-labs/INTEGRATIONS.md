# INTEGRATIONS.md — Connected Services

Declarative list of which external services this agent can reach and how. If it's not here, you don't have it.

## Pattern

Each integration has:
- **Name** — service identifier
- **How I access it** — MCP server name, CLI, Python lib, webhook
- **What it's for** — the use case
- **Auth** — where credentials live (env var name, not the value)
- **Rate / cost notes** — if relevant

## Starter integrations (fill in only what this agent actually uses)

### Discord

- **Access:** discord.py via the relay bot (inbound), webhook for outbound
- **Use:** talk to the operator and other agents in my channel
- **Auth:** `DISCORD_BOT_TOKEN` (bot), `<NAME>_WEBHOOK_URL` (outbound)
- **Notes:** 5 message edits / 5 seconds per channel. Stay under 1900 chars.

### Obsidian (via cwd)

- **Access:** built-in `Read`/`Write`/`Glob`/`Grep` tools; cwd is the vault
- **Use:** durable memory, team roster, decisions, topic notes
- **Auth:** filesystem permissions

### (Add only if granted)

- **Gmail** — via Himalaya CLI or gog; account: `dhruv@vayu-prana.com`
- **Supabase** — Postgres + auth for Vayu app
- **Apollo.io** — sales/outreach data
- **Mixpanel** — product analytics
- **Sentry / PostHog** — errors / product events

---

**Rule:** if an integration is listed here, you can use it. If it's not, don't invent it. Ask the operator to add it first.
