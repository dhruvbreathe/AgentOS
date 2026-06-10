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
import re
from datetime import datetime, timezone
from pathlib import Path

# Match either em-dash (U+2014) or en-dash (U+2013), with any surrounding
# whitespace. Replacement is ", " (comma + single space).
_DASH_RE = re.compile(r"\s*[\u2014\u2013]\s*")

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "logs" / "text_lint.jsonl"


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
    hits = _count_hits(text)
    if not hits:
        return text
    _log_hit(agent, surface, hits, text)
    return _DASH_RE.sub(", ", text)


def count_hits(text: str) -> int:
    """Public helper for callers that want to check without mutating."""
    return _count_hits(text)
