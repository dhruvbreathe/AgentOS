# TOOLS.md — Local Notes (Pixel / media)

## Stack

- **AI image:** Midjourney, SDXL (with ADETAILER / ControlNet), Flux — pick the right model for the brief
- **AI video:** Runway Gen-4, Sora, Veo — pick based on shot length and realism target
- **Voice / TTS:** ElevenLabs (if wired); native macOS voices as fallback
- **NLE:** confirm on first session — likely Premiere / Final Cut / CapCut / DaVinci
- **Image editor:** Photoshop / Pixelmator / Affinity — confirm
- **Vector:** Figma for layout; Illustrator / Affinity for vector work

## Asset library (confirm path on first session)

- **Source assets:** likely under `~/Dropbox/Vayu/Media/` or `~/Documents/Vayu/Media/` — record canonical path here
- **Final renders:** same root, `finals/YYYY-MM/` subfolders
- **Raw captures:** `raw/` subfolders by shoot date
- **Brand guide:** Figma link + `Topics/Brand Guide.md` in the vault
- **Consent forms:** `~/Documents/Vayu/Legal/Consent/` — never post anyone whose consent isn't on file

## Platform output specs

| Surface | Spec |
|---|---|
| Instagram Reels | 1080×1920, 9:16, <90s |
| TikTok | 1080×1920, 9:16, <60s for feed; up to 10m possible |
| YouTube Shorts | 1080×1920, 9:16, <60s |
| X (feed) | 1:1 square for images, 16:9 for video |
| LinkedIn | 1:1 square preferred |
| OG image | 1200×630, 1.91:1 |
| App Store screenshots | per device frame — iPhone 6.9" / 6.5" etc. |

## Obsidian vault (durable memory)

- **Brand guide:** `Topics/Brand Guide.md`
- **Visual decisions:** `Company/DECISIONS.md` — colour, type, identity calls
- **Campaign archive:** `Topics/Campaigns/YYYY-MM-<slug>.md`
- **Prompt library:** `Topics/Pixel Prompt Library.md` (create if missing) — the prompts that produced on-brand results, so I don't re-derive
- **Sessions:** `Sessions/YYYY-MM-DD-media-<topic>.md`
- **My daily memory:** `agents/media/memory/YYYY-MM-DD.md`
- **My durable lessons:** `agents/media/LEARNINGS.md`
- **My trajectories:** `logs/trajectories/media/<session_id>.jsonl`

## Discord

- **Guild:** `1469395433360195777` (Vayu Prana Labs)
- **My channel:** `1469500272802926653` (`#media`)
- **My Discord identity:** own bot (`bot_token_env: MEDIA_BOT_TOKEN`)
- **My webhook:** `MEDIA_WEBHOOK_URL`

## Cross-agent routes I use

| Who | When I route to them | Channel |
|---|---|---|
| `marketing` (Mira) | asset delivered, awaiting review; brief clarification | `1469778689322647800` |
| `social-media` | asset ready for platform post | `1471339567071101045` |
| `ads` | ad-variant set ready for testing | `1471218500969435156` |
| `web-developer` (Indra) | OG / hero / featured image ready for commit | `1470278378077814804` |
| `deepali` (CDO) | brand-voice review before shipping public | `1469503216545693766` |
| `main` (Vayu) | strategic campaign delivery, major launch asset | `1469505325102006490` |
| `reddit-crawler` (Rook) | visual explainer for a thread | `1471675794844680212` |
| `ios-developer` / `android-developer` | app-store screenshots, WearOS renders | iOS `1470499341763608681` · Android `1471023591033278484` |
| `project-manager` (Tempo) | task state | `1470690373667127420` |

## Runtime baseline

- Model: `claude-opus-4-6` (CLI default)
- Timezone: Pacific (Vancouver)
