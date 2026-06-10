#!/usr/bin/env python3
"""
Direct callers for Apple App Store Connect + Google Play Developer APIs.

Subcommands:
  apple-probe             Try each ASC key file + key_id combo, find the valid one.
  apple-sales --date      Apple daily sales summary (ASC Reports API).
  apple-subs --date       Apple subscriber state (ASC Reports API, SUBSCRIPTION).
  apple-events --date     Apple subscription events (ASC Reports API, SUBSCRIPTION_EVENT).
  apple-finance --period  Apple financial report for a region+period (YYYY-MM or QQ-YYYY).
  apple-reviews           Apple customer reviews, newest first (ASC API).
  play-stats --start --end   Google Play install stats (Play Developer API).
  play-reviews            Google Play reviews (Play Developer API).
  play-voided             Google Play voided purchases.
  play-subscriptions      Google Play subscription state per purchase token.

Requires env vars from .env:
  APP_STORE_CONNECT_ISSUER_ID
  APP_STORE_CONNECT_KEY_ID (plain alias → Prana prod = N9X6FR7K52)
  APP_STORE_CONNECT_KEY_PATH_* (three candidate .p8 files)
  GOOGLE_PLAY_SERVICE_ACCOUNT_JSON
  GOOGLE_PLAY_PROJECT (android package name for authorized apps)

Apple docs: https://developer.apple.com/documentation/appstoreconnectapi
Google Play docs: https://developers.google.com/android-publisher/api-ref/rest
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import json
import os
import pathlib
import sys
import time
from typing import Any

import httpx
import jwt as pyjwt

ASC_BASE = "https://api.appstoreconnect.apple.com/v1"
PLAY_BASE = "https://androidpublisher.googleapis.com"
VAYU_APP_ID_APPLE = None  # resolved at runtime via /apps
VAYU_PACKAGE_ANDROID = "com.vayu.app"  # updated from env if set

KEY_PATH_ENVS = [
    "APP_STORE_CONNECT_KEY_PATH_APPSTORE",
    "APP_STORE_CONNECT_KEY_PATH_3XJL9RTV9Z",
    "APP_STORE_CONNECT_KEY_PATH_75672KR2X3",
]
KEY_ID_ENVS = [
    "APP_STORE_CONNECT_KEY_ID",          # default / Prana (N9X6FR7K52)
    "APP_STORE_CONNECT_KEY_ID_PRANA",    # Prana (N9X6FR7K52)
    "APP_STORE_CONNECT_KEY_ID_VAYU_PROMO",  # Vayu Promo (B3V694MD49)
]


def _asc_jwt(key_id: str, issuer: str, key_path: str) -> str:
    """Mint a short-lived ASC JWT (ES256)."""
    key = pathlib.Path(key_path).read_text()
    now = int(time.time())
    payload = {
        "iss": issuer,
        "iat": now,
        "exp": now + 60 * 18,  # 20-min cap per Apple; use 18 to be safe
        "aud": "appstoreconnect-v1",
    }
    return pyjwt.encode(payload, key, algorithm="ES256", headers={"kid": key_id, "typ": "JWT"})


def _asc_get(path: str, token: str, **params) -> httpx.Response:
    url = ASC_BASE + path
    return httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)


def cmd_apple_probe(args: argparse.Namespace) -> int:
    """Try every (key_id, key_path) pair and report which authenticate."""
    issuer = os.environ["APP_STORE_CONNECT_ISSUER_ID"]
    results: list[dict] = []
    for kid_env in KEY_ID_ENVS:
        kid = os.environ.get(kid_env)
        if not kid:
            continue
        for path_env in KEY_PATH_ENVS:
            path = os.environ.get(path_env)
            if not path or not pathlib.Path(path).exists():
                continue
            try:
                token = _asc_jwt(kid, issuer, path)
                r = _asc_get("/apps", token, limit=1)
                results.append({
                    "key_id_env": kid_env, "key_id": kid,
                    "path_env": path_env, "path_basename": pathlib.Path(path).name,
                    "status": r.status_code,
                    "ok": r.status_code == 200,
                    "body_head": r.text[:120] if r.status_code != 200 else "ok",
                })
            except Exception as e:
                results.append({
                    "key_id_env": kid_env, "key_id": kid, "path_env": path_env,
                    "path_basename": pathlib.Path(path).name,
                    "status": None, "ok": False, "error": str(e)[:120],
                })
    print(json.dumps(results, indent=2))
    return 0 if any(r.get("ok") for r in results) else 1


def _resolve_asc_auth() -> tuple[str, str, str]:
    """Find a working (key_id, issuer, key_path) from the env. Cached per run."""
    issuer = os.environ["APP_STORE_CONNECT_ISSUER_ID"]
    for kid_env in KEY_ID_ENVS:
        kid = os.environ.get(kid_env)
        if not kid:
            continue
        for path_env in KEY_PATH_ENVS:
            path = os.environ.get(path_env)
            if not path or not pathlib.Path(path).exists():
                continue
            try:
                token = _asc_jwt(kid, issuer, path)
                r = _asc_get("/apps", token, limit=1)
                if r.status_code == 200:
                    return kid, issuer, path
            except Exception:
                continue
    raise RuntimeError("No valid ASC key/key_id pair found. Run `apple-probe` to diagnose.")


def _find_vayu_vendor_number(token: str) -> str | None:
    """Get the vendor number (required for sales reports)."""
    # ASC Reports API needs vendor number; it's org-level, query via /salesReports
    # Actually vendor number isn't in /apps. Try env first.
    return os.environ.get("APPLE_VENDOR_NUMBER")


def cmd_apple_sales(args: argparse.Namespace) -> int:
    kid, issuer, path = _resolve_asc_auth()
    token = _asc_jwt(kid, issuer, path)
    vendor = _find_vayu_vendor_number(token)
    if not vendor:
        print(json.dumps({"error": "APPLE_VENDOR_NUMBER not set in .env — needed for sales report. Find it in ASC → Payments and Financial Reports → Vendor Number."}))
        return 2
    params = {
        "filter[frequency]": args.frequency.upper(),
        "filter[reportSubType]": "SUMMARY",
        "filter[reportType]": "SALES",
        "filter[reportDate]": args.date,
        "filter[vendorNumber]": vendor,
    }
    url = ASC_BASE + "/salesReports"
    r = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/a-gzip"}, timeout=60)
    if r.status_code != 200:
        print(json.dumps({"error": f"asc {r.status_code}", "body": r.text[:500]}))
        return 1
    # response is gzip TSV
    tsv = gzip.decompress(r.content).decode("utf-8")
    lines = tsv.strip().split("\n")
    header = lines[0].split("\t")
    rows = [dict(zip(header, line.split("\t"))) for line in lines[1:]]
    totals = {
        "totalUnits": sum(int(r.get("Units") or 0) for r in rows),
        "totalProceeds": round(sum(float(r.get("Developer Proceeds") or 0) for r in rows), 2),
        "rowCount": len(rows),
    }
    print(json.dumps({"date": args.date, "frequency": args.frequency, "totals": totals, "rows": rows[:args.limit] if args.limit else rows}, default=str))
    return 0


VAYU_APPLE_APP_ID = "6744126459"  # Vayu iOS appstore app id


def cmd_apple_reviews(args: argparse.Namespace) -> int:
    """Apple customer reviews for Vayu iOS, newest first."""
    kid, issuer, path = _resolve_asc_auth()
    token = _asc_jwt(kid, issuer, path)
    app_id = args.app_id or os.environ.get("APPLE_APP_ID") or VAYU_APPLE_APP_ID
    params = {"sort": "-createdDate", "limit": args.limit}
    r = _asc_get(f"/apps/{app_id}/customerReviews", token, **params)
    if r.status_code != 200:
        print(json.dumps({"error": f"asc {r.status_code}", "body": r.text[:500]}))
        return 1
    body = r.json()
    reviews = []
    for item in body.get("data", []):
        a = item.get("attributes", {})
        reviews.append({
            "id": item.get("id"),
            "rating": a.get("rating"),
            "title": a.get("title"),
            "body": a.get("body"),
            "reviewerNickname": a.get("reviewerNickname"),
            "createdDate": a.get("createdDate"),
            "territory": a.get("territory"),
        })
    out = {
        "app_id": app_id,
        "count": len(reviews),
        "reviews": reviews,
    }
    print(json.dumps(out, indent=2, default=str))
    return 0


def _play_access_token() -> str:
    """Mint a Google OAuth2 access token from the service account JSON."""
    sa_path = os.environ["GOOGLE_PLAY_SERVICE_ACCOUNT_JSON"]
    sa = json.loads(pathlib.Path(sa_path).read_text())
    now = int(time.time())
    claims = {
        "iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/androidpublisher",
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }
    assertion = pyjwt.encode(claims, sa["private_key"], algorithm="RS256")
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _play_get(path: str, token: str, **params) -> httpx.Response:
    url = PLAY_BASE + path
    return httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)


def cmd_play_probe(args: argparse.Namespace) -> int:
    """Verify Google Play service account auth + find app package."""
    token = _play_access_token()
    # Try common Vayu package names
    candidates = [
        os.environ.get("GOOGLE_PLAY_PACKAGE"),
        "com.vayu.app",
        "com.vayuprana.app",
        "com.prana.vayu",
        "com.prana.vayuapp",
    ]
    results = []
    for pkg in candidates:
        if not pkg:
            continue
        r = _play_get(f"/androidpublisher/v3/applications/{pkg}/edits", token)
        results.append({"package": pkg, "status": r.status_code, "body_head": r.text[:120]})
    print(json.dumps({"token_len": len(token), "probes": results}, indent=2))
    return 0


def cmd_play_reviews(args: argparse.Namespace) -> int:
    token = _play_access_token()
    pkg = args.package or os.environ.get("GOOGLE_PLAY_PACKAGE", "com.vayu.app")
    r = _play_get(f"/androidpublisher/v3/applications/{pkg}/reviews", token, maxResults=args.limit)
    if r.status_code != 200:
        print(json.dumps({"error": r.status_code, "body": r.text[:500]}))
        return 1
    print(r.text)
    return 0


def cmd_play_subscriptions(args: argparse.Namespace) -> int:
    """List subscription products from Play."""
    token = _play_access_token()
    pkg = args.package or os.environ.get("GOOGLE_PLAY_PACKAGE", "com.vayu.app")
    r = _play_get(f"/androidpublisher/v3/applications/{pkg}/subscriptions", token)
    if r.status_code != 200:
        print(json.dumps({"error": r.status_code, "body": r.text[:500]}))
        return 1
    print(r.text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("apple-probe", help="Try each ASC key combo, report which auth.")

    p = sub.add_parser("apple-sales", help="Apple sales report.")
    p.add_argument("--date", required=True, help="YYYY-MM-DD for DAILY, YYYY-MM for MONTHLY.")
    p.add_argument("--frequency", default="DAILY", choices=["DAILY", "WEEKLY", "MONTHLY", "YEARLY"])
    p.add_argument("--limit", type=int, default=0, help="Max rows in output (0 = all).")

    p = sub.add_parser("apple-reviews", help="Apple customer reviews, newest first.")
    p.add_argument("--app-id", dest="app_id", help="ASC app id, default Vayu prod.")
    p.add_argument("--limit", type=int, default=5)

    sub.add_parser("play-probe", help="Verify Play SA auth + find package.")

    p = sub.add_parser("play-reviews", help="Play reviews.")
    p.add_argument("--package")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("play-subscriptions", help="Play subscription products.")
    p.add_argument("--package")

    args = parser.parse_args()

    if args.cmd == "apple-probe":
        return cmd_apple_probe(args)
    if args.cmd == "apple-sales":
        return cmd_apple_sales(args)
    if args.cmd == "apple-reviews":
        return cmd_apple_reviews(args)
    if args.cmd == "play-probe":
        return cmd_play_probe(args)
    if args.cmd == "play-reviews":
        return cmd_play_reviews(args)
    if args.cmd == "play-subscriptions":
        return cmd_play_subscriptions(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
