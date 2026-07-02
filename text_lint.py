"""text_lint.py — outbound text linter (em-dash strip + telemetry).

Operator directive 2026-05-19: NO em-dashes or en-dashes in any output that
leaves Claude. This module is the runtime enforcement layer that catches
drift between prompt-load time and webhook-post time.

Public API:
    sanitize(text, agent=None, surface=None) -> str
        Returns text with `—` and `–` (and surrounding whitespace) replaced
        by `", "`. Logs every hit to logs/text_lint.jsonl so we can see which
        agent and which surface is still drifting.

Replacement rule:
    r"\\s*[—–]\\s*"  -->  ", "
    (collapse whitespace around the dash and substitute with comma + space).
    This is grammatically safe in ~95% of cases. The remaining 5% (em-dash
    used as a sentence-end break) reads as a slightly long comma clause,
    which is fine in chat.

Edge case: triple-em-dashes in horizontal rules `---` ARE the markdown rule
and are NOT touched (we only target the unicode em-dash U+2014 and en-dash
U+2013, not ASCII hyphens).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Match either em-dash (U+2014) or en-dash (U+2013), with any surrounding
# whitespace. Replacement is ", " (comma + single space).
_DASH_RE = re.compile(r"\s*[\u2014\u2013]\s*")

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "logs" / "text_lint.jsonl"

# ---------------------------------------------------------------------------
# Secret egress scrubbing (audit A4, 2026-07-01).
#
# env_passthrough injects live secrets into agent Bash envs; nothing stopped
# a turn from `printenv`-ing them into Discord. sanitize() is the single
# choke point every outbound surface already flows through (relay sink,
# cron webhook, send_to_agent), so the scrub lives here.
#
# Strategy: exact-value replacement. We collect the VALUES of env vars whose
# NAMES look secret-bearing (KEY/TOKEN/SECRET/...), from both os.environ and
# the repo .env, and replace any occurrence in outbound text with
# [REDACTED:<VAR_NAME>]. Values shorter than _SECRET_MIN_LEN are skipped to
# avoid mangling ordinary words. Discord webhook tokens inside URLs are also
# redacted by pattern. Never raises; on any failure text passes unscrubbed
# rather than undelivered.
# ---------------------------------------------------------------------------

_SECRET_MIN_LEN = 8
_SECRET_NAME_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|_AUTH|WEBHOOK_URL|"
    r"_JSON|_DSN|POOLER)", re.I,
)
# Env names that match the pattern above but are NOT secret values.
_SECRET_NAME_ALLOWLIST = {
    "APP_STORE_CONNECT_KEY_ID",            # key *identifier*, not key material
    "APP_STORE_CONNECT_KEY_ID_VAYU_PROMO",
    "APP_STORE_CONNECT_KEY_ID_PRANA",
    "GOOGLE_PLAY_SERVICE_ACCOUNT_KEY_ID",
    "SENTRY_ORG",
}
_WEBHOOK_TOKEN_RE = re.compile(
    r"(https?://(?:discord\.com|discordapp\.com)/api/webhooks/\d+/)"
    r"[A-Za-z0-9_.\-]{30,}"
)

_secret_map: dict[str, str] | None = None  # value -> env var name


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if name and value:
                out[name] = value
    except Exception:
        pass
    return out


def _build_secret_map() -> dict[str, str]:
    candidates: dict[str, str] = {}
    candidates.update(_parse_env_file(ROOT / ".env"))
    candidates.update({k: v for k, v in os.environ.items() if v})
    secret_map: dict[str, str] = {}
    for name, value in candidates.items():
        if name in _SECRET_NAME_ALLOWLIST:
            continue
        if not _SECRET_NAME_RE.search(name):
            continue
        if len(value) < _SECRET_MIN_LEN:
            continue
        secret_map[value] = name
        # JSON-valued secrets (service accounts): redact embedded private_key
        # material in both raw (\n-escaped) and unescaped forms, so a
        # jq-extracted key still gets caught.
        if value.lstrip().startswith("{"):
            try:
                blob = json.loads(value)
                pk = blob.get("private_key") if isinstance(blob, dict) else None
                if isinstance(pk, str) and len(pk) >= _SECRET_MIN_LEN:
                    secret_map[pk] = f"{name}.private_key"
                    secret_map[pk.replace("\n", "\\n")] = f"{name}.private_key"
            except Exception:
                pass
    return secret_map


def scrub_secrets(text: str, agent: str | None = None,
                  surface: str | None = None) -> str:
    """Replace known secret values in outbound text with [REDACTED:<NAME>].

    Exact-value matching against secret-named env vars (os.environ + .env),
    plus Discord webhook token URLs by pattern. Best-effort: any internal
    failure returns the original text so delivery never breaks.
    """
    if not text:
        return text
    try:
        global _secret_map
        if _secret_map is None:
            _secret_map = _build_secret_map()
        hit_names: list[str] = []
        # Longest values first so a substring secret can't mask a longer one.
        for value in sorted(_secret_map, key=len, reverse=True):
            if value in text:
                name = _secret_map[value]
                text = text.replace(value, f"[REDACTED:{name}]")
                hit_names.append(name)
        text, n_hook = _WEBHOOK_TOKEN_RE.subn(r"\1[REDACTED:webhook_token]", text)
        if n_hook:
            hit_names.append("webhook_token_url")
        if hit_names:
            _log_hit(agent, f"{surface or 'unknown'}/secret-scrub",
                     len(hit_names), "redacted: " + ", ".join(hit_names))
    except Exception:
        # Scrub failure must never block the message.
        pass
    return text


def _count_hits(text: str) -> int:
    return sum(1 for _ in _DASH_RE.finditer(text or ""))


def _log_hit(agent: str | None, surface: str | None, hits: int, sample: str) -> None:
    """Append a single JSON line per offending message. Best-effort; never
    raises (lint must not break message delivery)."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "agent": agent or "unknown",
            "surface": surface or "unknown",
            "hits": hits,
            # 240-char preview centred on the first hit, so we can eyeball
            # which patterns are leaking through.
            "sample": sample[:240],
        }
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Telemetry failure must never block the message.
        pass


def sanitize(text: str, agent: str | None = None, surface: str | None = None) -> str:
    """Strip em-dashes / en-dashes from `text` and return the cleaned string.

    Args:
        text: raw outbound text.
        agent: agent name (e.g. "main", "marketing") for telemetry.
        surface: where this text was about to land (e.g. "discord_sink",
            "cron_webhook", "send_to_agent").
    """
    if not text:
        return text
    # Secret egress scrub first (A4) — every sanitize() caller gets it.
    text = scrub_secrets(text, agent=agent, surface=surface)
    hits = _count_hits(text)
    if not hits:
        return text
    _log_hit(agent, surface, hits, text)
    return _DASH_RE.sub(", ", text)


def count_hits(text: str) -> int:
    """Public helper for callers that want to check without mutating."""
    return _count_hits(text)
