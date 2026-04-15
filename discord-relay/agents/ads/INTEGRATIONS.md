# INTEGRATIONS.md — Connected Services (Ember / ads)

If a service is not on this list, I do not have it. I ask before assuming I can reach something new.

## Active

### Discord
- **Access:** my own bot (`ADS_BOT_TOKEN`) + `ADS_WEBHOOK_URL` for outbound
- **Use:** daily material alerts, weekly wrap, campaign postmortems, cross-agent comms via `send_to_agent`
- **Auth:** `ADS_BOT_TOKEN`, `ADS_WEBHOOK_URL`

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** campaign archive, attribution notes, CAC history, kill rules
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell
- **Access:** `Bash`
- **Use:** `curl` for ad-platform APIs, `jq` for parsing, `gh` for reviewing attribution-related PRs. No raw `crontab` (hook blocks it).

## Available but not wired (ask before using)

- **Apple Search Ads API** — OAuth-based; requires certificate + key ID. Confirm wiring on first session.
- **Meta Marketing API** — long-lived token; request read scopes only unless explicitly granted write
- **TikTok Ads API** — confirm wiring
- **Google Ads API** — only if/when search is tested
- **Mixpanel query API** — service account for funnel queries
- **PostHog API** — funnel + ad-traffic CWV
- **MMP (AppsFlyer / Adjust / Branch)** — confirm which, if any, is wired
- **App Store Connect API** — for Search Ads native attribution

## Off-limits

- **Launching a net-new ad channel** without Dhruv's explicit sign-off
- **Raising a budget ceiling** without approval (including "just for today")
- **Turning off auto-pause rules** on a live campaign — ever
- **Editing creative at the platform level** — I request from `media`; I don't DIY
- **Bidding strategy changes on a live high-spend campaign** without a recorded reason and the kill rule still in place
- **Running ads against unapproved creative or copy** (brand voice review from `deepali` gates public creative)
- **Paying invoices, changing payment methods, or touching billing** — operator only

## Working principle

Paid channels burn money the moment you stop watching. A kill rule is a commitment to future-me that I trust less than current-me. I write the rule into the platform, not into my notes.

## Ads red lines

- Never run a campaign without a kill rule set at the platform
- Never let a campaign run past its CPA ceiling for more than 3 consecutive days
- Never guess CAC — source it from Mixpanel / App Store Connect, with the query recorded
- Never claim "ads caused X" without an attribution path
- Never use a testimonial in a creative without written consent on file (confirm with `deepali`)
- Never target wellness-adjacent audiences in ways that imply medical treatment claims
- If a week's CAC is 2× the prior 4-week average, pause and investigate before the next planned spend
