# FILE_DELIVERY.md — Drop Files In Discord, Not Paths

**The rule:** when the operator asks for files — PDFs, images, audio, video, screenshots, exports — **upload them directly into the Discord channel as attachments**, not as vault paths the operator has to go open in Finder.

Providing a path like `Marketing/Outreach/.../05-instructors.pdf` and stopping there is wrong. Discord lets the operator preview / download / forward in one click; a path forces them to leave the chat and chase the file on disk. Operator-set rule, 2026-05-14: **"always share all the files here directly in Discord rather than providing path."**

Path + attachment is fine. Path alone is not.

## How I post a file via my webhook

Every agent has its own outbound webhook in `.env` under `<KEY>_WEBHOOK_URL` (e.g. `$MAIN_WEBHOOK_URL`, `$MARKETING_WEBHOOK_URL`, `$MEDIA_WEBHOOK_URL`). The same webhook the relay uses for text posts accepts `multipart/form-data` with up to **10 files per request**.

```bash
curl -sS -X POST "$<MY>_WEBHOOK_URL" \
  -F 'payload_json={"username":"<my-name>","content":"<my message>"}' \
  -F "file1=@/absolute/path/to/file-a.pdf" \
  -F "file2=@/absolute/path/to/file-b.png"
```

That single curl call posts the message **and** attaches the files. HTTP 200 = delivered. JSON response includes `attachments[].url` if you want to log it.

## Size limits

Discord caps webhook payloads at roughly **10 MB total per request** (free-tier server). Hard rules:

- **A single file ≥ 10 MB → split or chunk.** You cannot post it as one.
- **Multiple files totalling ≥ 10 MB → batch into multiple posts.** Keep each post under 10 MB.
- **A single file > 25 MB on a boosted server, or > 10 MB on free → forget Discord, fall back to vault path + note the size + offer to upload to Google Drive.**

A 31 MB zip will fail with HTTP 413 `Request entity too large`. I've burned that lesson; don't repeat it.

## Chunking strategy

If the operator asks for a whole package:

1. **Post the marquee deliverable first** in its own message — the PDFs, the report, the thing they actually need to look at. Don't bury it.
2. **Group like-with-like in follow-up posts** — screenshots in one, audio in one, video in one. Each post gets its own short caption.
3. **Number the bundles** so the operator can tell at a glance which is which. `📎 Bundle 1 — PDFs`, `📸 Bundle 2 — visuals`, `🎧 Bundle 3 — audio`.
4. **End with a one-line map** of what's where, so they can scan without scrolling.

Example shape (post sequence):

```
📎 Bundle 1 — PDFs   (3 attachments)
📸 Bundle 2 — visuals   (8 attachments)
🎥 Bundle 3 — video   (2 attachments)
🎧 Bundle 4 — audio short   (3 attachments)
🎧 Bundle 5 — audio long   (2 attachments)
✅ All 5 bundles dropped. Quick map: 1=PDFs, 2=screens, 3=video, 4–5=audio.
```

## When NOT to upload

- **Secrets.** Tokens, API keys, raw credentials, `.env` files, `.p8` certificates. Never. Even if the operator asks. Surface a path + a warning, or offer to walk them through it via vault.
- **Files > 25 MB.** Discord won't accept; chunking video/audio loses meaning. Use Google Drive / vault path + explanation.
- **Files the operator didn't ask for.** Don't volunteer dumps. If you have 30 files and they asked for "the deck", send the deck.
- **Files outside the operator's chat scope.** If you're routing to another agent (`send_to_agent`), don't attach — the relay doesn't carry attachments cross-agent. Use a vault path in the route instead.

## Path + attachment is the right shape

A good file-delivery reply looks like:

> 📎 Here's the package.
>
> *(attachments here)*
>
> Full vault path if you prefer working from disk: `Marketing/Outreach/2026-05-14-foo/`

Path included for archival / forwarding to non-Discord destinations. Attachments included because that's what the operator can click right now.

## Recovery when upload fails

- **HTTP 413** → split smaller, retry. Don't tell the operator "Discord rejected this" without retrying with a smaller chunk first.
- **HTTP 429 (rate-limited)** → wait, retry once. If still rate-limited, fall back to path + note.
- **HTTP 401 / 403** → my webhook URL is stale. Fall back to path + flag in #virtual-ceo-cto-dhruv that webhooks need a refresh.

## The one-liner version

> Files in chat go IN chat. Path-only is a half-delivery.
