#!/usr/bin/env python3
"""Safe Discord webhook poster for agents and crons.

Hard guarantees, in order:
1. Em-dash / en-dash scan BEFORE anything leaves the machine (operator ban,
   2026-05-19). Fails loud, never posts a draft containing them.
2. User-Agent header always set (UA-less posts get Cloudflare 1010 -> 403).
3. Auto-split at 1900 chars on paragraph boundaries (Discord 2000 cap).
4. Optional file attachments (multipart), 10-file / ~10MB guard.

Usage:
  post_webhook.py --env MAIN_WEBHOOK_URL --content "message"        # or --content-file path
  post_webhook.py --env MAIN_WEBHOOK_URL --payload-json path        # raw CV2 payload, posted as-is (still dash-scanned)
  ... [--file /path/to/attach ...] [--components-v2]

Exit codes: 0 ok, 2 dash found, 3 HTTP error, 4 size/limit violation.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

UA = "VayuAgentOS/1.0 (+https://vayu-prana.com)"
DASHES = ("\u2014", "\u2013")
SPLIT_AT = 1900
MAX_FILES = 10
MAX_BYTES = 9_500_000  # stay under Discord's ~10MB multipart cap


def fail(code: int, msg: str) -> None:
    print(f"post_webhook: {msg}", file=sys.stderr)
    sys.exit(code)


def dash_scan(text: str, label: str) -> None:
    for ch in DASHES:
        if ch in text:
            idx = text.index(ch)
            ctx = text[max(0, idx - 40): idx + 40].replace("\n", " ")
            fail(2, f"banned dash ({ch!r}) in {label} near: ...{ctx}... Rewrite with comma/period/parens/colon.")


def split_content(text: str) -> list[str]:
    if len(text) <= SPLIT_AT:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        candidate = f"{cur}\n\n{para}" if cur else para
        if len(candidate) <= SPLIT_AT:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
        while len(para) > SPLIT_AT:  # single oversized paragraph: hard split on newline/space
            cut = para.rfind("\n", 0, SPLIT_AT)
            if cut < SPLIT_AT // 2:
                cut = para.rfind(" ", 0, SPLIT_AT)
            if cut <= 0:
                cut = SPLIT_AT
            chunks.append(para[:cut])
            para = para[cut:].lstrip()
        cur = para
    if cur:
        chunks.append(cur)
    return chunks


def curl_post(url: str, payload: dict, files: list[str]) -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(json.dumps(payload, ensure_ascii=False))
        payload_path = f.name
    cmd = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
           "-X", "POST", url, "-A", UA, "-F", f"payload_json=<{payload_path}"]
    for i, path in enumerate(files, 1):
        cmd += ["-F", f"file{i}=@{path}"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    os.unlink(payload_path)
    try:
        return int(out.stdout.strip())
    except ValueError:
        return -1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="MAIN_WEBHOOK_URL", help="env var holding the webhook URL")
    ap.add_argument("--url", help="explicit webhook URL (overrides --env)")
    ap.add_argument("--content", help="message text")
    ap.add_argument("--content-file", help="read message text from file")
    ap.add_argument("--payload-json", help="raw payload JSON file (e.g. Components V2); posted as-is")
    ap.add_argument("--username", default=None)
    ap.add_argument("--components-v2", action="store_true", help="append ?with_components=true")
    ap.add_argument("--file", action="append", default=[], help="attachment path (repeatable)")
    args = ap.parse_args()

    url = args.url or os.environ.get(args.env)
    if not url:
        fail(3, f"no webhook URL (env {args.env} unset and no --url)")
    if args.components_v2 and "with_components" not in url:
        url += ("&" if "?" in url else "?") + "with_components=true"

    if len(args.file) > MAX_FILES:
        fail(4, f"{len(args.file)} attachments > {MAX_FILES} max; batch into multiple posts")
    total = sum(os.path.getsize(p) for p in args.file)
    if total > MAX_BYTES:
        fail(4, f"attachments total {total} bytes > {MAX_BYTES}; split into smaller posts")

    if args.payload_json:
        raw = open(args.payload_json).read()
        dash_scan(raw, args.payload_json)
        code = curl_post(url, json.loads(raw), args.file)
        if code not in (200, 204):
            fail(3, f"HTTP {code}")
        print(f"ok HTTP {code}")
        return

    text = args.content if args.content is not None else (open(args.content_file).read() if args.content_file else None)
    if text is None:
        fail(3, "need --content, --content-file, or --payload-json")
    dash_scan(text, "content")

    chunks = split_content(text)
    for i, chunk in enumerate(chunks):
        payload = {"content": chunk}
        if args.username:
            payload["username"] = args.username
        code = curl_post(url, payload, args.file if i == len(chunks) - 1 else [])
        if code not in (200, 204):
            fail(3, f"HTTP {code} on chunk {i + 1}/{len(chunks)}")
    print(f"ok ({len(chunks)} message{'s' if len(chunks) > 1 else ''})")


if __name__ == "__main__":
    main()
