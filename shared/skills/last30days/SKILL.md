---
name: last30days
description: Research what people actually said about any topic in the last 30 days, ranked by real engagement. Pulls posts + engagement from Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, and the web, then synthesizes. Use for competitor/market scans, "what are people saying about X", trend checks, launch-reaction reads, and grounding any claim in recent real-world discussion.
---

# last30days (v3.3.2)

Multi-source recency research engine. Works keyless (Reddit + Web + GitHub + HN); richer with optional API keys. Source repo: github.com/mvanhorn/last30days-skill.

> This is a thin loader. The full operating contract (1709 lines of laws, pre-flight steps, output format) lives next to this file as `SKILL.full.md` and is NOT inlined every turn on purpose, it's heavy. Read it on demand, only when you're actually about to run the skill.

## When you invoke this skill

1. **Read the full spec first.** `Read` the sibling file `SKILL.full.md` in this same directory top to bottom before producing any output. It defines the LAWs, the `--plan` requirement for named entities, the pre-flight checklist, and the exact output format. Skipping it produces malformed results, this is the skill's own hard rule.
2. **Pin the interpreter.** This box's default `python3` is a pyenv 3.9 shim, but the engine needs 3.12+. Always invoke with the explicit path:
   ```bash
   export LAST30DAYS_PYTHON=/opt/homebrew/bin/python3.12
   ```
3. **SKILL_DIR is this directory.** `scripts/last30days.py` sits directly under it. From an agent the absolute path is:
   `/Users/celainc/Developers/ClaudeAgentSDK/shared/skills/last30days/`
4. **Run the engine** per `SKILL.full.md` (basic shape: `"$LAST30DAYS_PYTHON" "$SKILL_DIR/scripts/last30days.py" "<topic>" --plan "$PLAN_FILE" --emit=compact`). Never produce output from WebSearch alone, the valid output always carries the engine's emoji-tree footer.

## Deps on this machine
- `python3.12`, `node v22`, `yt-dlp`, `gh` — all present.
- Runtime keyless ≈ 60s. Sources: Reddit, Web, GitHub, HN.

## Optional keys (add to `.env` + the using agent's `env_passthrough` to widen coverage)
- `SCRAPECREATORS_API_KEY` — primary; unlocks TikTok, Instagram, Threads, Pinterest (free key at scrapecreators.com)
- `XAI_API_KEY` or browser `AUTH_TOKEN`/`CT0` — unlocks X/Twitter (fastest signal for breaking topics)
- `OPENAI_API_KEY` / `OPENROUTER_API_KEY` — better LLM rerank + scoring (falls back to local without)
- `BSKY_HANDLE` + `BSKY_APP_PASSWORD` — Bluesky

Without any keys it still returns grounded Reddit/Web/GitHub/HN evidence.
