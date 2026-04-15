# TOOLS.md — Local Notes (Sentry / security)

## Scopes I watch

- **Dev host:** the Mac running OpenClaw + AgentOS. `~/.openclaw/`, `~/Developers/`, `~/.local/bin`, `~/.ssh/` integrity.
- **Web prod:** `vayu-prana.com` on Vercel — HTTP headers, TLS config, build-artefact contents.
- **App stores:** iOS app + Android app — signing, entitlements, permission strings.
- **Supabase:** RLS policies, service-role token scope, auth flows. Backend-developer owns; I review.
- **Repos:** any file named `.env`, `secrets.properties`, `*-service-account.json`, `.npmrc`, `.pypirc`. Anything that looks like it might hold a secret.

## Scanner / tool surface

- `npm audit`, `pip-audit`, `gradle dependencies --scan`, `cocoapods outdated`
- `trivy` / `osv-scanner` for container + dep CVE cross-reference (install on demand; not assumed wired)
- `curl -I` + `securityheaders.com` for header drift check after each Vercel deploy
- `gitleaks` / `trufflehog` for repo secret scanning when I'm about to review a new repo
- `launchctl list` / `ps` for unexpected persistent processes on the dev host

Most of these aren't wired yet — I install on demand and record the command in `Topics/Security Playbook.md`.

## Obsidian vault (durable memory)

- **Security playbook:** `Topics/Security Playbook.md` (create if missing) — scanner commands, header baseline, rotation runbook
- **CVE log:** `Topics/CVE Log.md` (create if missing) — per-CVE entry with exploit path, status, fix SHA
- **Access audit:** `Topics/Access Audit.md` (create if missing) — tokens, OAuth scopes, bot tokens, webhook URLs with rotation cadence
- **Header baseline:** `Topics/Web Headers.md` (create if missing) — CSP / HSTS / etc. target values
- **Incidents:** `Sessions/YYYY-MM-DD-security-<incident>.md`
- **Decisions that affect posture:** `Company/DECISIONS.md`
- **My daily memory:** `agents/security/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/security/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/security/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1471886526198714449` (`#security`)
- **My Discord identity:** own bot (`bot_token_env: SECURITY_BOT_TOKEN`)
- **My webhook:** `SECURITY_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `main` (Vayu) | material or critical finding | `1469505325102006490` |
| `web-developer` (Indra) | header drift, CSP regression, web-side CVE | `1470278378077814804` |
| `backend-developer` | Supabase RLS / auth / service-account | `1471890585223954503` |
| `ios-developer` (Aria) | iOS supply-chain CVE, signing/entitlement concern | `1470499341763608681` |
| `android-developer` (Ravi) | Android supply-chain CVE, keystore concern | `1471023591033278484` |
| `qa` | any bug flavour I spot has a privacy/auth angle | `1470297479722565647` |
| `project-manager` (Tempo) | task state on a security fix with an SLA clock | `1470690373667127420` |

## Cadence

- **Daily:** host integrity check + CVE scan across known deps (noise-filtered; report only if non-clean)
- **After every web deploy:** header posture check on `vayu-prana.com` and `dashboard.vayu-prana.com`
- **Weekly:** workspace integrity sweep
- **Quarterly:** access audit (tokens, OAuth scopes, bot tokens, webhook URLs — rotate what's stale)

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
