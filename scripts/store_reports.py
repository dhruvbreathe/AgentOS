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
  play-reviews            Google Play reviews (Play Developer API).
  play-subscriptions      Google Play subscription products.
  play-crash --days       Play crash rate, DAILY (Developer Reporting API).
  play-anr --days         Play ANR rate, DAILY (Developer Reporting API).
  play-stats --month      Play install stats (GCS stats bucket; needs GOOGLE_PLAY_STATS_BUCKET).

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
VAYU_PACKAGE_ANDROID = "com.prana.vayu"  # real Play package; updated from env if set

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


def _asc_report_rows(report_type: str, version: str, date: str, frequency: str = "DAILY") -> tuple[list[dict], str | None]:
    """Fetch a gzip-TSV ASC sales/subscription report. Returns (rows, error)."""
    kid, issuer, path = _resolve_asc_auth()
    token = _asc_jwt(kid, issuer, path)
    vendor = _find_vayu_vendor_number(token)
    if not vendor:
        return [], "APPLE_VENDOR_NUMBER not set in .env"
    params = {
        "filter[frequency]": frequency.upper(),
        "filter[reportSubType]": "SUMMARY",
        "filter[reportType]": report_type,
        "filter[version]": version,
        "filter[reportDate]": date,
        "filter[vendorNumber]": vendor,
    }
    r = httpx.get(ASC_BASE + "/salesReports", params=params,
                  headers={"Authorization": f"Bearer {token}", "Accept": "application/a-gzip"}, timeout=60)
    if r.status_code == 404:
        # "Report is not available yet" or no data for that date -> treat as empty
        # so callers step back to an earlier report date.
        return [], None
    if r.status_code != 200:
        return [], f"asc {r.status_code}: {r.text[:300]}"
    tsv = gzip.decompress(r.content).decode("utf-8")
    lines = tsv.strip().split("\n")
    if len(lines) < 2:
        return [], None  # no data (report empty for that date)
    header = lines[0].split("\t")
    return [dict(zip(header, ln.split("\t"))) for ln in lines[1:]], None


# Active-subscriber columns in the SUBSCRIPTION (v1_4) report.
_SUB_PAID_COLS = [
    "Active Standard Price Subscriptions",
    "Active Pay Up Front Introductory Offer Subscriptions",
    "Active Pay As You Go Introductory Offer Subscriptions",
    "Pay Up Front Offer Code Subscriptions",
    "Pay As You Go Offer Code Subscriptions",
    "Pay Up Front Win-back Offers",
    "Pay As You Go Win-back Offers",
]
_SUB_TRIAL_COLS = [
    "Active Free Trial Introductory Offer Subscriptions",
    "Free Trial Offer Code Subscriptions",
    "Free Trial Win-back Offers",
]


def cmd_apple_subs(args: argparse.Namespace) -> int:
    """Apple active-subscriber state snapshot (SUBSCRIPTION report, v1_4).

    The SUBSCRIPTION report lags ~5 days; if the requested date is empty it
    steps back day by day (up to --lookback) to the most recent one with data.
    """
    target = args.date or dt.date.today().isoformat()
    base = dt.date.fromisoformat(target)
    rows: list[dict] = []
    used = None
    err = None
    for back in range(args.lookback + 1):
        d = (base - dt.timedelta(days=back)).isoformat()
        rows, err = _asc_report_rows("SUBSCRIPTION", "1_4", d)
        if err:
            print(json.dumps({"error": err, "date": d})); return 1
        if rows:
            used = d
            break
    if not rows:
        print(json.dumps({"error": "no SUBSCRIPTION data found", "searched_through": d})); return 1

    def col_sum(cols):
        tot = 0
        for r in rows:
            for c in cols:
                try:
                    tot += int(r.get(c) or 0)
                except ValueError:
                    pass
        return tot

    paid = col_sum(_SUB_PAID_COLS)
    trial = col_sum(_SUB_TRIAL_COLS)
    # per-product (subscription name) paid breakdown
    by_product: dict[str, int] = {}
    for r in rows:
        name = r.get("Subscription Name", "?")
        n = 0
        for c in _SUB_PAID_COLS:
            try:
                n += int(r.get(c) or 0)
            except ValueError:
                pass
        by_product[name] = by_product.get(name, 0) + n
    print(json.dumps({
        "report": "SUBSCRIPTION",
        "report_date": used,
        "active_paid": paid,
        "active_trial": trial,
        "paid_by_product": by_product,
        "row_count": len(rows),
    }, indent=2, default=str))
    return 0


def cmd_apple_events(args: argparse.Namespace) -> int:
    """Apple subscription events (SUBSCRIPTION_EVENT report, v1_4): renewals + churn.

    Aggregates the Quantity column by Event type over a single report date
    (or the most recent one with data within --lookback).
    """
    target = args.date or dt.date.today().isoformat()
    base = dt.date.fromisoformat(target)
    rows: list[dict] = []
    used = None
    for back in range(args.lookback + 1):
        d = (base - dt.timedelta(days=back)).isoformat()
        rows, err = _asc_report_rows("SUBSCRIPTION_EVENT", "1_4", d)
        if err:
            print(json.dumps({"error": err, "date": d})); return 1
        if rows:
            used = d
            break
    if not rows:
        print(json.dumps({"error": "no SUBSCRIPTION_EVENT data found", "searched_through": d})); return 1

    by_event: dict[str, int] = {}
    for r in rows:
        ev = r.get("Event", "?")
        try:
            q = int(r.get("Quantity") or 0)
        except ValueError:
            q = 0
        by_event[ev] = by_event.get(ev, 0) + q
    print(json.dumps({
        "report": "SUBSCRIPTION_EVENT",
        "report_date": used,
        "events_by_type": dict(sorted(by_event.items(), key=lambda kv: -kv[1])),
        "row_count": len(rows),
    }, indent=2, default=str))
    return 0


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


PLAY_SCOPE_PUBLISHER = "https://www.googleapis.com/auth/androidpublisher"
PLAY_SCOPE_REPORTING = "https://www.googleapis.com/auth/playdeveloperreporting"
PLAY_SCOPE_GCS = "https://www.googleapis.com/auth/devstorage.read_only"


def _play_access_token(scope: str = PLAY_SCOPE_PUBLISHER) -> str:
    """Mint a Google OAuth2 access token from the service account JSON.

    Pass a space-separated scope string to request more than one scope.
    """
    sa_path = os.environ["GOOGLE_PLAY_SERVICE_ACCOUNT_JSON"]
    sa = json.loads(pathlib.Path(sa_path).read_text())
    now = int(time.time())
    claims = {
        "iss": sa["client_email"],
        "scope": scope,
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
        # Use a real read endpoint. There is no list-edits REST method; a GET on
        # /edits 404s (HTML body) for ANY package, valid or not — which falsely
        # reads as "SA not linked". /subscriptions is a genuine access check.
        r = _play_get(f"/androidpublisher/v3/applications/{pkg}/subscriptions", token)
        results.append({"package": pkg, "status": r.status_code, "body_head": r.text[:120]})
    print(json.dumps({"token_len": len(token), "probes": results}, indent=2))
    return 0


def cmd_play_reviews(args: argparse.Namespace) -> int:
    token = _play_access_token()
    pkg = args.package or os.environ.get("GOOGLE_PLAY_PACKAGE", VAYU_PACKAGE_ANDROID)
    r = _play_get(f"/androidpublisher/v3/applications/{pkg}/reviews", token, maxResults=args.limit)
    if r.status_code != 200:
        print(json.dumps({"error": r.status_code, "body": r.text[:500]}))
        return 1
    print(r.text)
    return 0


def cmd_play_subscriptions(args: argparse.Namespace) -> int:
    """List subscription products from Play."""
    token = _play_access_token()
    pkg = args.package or os.environ.get("GOOGLE_PLAY_PACKAGE", VAYU_PACKAGE_ANDROID)
    r = _play_get(f"/androidpublisher/v3/applications/{pkg}/subscriptions", token)
    if r.status_code != 200:
        print(json.dumps({"error": r.status_code, "body": r.text[:500]}))
        return 1
    print(r.text)
    return 0


PLAY_REPORTING_BASE = "https://playdeveloperreporting.googleapis.com/v1beta1"


def _pd_date(d: dt.date) -> dict:
    return {"year": d.year, "month": d.month, "day": d.day}


def _play_reporting_query(metric_set: str, metrics: list[str], days: int, pkg: str) -> dict:
    """Query a Play Developer Reporting metric set, DAILY aggregation, last `days`.

    metric_set: e.g. 'crashRateMetricSet', 'anrRateMetricSet'.
    Returns the parsed JSON (with a 'rows' timeline) or an {'error': ...}.
    """
    token = _play_access_token(PLAY_SCOPE_REPORTING)
    # The Reporting API rejects an endTime past the metric set's freshness, so
    # read the DAILY freshness and clamp end to it.
    meta = httpx.get(f"{PLAY_REPORTING_BASE}/apps/{pkg}/{metric_set}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    end = dt.date.today()
    if meta.status_code == 200:
        for fr in meta.json().get("freshnessInfo", {}).get("freshnesses", []):
            if fr.get("aggregationPeriod") == "DAILY":
                le = fr.get("latestEndTime", {})
                if le.get("year"):
                    end = dt.date(le["year"], le["month"], le["day"])
    start = end - dt.timedelta(days=days)
    body = {
        "timelineSpec": {
            "aggregationPeriod": "DAILY",
            "startTime": {**_pd_date(start), "timeZone": {"id": "America/Los_Angeles"}},
            "endTime": {**_pd_date(end), "timeZone": {"id": "America/Los_Angeles"}},
        },
        "metrics": metrics,
    }
    url = f"{PLAY_REPORTING_BASE}/apps/{pkg}/{metric_set}:query"
    r = httpx.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        return {"error": f"reporting {r.status_code}", "body": r.text[:400]}
    return r.json()


def _flatten_reporting_rows(payload: dict, metrics: list[str]) -> list[dict]:
    """Turn the Reporting API row timeline into flat {date, metric: value} dicts."""
    out = []
    for row in payload.get("rows", []):
        st = row.get("startTime", {})
        date = f"{st.get('year'):04d}-{st.get('month',1):02d}-{st.get('day',1):02d}" if st.get("year") else None
        rec = {"date": date}
        for m in row.get("metrics", []):
            name = m.get("metric")
            dv = m.get("decimalValue", {}).get("value")
            iv = m.get("integerValue", {}).get("value")
            rec[name] = dv if dv is not None else iv
        out.append(rec)
    return out


def cmd_play_crash(args: argparse.Namespace) -> int:
    """Play crash rate + affected users, DAILY, last N days (Developer Reporting API)."""
    pkg = args.package or os.environ.get("GOOGLE_PLAY_PACKAGE", VAYU_PACKAGE_ANDROID)
    metrics = ["crashRate", "distinctUsers"]
    payload = _play_reporting_query("crashRateMetricSet", metrics, args.days, pkg)
    if "error" in payload:
        print(json.dumps(payload)); return 1
    print(json.dumps({"metric_set": "crashRate", "package": pkg,
                      "timeline": _flatten_reporting_rows(payload, metrics)}, indent=2, default=str))
    return 0


def cmd_play_anr(args: argparse.Namespace) -> int:
    """Play ANR rate + affected users, DAILY, last N days (Developer Reporting API)."""
    pkg = args.package or os.environ.get("GOOGLE_PLAY_PACKAGE", VAYU_PACKAGE_ANDROID)
    metrics = ["anrRate", "distinctUsers"]
    payload = _play_reporting_query("anrRateMetricSet", metrics, args.days, pkg)
    if "error" in payload:
        print(json.dumps(payload)); return 1
    print(json.dumps({"metric_set": "anrRate", "package": pkg,
                      "timeline": _flatten_reporting_rows(payload, metrics)}, indent=2, default=str))
    return 0


def cmd_play_stats(args: argparse.Namespace) -> int:
    """Play install/uninstall stats from the Play statistics GCS bucket.

    Install counts are NOT in the Developer Reporting API; they live as CSVs in
    the Play-managed GCS bucket `gs://pubsite_prod_rev_<developer_account_id>/`.
    Set GOOGLE_PLAY_STATS_BUCKET to that bucket name (without gs://) to enable.
    Find it in Play Console -> Download reports -> Statistics -> "Copy Cloud
    Storage URI". The service account already has devstorage.read_only.
    """
    bucket = args.bucket or os.environ.get("GOOGLE_PLAY_STATS_BUCKET")
    if not bucket:
        print(json.dumps({
            "error": "install stats need GOOGLE_PLAY_STATS_BUCKET",
            "why": "installs are GCS-only, not in the Developer Reporting API",
            "fix": "Play Console -> Download reports -> Statistics -> copy the gs:// bucket id "
                   "(pubsite_prod_rev_<numeric_dev_id>) into GOOGLE_PLAY_STATS_BUCKET in .env",
        }))
        return 2
    token = _play_access_token(PLAY_SCOPE_GCS)
    pkg = args.package or os.environ.get("GOOGLE_PLAY_PACKAGE", VAYU_PACKAGE_ANDROID)
    # Stats CSVs are namespaced stats/installs/installs_<pkg>_YYYYMM_overview.csv
    month = (args.month or dt.date.today().strftime("%Y%m"))
    obj = f"stats/installs/installs_{pkg}_{month}_overview.csv"
    from urllib.parse import quote
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(obj, safe='')}?alt=media"
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        print(json.dumps({"error": f"gcs {r.status_code}", "object": obj, "body": r.text[:300]}))
        return 1
    text = r.content.decode("utf-16") if r.content[:2] in (b"\xff\xfe", b"\xfe\xff") else r.text
    lines = [ln for ln in text.strip().split("\n") if ln]
    print(json.dumps({"object": obj, "month": month, "rows": len(lines) - 1,
                      "csv_head": lines[:5]}, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("apple-probe", help="Try each ASC key combo, report which auth.")

    p = sub.add_parser("apple-sales", help="Apple sales report.")
    p.add_argument("--date", required=True, help="YYYY-MM-DD for DAILY, YYYY-MM for MONTHLY.")
    p.add_argument("--frequency", default="DAILY", choices=["DAILY", "WEEKLY", "MONTHLY", "YEARLY"])
    p.add_argument("--limit", type=int, default=0, help="Max rows in output (0 = all).")

    p = sub.add_parser("apple-subs", help="Apple active-subscriber state (SUBSCRIPTION v1_4).")
    p.add_argument("--date", help="YYYY-MM-DD anchor (default today); steps back to newest with data.")
    p.add_argument("--lookback", type=int, default=8, help="Max days to step back for data.")

    p = sub.add_parser("apple-events", help="Apple subscription events (SUBSCRIPTION_EVENT v1_4).")
    p.add_argument("--date", help="YYYY-MM-DD anchor (default today); steps back to newest with data.")
    p.add_argument("--lookback", type=int, default=8, help="Max days to step back for data.")

    p = sub.add_parser("apple-reviews", help="Apple customer reviews, newest first.")
    p.add_argument("--app-id", dest="app_id", help="ASC app id, default Vayu prod.")
    p.add_argument("--limit", type=int, default=5)

    sub.add_parser("play-probe", help="Verify Play SA auth + find package.")

    p = sub.add_parser("play-crash", help="Play crash rate, DAILY (Developer Reporting API).")
    p.add_argument("--package")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("play-anr", help="Play ANR rate, DAILY (Developer Reporting API).")
    p.add_argument("--package")
    p.add_argument("--days", type=int, default=7)

    p = sub.add_parser("play-stats", help="Play install stats from GCS stats bucket.")
    p.add_argument("--package")
    p.add_argument("--bucket", help="GCS bucket id (default GOOGLE_PLAY_STATS_BUCKET).")
    p.add_argument("--month", help="YYYYMM (default current month).")

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
    if args.cmd == "apple-subs":
        return cmd_apple_subs(args)
    if args.cmd == "apple-events":
        return cmd_apple_events(args)
    if args.cmd == "apple-reviews":
        return cmd_apple_reviews(args)
    if args.cmd == "play-probe":
        return cmd_play_probe(args)
    if args.cmd == "play-reviews":
        return cmd_play_reviews(args)
    if args.cmd == "play-subscriptions":
        return cmd_play_subscriptions(args)
    if args.cmd == "play-crash":
        return cmd_play_crash(args)
    if args.cmd == "play-anr":
        return cmd_play_anr(args)
    if args.cmd == "play-stats":
        return cmd_play_stats(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
