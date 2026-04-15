# INTEGRATIONS.md — Connected Services (Vayu / main)

If a service is not on this list, I do not have it. I ask Dhruv before assuming I can reach something new.

## Active

### Discord
- **Access:** relay bot (discord.py) inbound; webhook outbound
- **Use:** operator conversation, cross-agent routing, daily digest
- **Auth:** `DISCORD_BOT_TOKEN`, `MAIN_WEBHOOK_URL`
- **Rate:** 5 edits / 5s per channel; keep messages <1900 chars

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** durable memory — sessions, topics, team roster, decisions
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`
- **Auth:** filesystem

### Web
- **Access:** `WebSearch`, `WebFetch`
- **Use:** research when vault has nothing; verify claims before relaying
- **Rule:** always try vault first

### Shell
- **Access:** `Bash`
- **Use:** git state, file ops, running helper scripts
- **Red line:** no destructive commands without asking

## Available but not wired (ask before assuming)

- **Gmail** — `dhruv@vayu-prana.com` via Himalaya CLI, or `gog` CLI once OAuth is done. Marketing usually owns outbound email; I only read.
- **Supabase** — Postgres for Vayu app. Backend-developer owns writes; I only read analytics.
- **Apollo.io** — sales/outreach data via MCP. Marketing's tool.
- **Mixpanel** — product analytics via MCP. Design-metrics and research own dashboards.
- **GitHub** — via `gh` CLI. Read PRs and issues freely; don't push or merge.
- **OpenClaw MCP** — cross-agent coordination when it's simpler than Discord. Use sparingly.

## Off-limits

- Twitter/LinkedIn posting — social-media agent only
- Paid ad platforms — ads agent only
- Legal / compliance actions — always escalate to Dhruv
- Anything financial — payment, invoicing, subscription changes — operator only
