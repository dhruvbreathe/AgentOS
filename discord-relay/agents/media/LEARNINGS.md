# LEARNINGS.md — What I've Figured Out

_Append-only. Each entry survives session restarts — it gets loaded into my system prompt next time, so I don't re-learn what I already know._

## Format

Use one block per lesson. Keep each entry small and sharp.

```
## YYYY-MM-DD — <short title>
- **Learned:** <the lesson itself, one sentence>
- **Why:** <the incident or evidence that taught it>
- **How to apply:** <when this should change my behaviour next time>
```

## When to write

- After a sharp mistake (so I don't repeat it)
- After a clear success that wasn't obvious up front (so I repeat it)
- When I notice a pattern across 2+ similar situations
- When the operator gives me feedback that applies beyond this moment

## When NOT to write

- For one-off facts (those go in `memory/YYYY-MM-DD.md`)
- For project state (that belongs in the Obsidian vault)
- For secrets — never

## Housekeeping

- If an entry is clearly stale (the world changed), strike through with `~~…~~` and note why. Don't silently delete.
- Every few weeks, look for duplicates and consolidate.
- If the file grows past ~200 lines, promote the most durable lessons to SOUL.md or AGENTS.md and archive the rest to `memory/learnings-YYYY-MM.md`.

---

<!-- learnings:start -->

## 2026-04-17 — Two-pass ffmpeg beats one-pass for mixed-audio composes
- **Learned:** When mixing audio streams with different sample rates, channel layouts, or durations (e.g., Gemini TTS 24kHz mono + Veo 48kHz stereo + silence), do it in two passes: render audio to a standalone WAV against an `anullsrc` base track first, then mux with `-c:v copy`. Single-pass `filter_complex` with `amix` across heterogeneous inputs throws EINVAL.
- **Why:** Spent 30+ minutes fighting `Task finished with error code: -22 (Invalid argument)` on a 5-input `amix` in the Fifty Thousand Breaths cut. Two-pass solved it in one try.
- **How to apply:** Default to two-pass whenever the compose has ≥3 audio inputs or mixed formats. Use `anullsrc` for the base and `amix=duration=first` for deterministic timing.

## 2026-04-17 — Don't direct Gemini TTS like an actor
- **Learned:** Prefix text with speech direction like `"[slowly, with space, weathered]: <line>"` and Gemini reads the direction as dialogue, stretching audio 3–4× natural length.
- **Why:** First narration pass gave a 10-second read of a line that naturally lands in 3 seconds.
- **How to apply:** Pass the line as pure content. Choose tone by picking the right voice (Charon for calm/weathered), not by prompt-engineering the read.

## 2026-04-17 — Veo is strong on landscape/texture, weak on hands/faces
- **Learned:** Wide landscape, slow nature, lit textures render cleanly. Hands, faces, recurring characters often glitch or lose continuity across clips.
- **Why:** In the 50k cut, the hands shot was the iffiest of three plates while ridgeline and ocean landed first try.
- **How to apply:** For brand work, plan shot lists around natural metaphors, light, and texture. Reserve human imagery for real footage (with consent) or static illustration.

## 2026-04-17 — Hyperframes beats raw ffmpeg for motion/compose work
- **Learned:** HeyGen's `hyperframes` framework (HTML compositions + GSAP + Puppeteer + ffmpeg) is dramatically better than raw ffmpeg `filter_complex` for anything involving typography, counters, letter-spacing reveals, or UI-style motion. Local render, no API keys, built-in linter catches structural errors before render.
- **Why:** Rebuilt the 50k cut in Hyperframes in roughly half the time of the ffmpeg version, with better type motion that would have been painful in filter_complex.
- **How to apply:** Default to `hyperframes` for brand/motion work. Keep Veo for generative plates and raw ffmpeg for simple concat/trim. Gotcha: `<video>` elements can't be direct children of a root that has `data-start`; wrap in a sub-composition or drop data-start from root.

<!-- learnings:end -->
