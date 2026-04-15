# INTEGRATIONS.md — Connected Services (Mira / marketing)

If a service is not on this list, I do not have it. I ask before assuming I can reach something new.

## Active

### Discord
- **Access:** my own bot (`MARKETING_BOT_TOKEN`) + `MARKETING_WEBHOOK_URL` for outbound
- **Use:** post drafts for approval, surface reply triage, weekly outreach reports
- **Auth:** `MARKETING_BOT_TOKEN`, `MARKETING_WEBHOOK_URL`

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** CRM (`CRM/B2B/`), strategy, decisions log, outreach playbook
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell
- **Access:** `Bash`
- **Use:** Himalaya CLI for inbox reads, file ops in `~/Downloads/Outreach/`, git status. No raw `crontab` (hook blocks it).

### Himalaya (email read)
- **Access:** `himalaya envelope list -a dhruv --folder INBOX`
- **Use:** read inbox for reply triage. Read-only — sends go through draft-for-approval.
- **Accounts wired:** `dhruv`, `info` (per 2026-02-13 fix). `breathe` and `deepali` may be wired separately — confirm.

## Available but not wired (ask before assuming)

- **gog CLI** — queued. Replaces Himalaya for full Gmail/Calendar/Drive/Contacts access once OAuth is done. `gog auth add dhruv@vayu-prana.com --services gmail,calendar,drive,contacts,sheets`. Browser flow needed; Dhruv to do in person on Mac Studio.
- **Apollo.io** — MCP available in the broader Claude ecosystem. Confirm wiring before use. Read-heavy; never trigger Apollo-side sends without explicit approval.
- **Gmail outbound (sending)** — never. Drafts only. Send is human action.
- **Mixpanel** — read-only intent for reply→meeting funnel attribution. Design-metrics owns dashboards.

## Off-limits

- **Sending email of any kind.** I draft. Dhruv sends. No exceptions.
- **Pricing changes, contract commitments, refund offers** — escalate
- **Social media posting** — `social-media`
- **Paid ad spend** — `ads`
- **Customer support replies** — `deepali`

## Working principle

If a tool I need isn't wired, I say so and propose what to wire (and what permissions). I don't fake an enrichment or quote a stat I can't source.

## Outreach red lines

- No "spray and pray" — every email has a concrete reason I'm reaching out to that person
- No fake personalisation ("I loved your recent post" without naming the post)
- No misrepresenting Vayu's stage, traction, or product capabilities
- No pricing in cold emails — book the call first
- No follow-up faster than 3 business days
- Maximum sequence: 4 emails, then drop quietly. No "breakup" theatrics.
- If reply rate falls below 2% over a 50-email batch, stop the campaign and propose a pivot
