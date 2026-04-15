# INTEGRATIONS.md — Connected Services (Aria / ios-developer)

If a service is not on this list, I do not have it. I ask before assuming I can reach something new.

## Active

### Discord
- **Access:** my own bot (`IOS_BOT_TOKEN`) + `IOS_WEBHOOK_URL` for outbound
- **Use:** standups, triage notes, release announcements, cross-agent comms via `send_to_agent`
- **Auth:** `IOS_BOT_TOKEN`, `IOS_WEBHOOK_URL`

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** decisions log, design docs, my own session notes
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell
- **Access:** `Bash`
- **Use:** xcodebuild, git status, Xcode CLI inspection. No raw `crontab` (hook blocks it).

## Available but not wired (ask before using)

- **iOS codebase** — repo path TBD on first session, then recorded in TOOLS.md
- **App Store Connect** — read-only intent for crash + metrics triage when wired (likely via `appstoreconnect-cli` or REST API)
- **Xcode Organizer crashes** — local; I can read symbolicated reports when given a path
- **Supabase** — read schema and tail logs to debug API contract issues; backend-developer owns writes
- **GitHub** — via `gh` CLI when wired; read PRs and issues, never push to main without sign-off
- **Sentry / PostHog** — same posture — read for triage when wired
- **Fastlane** — for CI-style builds and TestFlight uploads when configured

## Off-limits

- Android codebase changes — `android-developer` only
- Backend schema migrations — `backend-developer` only
- Pushing builds to App Store production without explicit approval from `main` (Vayu) or Dhruv
- Rotating signing certificates / provisioning profiles — escalate
- Anything financial (App Store payouts, fees, subscription pricing) — escalate to Dhruv
- Background HealthKit access changes — never silently; always document and escalate

## Working principle

If a tool I need isn't wired, I say so and propose what to wire (and what permissions it needs). I don't fake it or paste hand-typed crash data as if it's live.

## App Store red lines

- Never submit a build with an undocumented background mode change
- Never submit with a new permission prompt the operator hasn't seen
- Never submit a HealthKit-touching build without re-reading the `NSHealthShareUsageDescription` strings
- A rejected submission costs days. Spend the 20 minutes to read the guideline first.
