# Vision loop playbook (browser-agent mode)

The vision loop turns dev-browser from a scripted scraper into an agent that can operate UIs it has never seen. The trick: every Claude model in this fleet reads images natively via the Read tool. A screenshot is not a log artifact, it is your eyes.

## The loop contract

```
while goal not reached and iterations < 6:
    1. dev-browser: perform ONE action on a named page, save a screenshot
    2. Read the screenshot (absolute path under /Users/celainc/.dev-browser/tmp/)
    3. Decide: what did I see, what single action follows
    4. Log the decision in one line, then act
```

One action per iteration. Multi-action scripts are fine once a flow is KNOWN; while discovering, small steps keep failure diagnosable. The named page (`browser.getPage("mission")`) is what carries cookies, login state, and DOM across your separate Bash calls.

## Worked example: find the cancel-subscription flow on an unknown dashboard

Step 1, land and look:

```bash
dev-browser <<'EOF'
const page = await browser.getPage("cancel-hunt");
await page.goto("https://app.example.com/account", { waitUntil: "networkidle" });
console.log(await saveScreenshot(await page.screenshot(), "cancel1.png"));
EOF
```

Read `/Users/celainc/.dev-browser/tmp/cancel1.png`. You see a sidebar with "Billing" and a gear icon.

Step 2, decide out loud ("Billing link visible in left sidebar, clicking it"), then act:

```bash
dev-browser <<'EOF'
const page = await browser.getPage("cancel-hunt");
await page.click("text=Billing");
await page.waitForLoadState("networkidle");
console.log(await saveScreenshot(await page.screenshot(), "cancel2.png"));
EOF
```

Read, decide, repeat. When you find the target, extract or act, then report the PATH you discovered so the flow can be scripted selector-first next time.

## Text-first shortcut

A screenshot costs more context than text. Before screenshotting, consider whether text answers it:

```js
const outline = await page.evaluate(() => ({
  headings: [...document.querySelectorAll("h1,h2,h3")].map(h => h.innerText.trim()).slice(0, 20),
  buttons: [...document.querySelectorAll("button,[role=button],a.btn")].map(b => b.innerText.trim()).filter(Boolean).slice(0, 30),
  url: location.href,
}));
console.log(JSON.stringify(outline));
```

Rule of thumb: text outline first; screenshot when layout, imagery, state coloring, or "why does this look wrong" is the actual question. Visual QA always screenshots.

## Failure modes and answers

| Symptom | Move |
|---|---|
| Screenshot shows a cookie banner / modal blocking everything | Dismiss it first (`page.click` on the accept button you can SEE), then continue |
| Same screenshot two iterations in a row | Your click did nothing. Check the outline dump, try keyboard nav (`page.keyboard.press("Tab")`) or a different element |
| Login wall | Named page keeps sessions; log in once manually via a scripted step, session persists across future runs |
| Infinite spinner in screenshot | `waitUntil: "networkidle"` missed a websocket app; use `page.waitForSelector` on something concrete instead |
| 6 iterations, no convergence | Stop. Report what you saw, attach the last screenshot to Discord, ask or hand off. Burning 20 loops is worse than asking |
| Element visible in screenshot but click misses | Use coordinates from what you saw: `page.mouse.click(x, y)` works when selectors lie (canvas, custom widgets) |

## Guardrails

- **Destructive actions:** anything that deletes, pays, publishes, or sends while driving a UI visually needs the same care as a gated Bash command. Screenshot BEFORE and AFTER the irreversible click, and if the account matters (App Store Connect, Play Console, ad platforms), confirm with the operator before the destructive step.
- **Secrets:** never type raw secrets from your context into pages you are exploring. Login flows use the secrets-file pattern from SKILL.md (write to `~/.dev-browser/tmp/secrets.json` via Bash, `readFile()` inside the sandbox).
- **Screenshots of sensitive dashboards** stay local. Do not attach screenshots containing keys, customer PII, or revenue breakdowns to public-facing surfaces; operator channel is fine.
- **Cleanup:** screenshots pile up in `~/.dev-browser/tmp/`. Name them per-mission (`cancel1.png`, `cancel2.png`) so a later `rm` of your own artifacts is safe and obvious.
