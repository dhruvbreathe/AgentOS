# TOOLS.md — Local Notes (Aria / ios-developer)

## Codebase

- **Repo:** Vayu iOS (path TBD — confirm with Dhruv on first session and record here)
- **Stack:** Swift 5.x+, SwiftUI primary, UIKit where required
- **Concurrency:** async/await; Combine where reactive flows already exist
- **Persistence:** SwiftData / Core Data — confirm which on first session
- **Frameworks:** HealthKit, CoreHaptics, AVFoundation, WatchKit (companion)
- **Min iOS:** confirm in Xcode project settings; record here
- **Distribution:** TestFlight internal → external → App Store
- **Crash + metrics:** Xcode Organizer, App Store Connect Metrics

## Obsidian vault (my durable memory)

- **Topics:** `Topics/Vayu iOS.md` (create if missing) — design decisions, gotchas, dependency choices
- **Sessions:** `Sessions/YYYY-MM-DD-ios-<topic>.md`
- **Decisions log:** `Company/DECISIONS.md` — read before shipping anything material
- **HealthKit privacy posture:** look for any prior notes in `Topics/HealthKit.md` or `Company/DECISIONS.md`; this is a sensitive surface
- **My daily memory:** `agents/ios-developer/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/ios-developer/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/ios-developer/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1470499341763608681` (`#ios-developer`)
- **My Discord identity:** own bot (`bot_token_env: IOS_BOT_TOKEN`)
- **My webhook:** `IOS_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | strategic / scope decisions, release timing, App Store risk calls | `1469505325102006490` |
| `android-developer` (Ravi) | feature parity check, shared UX language | `1471023591033278484` |
| `backend-developer` | API contract change, schema question | `1471890585223954503` |
| `qa` | repro steps, TestFlight builds for fix-confirm | `1470297479722565647` |
| `ui-ux-designer` | spec ambiguity, iOS-native deviation from Figma | `1472412741795840120` |
| `project-manager` (Tempo) | task state updates only | `1470690373667127420` |
| `media` (Pixel) | builds for marketing capture | `1469500272802926653` |

## Local environment habits

- Latest stable Xcode + matching command line tools
- Physical test devices: confirm with Dhruv (typically a current iPhone + an older one + an Apple Watch)
- iOS Simulator for breadth, physical device for the final pass before TestFlight promotion
- Console.app filtered by bundle id when triaging — paste filtered output, not the firehose
- HealthKit testing: physical device only (Simulator HealthKit is shallow)

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
