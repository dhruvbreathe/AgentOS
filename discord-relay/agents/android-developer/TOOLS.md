# TOOLS.md — Local Notes (Ravi / android-developer)

## Codebase

- **Repo:** Vayu Android (path TBD — confirm with Dhruv on first session and record here)
- **Stack:** Kotlin, Jetpack Compose, MVVM, Hilt, Room, Retrofit, Coroutines + Flow
- **Min SDK:** Android 8 (API 26) unless changed in `build.gradle.kts`
- **Target SDK:** latest stable Android API
- **Build system:** Gradle (Kotlin DSL)
- **Distribution:** Play Store (internal → closed → open beta → production)
- **Crash + vitals:** Firebase Crashlytics, Play Console vitals dashboard

## Obsidian vault (my durable memory)

- **Topics:** `Topics/Vayu Android.md` (create if missing) — design decisions, gotchas, dependency choices
- **Sessions:** `Sessions/YYYY-MM-DD-android-<topic>.md`
- **Decisions log:** `Company/DECISIONS.md` — read before shipping anything material
- **My daily memory:** `agents/android-developer/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/android-developer/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/android-developer/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1471023591033278484` (`#android-developer`)
- **My Discord identity:** own bot (`bot_token_env: ANDROID_BOT_TOKEN`)
- **My webhook:** `ANDROID_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | strategic / scope decisions, release timing | `1469505325102006490` |
| `ios-developer` | feature parity check, shared UX language | `1470499341763608681` |
| `backend-developer` | API contract change, schema question | `1471890585223954503` |
| `qa` | repro steps, fix-confirm builds | `1470297479722565647` |
| `ui-ux-designer` | spec ambiguity, Material 3 deviation | `1472412741795840120` |
| `project-manager` (Tempo) | task state updates only | `1470690373667127420` |
| `media` (Pixel) | builds for marketing capture | `1469500272802926653` |

## Local environment habits

- Android Studio current stable
- Physical test devices: confirm with Dhruv (typically a Pixel + an older mid-range)
- Wear emulator for WearOS work; physical Wear device for the final pass before promotion
- `adb logcat` filtered by package id when triaging — paste filtered output, not the firehose

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
