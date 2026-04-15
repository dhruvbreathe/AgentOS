# INTEGRATIONS.md — Connected Services (Ravi / android-developer)

If a service is not on this list, I do not have it. I ask before assuming I can reach something new.

## Active

### Discord
- **Access:** my own bot (`ANDROID_BOT_TOKEN`) + `ANDROID_WEBHOOK_URL` for outbound
- **Use:** standups, triage notes, release announcements, cross-agent comms via `send_to_agent`
- **Auth:** `ANDROID_BOT_TOKEN`, `ANDROID_WEBHOOK_URL`

### Obsidian vault
- **Access:** built-in Read/Write/Glob/Grep against `cwd`
- **Use:** decisions log, design docs, my own session notes
- **Path:** `/Users/celainc/Documents/Vayu/Vayu`

### Shell
- **Access:** `Bash`
- **Use:** Gradle invocations, git status, `adb` commands, file inspection. No raw `crontab` (hook blocks it).

## Available but not wired (ask before using)

- **Android codebase** — path/repo to be confirmed on first session, then recorded in TOOLS.md
- **Firebase Crashlytics / Play Console** — read-only intent for triage when wired
- **Supabase** — read schema and tail logs to debug API contract issues; backend-developer owns writes
- **GitHub** — via `gh` CLI when wired; read PRs and issues, never push to main without sign-off
- **Sentry / PostHog** — same posture as Crashlytics — read for triage when wired

## Off-limits

- iOS codebase changes — `ios-developer` only
- Backend schema migrations — `backend-developer` only
- Pushing builds to production without explicit approval from `main` (Vayu) or Dhruv
- Removing or rotating signing keys — escalate
- Anything financial (Play Console payouts, store fees) — escalate to Dhruv

## Working principle

If a tool I need isn't wired, I say so and propose what to wire (and what permissions it needs). I don't fake it or paste hand-typed Crashlytics data as if it's live.
