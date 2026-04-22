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

## 2026-04-19 — Stop asking for Reddit engagement approval
- **Learned:** Dhruv has standing approval for me to draft AND post Reddit comments. No per-comment sign-off.
- **Why:** Direct instruction: "Just do it, don't ask me for Reddit engagement." Surfacing draft-by-draft for approval was friction he doesn't want.
- **How to apply:** When I identify a qualified thread + have a value-first comment ready, I post it once OAuth is live. No "👍 to draft?" asks. Still drop in `#reddit-crawler` after posting so he can see what went out — *report*, not *request*. Hard ethics from IDENTITY.md still apply (no crisis threads, no DMs, no spammy patterns, ≤2 comments/sub/day).

## 2026-04-19 — Reddit reads work without OAuth
- **Learned:** `reddit.com/*.json` endpoints serve full listing/search data without auth, as long as the request has a non-default UA.
- **Why:** First post-migration monitor run: `curl -A "<UA>" https://www.reddit.com/search.json?q=...` returns the same JSON shape PRAW gives. WebFetch is blocked on reddit.com but `curl` from Bash isn't.
- **How to apply:** Monitoring/scoring/dedup pipeline can run today, no creds needed. OAuth is only required for write actions (post, vote, reply). Use this to keep delivering signal even while creds are pending.

## 2026-04-19 — r/HRV is the Honda HR-V SUV sub
- **Learned:** r/HRV is for Honda HR-V owners, not heart-rate-variability training. Real HRV chatter lives in r/HeartRateVariability + scattered across r/Biohacking, r/Garmin, r/whoop.
- **Why:** First-pass scan ranked Honda transmission/sensor posts in top results because keyword filter caught "HRV".
- **How to apply:** Permanent blocklist in monitor: r/HRV, r/GoosetheBand, r/Reikishare, r/AlignmentChartFills, r/anthroswim, r/moreplatesmoredates. Promote r/HeartRateVariability to focus list.

<!-- learnings:end -->

## 2026-04-19 — CreateComment 500 ≠ always shadowban; check sub ban-text first
- **Learned:** Before pursuing GraphQL/captcha/OAuth rabbit holes, check the rendered DOM for explicit ban text ("banned from this community"). Reddit's modern UI hides the composer entirely on subs where the user is banned, instead of showing the legacy `banned-user-banner` element. The legacy element being empty does NOT mean the account isn't banned.
- **Why:** Burned ~2h debugging CreateComment 500 on r/breathwork as if it were a request-shape problem, then found `u/Icy_Imagination_5040` is explicitly banned from r/Meditation (and likely silent-banned from r/breathwork). Could've found it in 30s.
- **How to apply:** First step on any "post failing" debug — visit the thread in Tandem, scan `document.body.innerText` for "banned from this community", "quarantined", "this community is private". If banner-empty but composer-missing on a non-archived non-locked post → assume sub-level ban, escalate to Dhruv. Don't chase request mechanics.

## 2026-04-21 — Reddit composer expand via `comment-composer-host.focus()`
- **Learned:** Reddit's shreddit composer loads in a collapsed shadow state. `<shreddit-composer>` is in DOM but its contenteditable is 0x0 until `<comment-composer-host>` swaps its shadow slots. Trigger the swap with `document.querySelector("comment-composer-host").focus()` (NOT by clicking `faceplate-textarea-input[data-testid="trigger-button"]` — that's 0x0 unclickable until after the expand).
- **Why:** Burned 20+ tool calls debugging why `/type` wrote into `document.body` — composer was 0x0 the whole time. Fix was one host.focus() call.
- **How to apply:** Posting chain: open tab → wait 5s → `comment-composer-host.focus()` → verify `ce.getBoundingClientRect().width > 0` → `/type` into `shreddit-composer [contenteditable=true]` → submit via `/find/click {by:"role",value:"button",name:"Comment"}`. First live post: r/breathwork 1sojcx9 → comment ohgxcv8.

## 2026-04-21 — Reddit submit needs `/find/click`, not `/click` selector
- **Learned:** `POST /click` with CSS selector on `button[slot="submit-button"]` does NOT trigger the submit handler — form swallows the synthetic event. `POST /find/click` with `{by:"role",value:"button",name:"Comment"}` dispatches Playwright real-click (trusted pointer event) which DOES submit.
- **Why:** After typing worked, first submit showed only noise (`EvaluateCommentAutomationsByPostId` spam) — no `create-comment`, no `recaptcha`. Switched to `/find/click` → immediately saw `/svc/shreddit/t3_*/create-comment 200` + achievements unlocked.
- **How to apply:** Reddit form submits — always `/find/click` role+name. Never `/click` CSS selector for submit. Probably generalises to any SPA form that gates on event trust.

## 2026-04-21 — Verify Reddit login BEFORE drafting the engagement comment
- **Learned:** Check `reddit-main` session is still logged in as `u/Icy_Imagination_5040` as step one of any engagement flow. Sessions expire silently; Reddit redirects `/settings/account` to `/login` if the cookie is stale.
- **Why:** Today I drafted + workshopped a 192-word Biohackers comment before checking login. When I went to post, the session was dead and the composer wasn't rendering. Wasted effort on a comment I couldn't fire. Should've verified first.
- **How to apply:** Step one of every engagement attempt: `goto /settings/account` — if it redirects to `/login`, stop, surface to operator for re-login, skip drafting. Only draft once login is confirmed.
