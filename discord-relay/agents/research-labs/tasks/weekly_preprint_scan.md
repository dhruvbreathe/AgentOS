---
cron: 0 8 * * 1
---
# Weekly preprint scan — breathwork / HRV / autonomic regulation

Scan new biomedical preprints relevant to Vayu's core science and post a tight Discord digest.

Steps:

1. Use `mcp__claude_ai_bioRxiv__search_preprints` (if available) or WebSearch against bioRxiv + arXiv q-bio for papers posted in the last 7 days matching any of:
   - heart rate variability (HRV)
   - vagal tone / vagus nerve stimulation
   - slow-paced breathing / resonance frequency breathing / HRV biofeedback
   - respiratory sinus arrhythmia (RSA)
   - interoception
   - breathwork / pranayama clinical trials
2. For each paper that looks signal (not just a passing mention):
   - Note title, authors (first + et al), date, 1-sentence finding, direct relevance to Vayu
   - Skip review papers unless they're a meta-analysis with new effect sizes
3. Cross-reference against `Topics/HRV-Coherence.md` — if a paper contradicts or sharpens a citation we already use, flag it as **⚠️ revision needed**.
4. Post digest to my Discord channel, format:
   - 🔬 **Weekly preprint scan — YYYY-MM-DD** opener
   - Up to 5 bullets (title + 1-line takeaway + `[link]`)
   - If nothing new and relevant this week, post a single line: "🔬 Weekly scan — no new signal in breathwork/HRV preprints this week." Do NOT pad.
5. If any paper is high-signal for pitch-deck use, also append a one-line addition to `Topics/HRV-Coherence.md` under a `## Weekly additions` section (create if missing).

Keep digest under ~500 chars. End with 🔬 signature.
