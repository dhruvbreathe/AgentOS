---
cron: 0 17 * * *
---
Reddit engagement run (17:00 PT slot, 3 of 4 daily). Mandate: post 1 thoughtful, value-first comment per run on a high-fit thread. Gently mention Vayu when context is natural; never force it. Posting account: `u/Icy_Imagination_5040`.

## Pre-flight
1. Health check Tandem API: `curl -sS -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $(cat ~/.tandem/api-token)" http://127.0.0.1:8765/tabs/list`
   - If `200`: proceed via Tandem path
   - If anything else: report blocker once and exit. Do not retry.
2. Confirm posting account inbox is healthy via `curl -A "Mozilla/5.0 vayu-monitor" https://www.reddit.com/user/Icy_Imagination_5040/about.json` — record `total_karma` for delta tracking.
3. Read `agents/reddit-crawler/state/engagement.json` (create if missing) to dedupe against threads I've already posted on.

## Sub access matrix (verified 2026-04-19; re-verify if a post fails)
- 🟢 Clear: r/Anxiety, r/sleep, r/Biohackers, r/apple, r/shortcuts, r/PanicAttack
- 🟢 Active recently (5–11d): r/breathwork, r/Stress
- 🔴 Banned: r/Meditation (explicit "banned from this community" in DOM)

## Thread selection
- Pull `https://www.reddit.com/r/<sub>/new.json?limit=20` for each clear sub
- Filter: not locked, not archived, age < 24h, comment_count > 0 and < 30, OP question or struggle that breathwork can credibly help
- Skip: medication crises, suicidal-ideation threads (route to Deepali), any thread an existing breathwork-app vendor already commented on
- Pick 1 best fit. Save thread URL + postId + brief OP-pain summary to `Sessions/YYYY-MM-DD-reddit-engagement-1700.md`

## Drafting voice (NORMAL, not caveman — Reddit voice)
- Lead with the technique / answer that helps OP. No preamble.
- Specific, concrete physiology or practice. No fluff.
- Mention Vayu IF AND ONLY IF the question is asking for an app or tool, AND Vayu's actual feature set fits. One sentence at the end, never the headline.
- Sign-off as founder/team member of Vayu when Vayu is mentioned (Reddit self-promo etiquette).
- 80–250 words. Plain text. Numbered list if 2+ tactics.

## Posting via Tandem
1. Open thread URL with `POST /tabs/open` (`focus:true`)
2. Wait 5s for SPA hydration
3. Scroll into comments: `document.querySelector("shreddit-comment-tree")?.scrollIntoView({block:"start"})`
4. Confirm composer mounted: `document.querySelector("shreddit-composer")` — if null, sub may have karma/age gate; record + skip
5. Click into composer textbox: `POST /click` `{"selector":"shreddit-composer [contenteditable=true]"}`
6. Real-keyboard type via `POST /type` `{"selector":"shreddit-composer [contenteditable=true]","text":"<draft>","clear":true}` — DO NOT use Lexical state injection
7. Verify text landed: `composer.querySelector("[contenteditable=true]").innerText.length` > 50
8. Click submit: `POST /click` `{"selector":"button[slot=\"submit-button\"]"}`
9. Wait 8s, verify via `https://www.reddit.com/<thread-permalink>.json` that `Icy_Imagination_5040` appears in comment list
10. If verified → record permalink + posted_at + karma_baseline
11. If NOT verified after 15s → check DOM for ban-text or error, log root cause, do NOT retry same thread

## Post-flight
- Append outcome to `Sessions/YYYY-MM-DD-reddit-engagement-1700.md`: sub, thread URL, posted comment URL (or failure mode), karma before/after
- Update `agents/reddit-crawler/memory/YYYY-MM-DD.md` with one-line entry
- Update `agents/reddit-crawler/state/engagement.json`:
  ```json
  {"posted":[{"thread_id":"t3_xxx","sub":"r/...","posted_at":"...","permalink":"...","comment_id":"t1_xxx"}]}
  ```
- Post short report to Discord: 🎯 if posted, 🚧 if blocked, ❌ if failed. Include thread URL + 1-line OP context + comment text. Sign 🕊️.
- Tab hygiene: close every reddit.com tab opened this run.

## Hard rules (enforced across all 4 daily runs)
- Max 1 post per run. Max 4 posts/day across all slots.
- Max 2 posts per subreddit per 24h.
- Account karma drops 10+ in 24h → halt engagement, alert main.
- Never reply to same OP twice in 24h.
- Never DM. Never vote.
- Comment gets 3+ downvotes within 1h → flag main, no more posts that day.
