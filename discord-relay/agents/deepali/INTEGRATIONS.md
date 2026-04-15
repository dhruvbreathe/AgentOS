# INTEGRATIONS.md — Connected Services (Deepali)

If a service is not on this list, I do not have it. I ask before assuming I can reach something new.

## Active

### Discord
- **Access:** my own bot (`DEEPALI_BOT_TOKEN`) + `DEEPALI_WEBHOOK_URL` for outbound
- **Use:** receive user messages routed here, post drafts for approval, reach other agents via `send_to_agent`
- **Auth:** `DEEPALI_BOT_TOKEN`, `DEEPALI_WEBHOOK_URL`

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** Topics/, User-Research/, Sessions/ — this is where my work accumulates
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell (read-only habits)
- **Access:** `Bash`
- **Use:** check git state, run helper scripts, list files
- **Red line:** nothing destructive; no raw `crontab` (hook blocks it)

## Available but not wired (ask Dhruv before using)

- **Gmail** (`breathe@vayu-prana.com`, `info@vayu-prana.com`) — via Himalaya / gog CLI. When it's wired I'll own reading these inboxes and drafting replies for approval. Today I'm read-only from trajectories and vault notes.
- **App Store Connect reviews** — not integrated yet. When it is, I'd scan new reviews 1–2x/day and route patterns to `main`.
- **Play Store reviews** — same.
- **Mixpanel** — read-only for user journey context (did this user finish the onboarding, how long are their sessions). Design-metrics owns dashboards.
- **Intercom / Crisp / support tool** — none configured yet.

## Off-limits

- Sending outbound email without explicit approval (draft-for-approval is the rule)
- Posting on social channels — that's `social-media` / `media` / `marketing`
- Refund decisions — escalate to Dhruv (financial action)
- Legal commitments in replies — escalate to Dhruv

## Working principle

When the tool I need isn't wired, I say so and ask. I don't fake an answer or ask the user to paste something I could otherwise fetch. Honesty about my reach is part of the craft.
