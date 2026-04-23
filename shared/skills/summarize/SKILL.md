---
name: summarize
description: Produce a tight, scannable summary of long content (chat logs, articles, research notes, meeting transcripts). Use when you need to distill 1000+ words into <200, or when writing a TL;DR at the top of a long reply. Output shape is load-bearing.
---

# summarize — distill without flattening

## When to reach for this

- Long Discord thread you need to catch the operator up on
- Research dump from a subagent that needs a TL;DR
- Article/paper/transcript being triaged for relevance
- Weekly/monthly digest across many data sources
- Session handoff where the next agent needs context fast

## Shape (follow it)

```
🧠 **TL;DR:** <one sentence — the headline conclusion, not a recap of topics>

**Key points:**
- <point 1, load-bearing, specific>
- <point 2>
- <point 3>
- (max 5 — if you have more, split into sections)

**What changed / what's new:** <if applicable>

**Open questions:** <if applicable>

**Source:** <link or vault path>
```

## Rules that keep summaries useful

1. **Lead with the conclusion, not the topic.** ❌ "Discussed pricing strategy" ✅ "Raising B2B price to $49/seat was approved."
2. **Numbers beat adjectives.** "Grew meaningfully" = useless. "412 → 487 DAU over 14 days" = useful.
3. **Name names.** "Someone asked about X" = useless. "Deepali flagged X" = useful.
4. **Cut the process.** Nobody needs "We discussed, then debated, then resolved." Skip to the resolution.
5. **If there's no conclusion, say so.** "Still debating — no decision yet" is a valid summary.
6. **Preserve disagreements.** If two people held different views, flag the split — don't paper over it.

## Anti-patterns to avoid

- ❌ "This article covers..." (tell me what it CONCLUDES)
- ❌ Bullet points that restate the heading
- ❌ "Various topics including A, B, C, D..." (pick the 3 that matter)
- ❌ "Important considerations" (what SPECIFICALLY)
- ❌ 5+ bullets at one nesting level (reorganize)

## Length targets

| Input | Output |
|---|---|
| Single article/thread (~2k words) | 100-150 words |
| Multi-source research dump (~10k words) | 250-400 words |
| Week of activity | 300-500 words |
| Quarter of activity | 500-800 words, sectioned |

## If the operator wants more depth

Don't bloat the summary — link to the source. "Full analysis: `Sessions/2026-04-19-<topic>.md`". The TL;DR is the door; the vault note is the room.
