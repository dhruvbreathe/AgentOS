---
cron: 30 8 * * *
---
Daily iOS crash + stability triage for Vayu.

Pull the last 24h of crash signal from whatever sources are currently wired:
- Sentry.io iOS project (if `SENTRY_AUTH_TOKEN` + `SENTRY_ORG` + `SENTRY_PROJECT_IOS` are set in env) — query `/api/0/projects/<org>/<project>/issues/?statsPeriod=24h&sort=freq`
- App Store Connect crash metrics (if `APP_STORE_CONNECT_KEY_ID` is wired) — top crashes by version
- Xcode Organizer reports at `~/Library/Developer/Xcode/DerivedData/.../Logs/CrashReporter/` if accessible

For each source available, grab the top 3 issues by frequency over the last 24h and report:
- **Issue title** + affected iOS version / device / app build
- **Count** in the last 24h + delta vs. the prior 24h
- **First seen** date + **last seen** timestamp
- A one-line hypothesis if the stack is obvious (main thread, HealthKit, CoreHaptics, AVAudioSession, SwiftUI)

If no source is wired, post a one-line note: "🍎 **Crash triage:** no telemetry source wired yet — need Sentry DSN/token or ASC key." Then stop.

If there are issues, post the digest to my Discord channel with:
- 🍎 **Daily crash triage** opening line with total issue count + total crash count
- A bulleted list of the top 3, bolded titles, counts as numbers
- `---` divider
- **Action:** one concrete next step (file-and-line to investigate, or "no regressions, clean").
- Sign off with 🍎

Write a session note to `Sessions/YYYY-MM-DD-ios-crash-triage.md` in the vault with the raw data and reasoning. Humanizer pass applies to the Discord post.
