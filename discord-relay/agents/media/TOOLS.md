# TOOLS.md — Local Notes (Pixel / media)

## Stack

- **AI image:** Midjourney, SDXL (with ADETAILER / ControlNet), Flux — pick the right model for the brief
- **AI video (generative plates):** Veo 3 via Gemini API (confirmed working — model `veo-3.0-generate-001`, 9:16, ~$0.50/clip, ~60s wall time). Runway Gen-4, Sora available but unwired.
- **Motion / compose (STANDARD — adopted 2026-04-17):** [Hyperframes](https://github.com/heygen-com/hyperframes) — HTML + GSAP + Puppeteer + ffmpeg. `npx hyperframes init|preview|lint|render`. No API keys, fully local. Beat raw ffmpeg in side-by-side test on the 50k brand film. Reference project lives at `/Users/celainc/Developers/vayu-media/50k-celebration/`.
- **Raw ffmpeg:** reserved for simple concat/trim/mux. When there's typography, counters, or any UI-style motion — use Hyperframes.
- **Voice / TTS:** Gemini 2.5 Flash TTS (`gemini-2.5-flash-preview-tts`, confirmed — voice "Charon" for calm/weathered; pass text directly, never prepend speech direction or it stretches the audio 3×). ElevenLabs available if wired.
- **NLE:** confirm on first session — likely Premiere / Final Cut / CapCut / DaVinci
- **Image editor:** Photoshop / Pixelmator / Affinity — confirm
- **Vector:** Figma for layout; Illustrator / Affinity for vector work

## Hyperframes idioms (the gotchas I hit)

- `<video>` elements CAN'T be direct children of a root that has `data-start`. Either drop `data-start` from root, or wrap the video in a sub-composition (`data-composition-id=... data-composition-src=...`).
- Every timed element needs `data-start`, `data-duration`, `data-track-index` AND `class="clip"`. Missing any = silent failure.
- Timelines must be `gsap.timeline({ paused: true })` and registered on `window.__timelines[<composition-id>]`.
- Videos need `muted` + separate `<audio>` tracks if you want sound — Veo's native audio is ignored unless you extract it.
- GSAP tween overlap warnings matter — add `overwrite: "auto"` or restructure.
- `npx hyperframes lint` ALWAYS before render. `--strict` fails on lint errors.
- Available example templates: `warm-grain, play-mode, swiss-grid, vignelli, kinetic-type, product-promo, cinematic-zoom, logo-outro, grain-overlay, shimmer-sweep, app-showcase, ui-3d-reveal, light-leak` and a deep transitions library. Pull from these rather than hand-rolling.

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
