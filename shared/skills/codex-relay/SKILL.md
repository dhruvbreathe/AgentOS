---
name: codex-relay
description: Route a writing prompt to Codex CLI (GPT-5.4, ChatGPT Plus OAuth) and return the response. Use for long-form prose drafts, captions, emails, pitch copy. Claude stays on technical work; Codex handles creative writing.
invocation: Bash (scripts/codex_relay.py)
---

# codex-relay -- GPT-5.4 writing assistant via ChatGPT Plus

Routes a prompt to OpenAI Codex CLI using Dhruv's ChatGPT Plus subscription (no separate API billing). Returns clean response text only.

## Dynamic routing -- decide before every response

Do NOT ask the operator which model to use. Decide internally based on the query shape:

**Route to Codex (GPT-5.4) when:**
- Pure writing output: caption, email draft, blog post, pitch paragraph, tagline, ad copy
- Creative variants: "give me 3 versions of X"
- Tone rewrite: "make this warmer / punchier / shorter"
- No files, APIs, or tools needed to answer

**Stay on Claude when:**
- Code, debugging, architecture, technical reasoning
- Data analysis, metrics, financial modeling
- Any tool use needed (Read, Bash, API calls, search)
- Multi-step agentic or orchestration work
- Structured decisions with tradeoffs

**Label every response with the model used.** Always show at the top or bottom:
- Codex output: `⚡ GPT-5.4`
- Claude output: `🤖 Claude Sonnet` (or whichever model)

This lets the operator see instantly which model produced what, without asking.

## Invocation pattern

```bash
PROMPT="Write a 2-sentence Instagram caption for Vayu, a breathwork app that adapts to your HRV in real time"
response=$(echo "$PROMPT" | /Users/celainc/Developers/ClaudeAgentSDK/.venv/bin/python3 /Users/celainc/Developers/ClaudeAgentSDK/scripts/codex_relay.py)
echo "$response"
```

Or with a multiline prompt:

```bash
response=$(cat <<'PROMPT' | /Users/celainc/Developers/ClaudeAgentSDK/.venv/bin/python3 /Users/celainc/Developers/ClaudeAgentSDK/scripts/codex_relay.py
You are writing copy for Vayu, a breathwork app. Tone: calm, embodied, specific.
No wellness cliches ("journey", "unlock", "transform").
Write three Facebook ad headline variants for a 7-day free trial offer.
PROMPT
)
echo "$response"
```

## Output

Returns only the clean model response. No headers, no token counts, no metadata. Ready to post or pass to a critic subagent.

## Notes

- Model: GPT-5.4 (ChatGPT Plus quota, no API credits consumed)
- Auth: ChatGPT OAuth stored in ~/.codex/auth.json (Dhruv's account)
- Timeout: 90s
- If Codex CLI is unavailable: the relay exits non-zero with a clear error. Fall back to drafting with Claude directly.
- The relay uses system python3 at the absolute venv path to avoid cwd dependency issues (LEARNINGS 2026-07-25).
