#!/usr/bin/env python3
"""
One-shot OAuth flow to mint a Google Ads API refresh token.

Run: ./.venv/bin/python scripts/get_google_ads_refresh_token.py

Reads GOOGLE_ADS_CLIENT_ID + GOOGLE_ADS_CLIENT_SECRET from .env, opens
your browser to Google's consent screen with the `adwords` scope, captures
the auth code via localhost loopback, exchanges it for tokens, and prints
the refresh_token. Append the value to .env as GOOGLE_ADS_REFRESH_TOKEN.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

SCOPE = "https://www.googleapis.com/auth/adwords"
REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8766
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/oauth2callback"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_PATH.exists():
        sys.exit(f"missing {ENV_PATH}")
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def main() -> None:
    env = load_env()
    client_id = env.get("GOOGLE_ADS_CLIENT_ID")
    client_secret = env.get("GOOGLE_ADS_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("missing GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET in .env")

    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_a, **_k):  # silence
            pass

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.path != "/oauth2callback":
                self.send_response(404)
                self.end_headers()
                return
            if qs.get("state", [None])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch")
                return
            if "error" in qs:
                captured["error"] = qs["error"][0]
            else:
                captured["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>OK</h1><p>You can close this tab. Return to the terminal.</p>")

    http.server.HTTPServer.allow_reuse_address = True
    server = http.server.HTTPServer((REDIRECT_HOST, REDIRECT_PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    print(f"opening browser → {url[:80]}…")
    print(f"if no browser opens, paste this URL manually:\n  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print(f"waiting on http://{REDIRECT_HOST}:{REDIRECT_PORT}/oauth2callback …")
    while not captured:
        pass
    server.shutdown()

    if "error" in captured:
        sys.exit(f"oauth error: {captured['error']}")

    code = captured["code"]
    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"token exchange failed: {e.read().decode()}")

    refresh = payload.get("refresh_token")
    access = payload.get("access_token")
    if not refresh:
        sys.exit(f"no refresh_token in response: {payload}")

    print()
    print("=" * 60)
    print("REFRESH TOKEN (save this):")
    print(refresh)
    print("=" * 60)
    print()
    print(f"access_token (test, expires {payload.get('expires_in')}s): {access[:20]}…")
    print()
    print("→ append to .env:")
    print(f"GOOGLE_ADS_REFRESH_TOKEN={refresh}")


if __name__ == "__main__":
    main()
