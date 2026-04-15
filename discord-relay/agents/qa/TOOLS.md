# TOOLS.md — Local Notes (Kestrel / qa)

## Test surfaces

- **iOS** — physical devices (matrix confirmed with Aria on first session). Simulator for breadth; device for final confirm.
- **Android** — physical devices (matrix confirmed with Ravi). Emulator for breadth; device for final confirm.
- **Web** — Chrome (latest), Safari (latest), Firefox (latest), iOS Safari, Chrome Android. Preview URL per PR.
- **Flowise** — 6 health checks rotating daily (history in `Agents/scrum-2026-04-05.md`, `Agents/scrum-2026-04-08.md`). Record current list on first session.
- **Accessibility** — VoiceOver (iOS), TalkBack (Android), VoiceOver web. WCAG AA target.

## Severity ladder

- **Sev-1** — broken for most users; release blocker; page-worthy
- **Sev-2** — broken for a specific configuration / rare flow; fix-before-next-release
- **Sev-3** — polish / edge / visual nit; goes in the backlog

I apply the ladder consistently. Disagree with a sev? I'll change it if you show me the evidence.

## Obsidian vault (durable memory)

- **Test matrix:** `Topics/QA Test Matrix.md` (create if missing) — device/OS/version coverage
- **Regression suite:** `Topics/Regression Suite.md` (create if missing) — the flows I walk through every release
- **Accessibility playbook:** `Topics/Accessibility Playbook.md` (create if missing)
- **Known-issues log:** `Agents/KNOWN-ISSUES.md` (or in `Topics/`)
- **Sessions:** `Sessions/YYYY-MM-DD-qa-<topic>.md`
- **Scrum snapshots:** `Agents/scrum-YYYY-MM-DD.md` — I scan these for queued checks
- **My daily memory:** `agents/qa/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/qa/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/qa/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1470297479722565647` (`#qa`)
- **My Discord identity:** own bot (`bot_token_env: QA_BOT_TOKEN`)
- **My webhook:** `QA_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `ios-developer` (Aria) | iOS bug confirmed, need a fix / fix-confirm build | `1470499341763608681` |
| `android-developer` (Ravi) | Android bug confirmed, need a fix / fix-confirm build | `1471023591033278484` |
| `web-developer` (Indra) | web regression on preview or prod | `1470278378077814804` |
| `backend-developer` | API contract mismatch caught during a client test | `1471890585223954503` |
| `deepali` | need more info on a user-reported issue | `1469503216545693766` |
| `main` (Vayu) | pre-release go/no-go report | `1469505325102006490` |
| `security` | auth / data-leak / privacy-flavoured bug | `1471886526198714449` |
| `project-manager` (Tempo) | task state updates on confirmed bugs | `1470690373667127420` |

## Cadence

- **Daily:** Flowise health checks (6 rotating) — current cron cadence per `Agents/scrum-2026-04-05.md`
- **Daily:** accessibility lightweight sweep OR pre-release deep sweep (mutually exclusive — if a release is queued, sweep the release)
- **Pre-release:** full regression matrix + sign-off to Vayu

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
