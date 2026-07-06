---
name: dev-browser
description: Real Playwright-controlled Chrome in a QuickJS sandbox. Use this when you need to scrape JS-rendered pages, automate multi-step web flows, take screenshots, or operate web apps that don't have an API. Reaches places WebFetch can't.
invocation: Bash (`dev-browser <<'EOF' ... EOF`)
---

# dev-browser — real Chrome for agents

`dev-browser` is a CLI that executes JavaScript inside a QuickJS WASM sandbox with a pre-connected Playwright browser. You write the script, pipe it via stdin, it runs end-to-end in one Bash call. No MCP round-trips, no screenshot-and-reason loop, no brittle selectors you can't debug.

## When to reach for it

**Use dev-browser when:**
- The page is JS-rendered (App Store, Supabase dashboard, Mixpanel, most SPAs). WebFetch can't see this content.
- You need to click, fill, submit, or navigate multi-step. WebFetch is read-only.
- You need a screenshot (for visual QA, competitor tracking, landing-page regression).
- You need a persistent session — login once, reuse across scripts via named pages.
- You're scraping ≥3 pages — save tokens by extracting structured data instead of dumping HTML into context.

**Skip it (use WebFetch) when:**
- The target is a static HTML page or a markdown-rendered GitHub README.
- You only need one page, one time, and the content is server-rendered.
- You're fetching a JSON API — just `curl` it.

## Invocation pattern

Always pipe the script via heredoc so the sandbox gets the full source:

```bash
dev-browser <<'EOF'
const page = await browser.getPage("descriptive-name");
await page.goto("https://example.com", { waitUntil: "domcontentloaded" });
const data = await page.evaluate(() => ({
  title: document.title,
  h1: document.querySelector("h1")?.innerText,
}));
console.log(JSON.stringify(data));
EOF
```

Named pages (`browser.getPage("my-name")`) persist across invocations — use this for login-required flows. Anonymous pages (`browser.newPage()`) clean up after the script exits.

## Sandbox rules — things that are NOT available

The script runs in QuickJS WASM, not Node.js:
- ❌ `require()` / `import()` — no module loading
- ❌ `fs`, `path`, `os`, `process` — no host access
- ❌ `fetch()`, `WebSocket` — no direct network (go through Playwright's `page`)
- ❌ `__dirname`, `__filename`

What IS available:
- ✅ `browser` — pre-connected handle
- ✅ `console.log/warn/error/info` — routed to stdout
- ✅ `setTimeout` / `clearTimeout`
- ✅ `await saveScreenshot(buf, name)` — writes to `~/.dev-browser/tmp/<name>`
- ✅ `await writeFile(name, data)` — writes to `~/.dev-browser/tmp/`
- ✅ `await readFile(name)` — reads from `~/.dev-browser/tmp/`

To get data into the vault or agent workspace: write it to `~/.dev-browser/tmp/` inside the script, then copy it out with a second Bash call (`cp ~/.dev-browser/tmp/foo.json /path/in/vault/`).

## Page API (inside a script)

Every page is a full Playwright Page:
- `page.goto(url, { waitUntil })` — `"domcontentloaded"` is usually right; `"networkidle"` for SPA dashboards.
- `page.click(selector)`, `page.fill(selector, text)`, `page.locator(selector)`
- `page.evaluate(fn)` — run JS in the page context, return serializable data. Best for extraction.
- `page.$$eval(selector, fn)` — map over matched nodes in one call.
- `page.screenshot({ fullPage })` — returns a Buffer; pass to `saveScreenshot()`.
- `page.waitForSelector(selector, { timeout })` — for slow SPAs.

## Output rules

- **Always `console.log(JSON.stringify(...))`** at the end. The agent reads stdout — strings are noise, JSON is parseable.
- **Keep scripts focused** — one goal per script. Don't chain unrelated tasks.
- **Timeouts**: default to `{ timeout: 10000 }` on waits. Nothing should hang longer than 30s.

## Real examples

**Competitor pricing scrape:**
```bash
dev-browser <<'EOF'
const page = await browser.getPage("calm-pricing");
await page.goto("https://www.calm.com/subscribe", { waitUntil: "networkidle" });
const prices = await page.$$eval("[data-price]", els => els.map(e => ({
  plan: e.dataset.plan,
  price: e.dataset.price,
})));
console.log(JSON.stringify(prices));
EOF
```

**Screenshot for visual regression:**
```bash
dev-browser <<'EOF'
const page = await browser.getPage("vayu-landing");
await page.goto("https://vayu-prana.com", { waitUntil: "networkidle" });
const path = await saveScreenshot(await page.screenshot({ fullPage: true }), "vayu-hero.png");
console.log(path);
EOF
```

Then copy out with Bash: `cp ~/.dev-browser/tmp/vayu-hero.png "$VAULT_PATH/Media/hero-$(date +%F).png"`

**Login + navigate (persistent named page):**
```bash
dev-browser <<'EOF'
const page = await browser.getPage("dashboard");  // reuses session if already logged in
await page.goto("https://app.example.com");
if (page.url().includes("/login")) {
  await page.fill("#email", "me@example.com");
  await page.fill("#password", process.env.APP_PW);  // NOT available — pass via Buffer if needed
  await page.click("button[type=submit]");
  await page.waitForURL("**/dashboard");
}
const metric = await page.locator(".metric-value").first().textContent();
console.log(JSON.stringify({ metric }));
EOF
```

> Note: sandbox has no `process.env`. Pass secrets by writing them to `~/.dev-browser/tmp/secrets.json` first via Bash, then `readFile()` them inside the script.

## Troubleshooting

- **Empty `{}` returned**: selectors didn't match. Dump `document.body.innerText.slice(0, 500)` to see what rendered.
- **`waitUntil: "domcontentloaded"` misses JS content**: switch to `"networkidle"` (slower but waits for SPA hydration).
- **Hanging**: add explicit timeouts on every `goto` and `waitForSelector`.
- **Chromium missing**: run `dev-browser install` once to fetch it (~250MB).

## Cost sense

- One dev-browser run = 1 Bash tool call + ~200B of JSON stdout.
- Same task via WebFetch on a JS-rendered page = N page fetches + full HTML in context per fetch. Order of magnitude more tokens.
- Prefer dev-browser for anything ≥2 steps or anything SPA-rendered.

## Vision loop (browser-agent mode)

When selectors are a guessing game (unknown UIs, canvas, shadow DOM, A/B-tested layouts, visual QA), stop guessing and look at the page:

1. **Act + screenshot** in one dev-browser call, always on a **named page** so state persists:
   ```bash
   dev-browser <<'EOF'
   const page = await browser.getPage("mission");
   await page.goto("https://target.example.com", { waitUntil: "networkidle" });
   console.log(await saveScreenshot(await page.screenshot(), "step1.png"));
   EOF
   ```
2. **Read the screenshot** with the Read tool at `/Users/celainc/.dev-browser/tmp/step1.png` (absolute path; Read renders images, so you see what a user sees).
3. **Decide** the next action from what you saw. State it in one line before acting (audit trail).
4. **Act again** on the same named page, screenshot again. Repeat until done.

Rules:
- Cap at ~6 loop iterations. Not converging? Dump `document.body.innerText.slice(0,800)` and rethink, or bail and report what you saw.
- Viewport screenshots (default), not `fullPage`, unless layout itself is the question. Faster, smaller.
- If selectors ARE knowable, use plain DOM extraction. The vision loop is for when they aren't.
- Full playbook, worked example, and failure modes: `references/vision-loop.md` next to this file.
