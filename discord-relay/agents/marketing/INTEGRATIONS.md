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
- **Use:** read inbox for reply triage. Read-only.
- **Accounts wired:** `dhruv`, `info` (per 2026-02-13 fix). `breathe` and `deepali` may be wired separately — confirm.

### Apollo.io (PRIMARY OUTREACH CHANNEL)
- **Access:** parent MCP tools `mcp__claude_ai_Apollo_io__*`
- **Use:** source + verify prospects, enroll into emailer campaigns — **Apollo is how we send**, not Gmail drafts. Connected mailbox on Apollo side does the actual delivery on the sequence schedule.
- **Key tools:**
  - `apollo_mixed_people_api_search` — prospect search
  - `apollo_people_match` / `apollo_people_bulk_match` — verify email (`email_status: verified` required)
  - `apollo_mixed_companies_search` / `apollo_organizations_enrich` — account research
  - `apollo_contacts_create` / `apollo_contacts_update` — CRM side
  - `apollo_emailer_campaigns_search` — find the live sequence
  - `apollo_emailer_campaigns_add_contact_ids` — enroll into sequence (the send)
  - `apollo_emailer_campaigns_remove_or_stop_contact_ids` — pull someone out
  - `apollo_email_accounts_index` — confirm sending mailbox
- **Autonomy:** per 2026-04-19 grant, daily batch enrolls without per-email approval. Hard requirement: every contact `email_status: verified` before enrollment. No pattern-guessed addresses.

### Gmail (parent MCP)
- **Access:** `mcp__claude_ai_Gmail__*` — create_draft, search_threads, get_thread, labels
- **Use:** reply triage + drafting inbound replies. One-off personal replies when Apollo sequence isn't the right surface. Not the primary outbound channel.

## Available but not wired (ask before assuming)

- **gog CLI** — queued. Full Gmail/Calendar/Drive/Contacts once OAuth done. Browser flow needed; Dhruv to do in person on Mac Studio.
- **Mixpanel** — read-only intent for reply→meeting funnel attribution (tools now loaded via parent MCP). Design-metrics owns dashboards.

## Off-limits

- **Outbound outside Apollo sequences or approved Gmail replies.** No ad-hoc sends from other accounts.
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
