"""Phase-2 memory recall at turn start (2026-07-05 orchestration master plan, item 8).

Problem: agents only remember what's baked into their system prompt
(LEARNINGS/MEMORY) plus whatever they think to search mid-turn. Session
logs, topic notes, FACTS entries, and recent daily breadcrumbs surface
only when the agent decides to go looking — which it often doesn't.
OpenClaw solves this with memory_search injection at turn start.

Fix: before each Discord-relayed turn, lexically match the incoming
message against a lightweight index of the shared vault (Sessions/,
Topics/, Company/FACTS.md, Company/DECISIONS.md) plus the agent's own
recent daily memory, and prepend the top-k snippets to the prompt. The
agent starts the turn already holding its most relevant memories.

Deviation from the masterplan's "smart-connections MCP" wording, on
purpose: that MCP is session-scoped (the agent can call it mid-turn; the
relay cannot call it pre-turn without spawning a CLI per message), and
embedding queries in-process would drag the plugin's transformer stack
into the relay. v1 is lexical scoring over the same corpus:
deterministic, ~ms per message on a warm index, zero new deps. A
semantic upgrade can replace _score() later without moving the seam.

Design constraints (same contract as session_rotation):
- FAIL-OPEN: any error → no injection, the turn proceeds untouched.
- Cheap: corpus index cached in-process with a TTL; per-message work is
  pure-python token scoring.
- Discord relay only (bot.py seam, beside the rotation check). Crons
  carry purpose-built prompts; web_chat can adopt later.
- Injection is bounded (default 1200 chars) and clearly labelled as
  auto-retrieved, possibly stale.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("memory-recall")

ROOT = Path(__file__).parent
AGENTS_DIR = ROOT / "agents"

# Defaults — override via config.yaml defaults.memory_recall.{...}
DEFAULT_K = 4
DEFAULT_MAX_CHARS = 1_200
DEFAULT_MIN_MESSAGE_CHARS = 20
INDEX_TTL_S = 600           # rebuild the corpus index at most every 10 min
SESSIONS_LOOKBACK_DAYS = 30
DAILY_LOOKBACK_DAYS = 7
_SNIPPET_CHARS = 200
_MAX_FILE_LINES = 40        # read only the head of each vault file

# Small english + channel-noise stopword set. "continue"/"go"/"ok" kill
# empty recalls on bare acks and steering words.
_STOPWORDS = frozenset(
    """a an and are as at be by can could did do does for from get got had has
    have he her here hi hey hello his how i if in into is it its just let lets
    me more most my no not now of off ok okay on only or other our out over
    own please she should so some than that the their them then there they
    this to too up us very was we were what when where which who why will
    with would yes you your yeah yep thanks thank continue go going stop done
    also about again all any been before being both each few had make makes
    need needs new old one two see set use used using want wants""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]+")


def _tokens(text: str) -> set[str]:
    return {
        t for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= 2 and t not in _STOPWORDS
    }


class _Doc:
    __slots__ = ("label", "title_tokens", "tokens", "snippet", "weight")

    def __init__(self, label: str, title_tokens: set[str], tokens: set[str],
                 snippet: str, weight: float) -> None:
        self.label = label
        self.title_tokens = title_tokens
        self.tokens = tokens | title_tokens
        self.snippet = snippet
        self.weight = weight


def _head_lines(path: Path, n: int = _MAX_FILE_LINES) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return [next(f).rstrip("\n") for _ in range(n)]
    except StopIteration:
        pass
    except OSError:
        return []
    # short file — reread fully (cheap, it was < n lines)
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _clean_snippet(lines: list[str]) -> str:
    """First meaningful prose lines, skipping frontmatter and headings."""
    out: list[str] = []
    in_fm = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if i == 0 and s == "---":
            in_fm = True
            continue
        if in_fm:
            if s == "---":
                in_fm = False
            continue
        if not s or s.startswith("#"):
            continue
        out.append(s)
        if sum(len(o) for o in out) >= _SNIPPET_CHARS:
            break
    return " ".join(out)[:_SNIPPET_CHARS]


def _age_weight(path: Path) -> float:
    """Recency weight from the YYYY-MM-DD prefix of a session filename."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.stem)
    if not m:
        return 0.8
    try:
        age = (date.today() - date.fromisoformat(m.group(1))).days
    except ValueError:
        return 0.8
    if age <= 7:
        return 1.0
    if age <= 14:
        return 0.85
    return 0.7


def _build_index(agent_name: str, vault: Path | None) -> list[_Doc]:
    docs: list[_Doc] = []

    if vault and vault.is_dir():
        # Sessions/ — one doc per file, recent window only.
        cutoff = date.today() - timedelta(days=SESSIONS_LOOKBACK_DAYS)
        sess = vault / "Sessions"
        if sess.is_dir():
            for p in sess.glob("*.md"):
                m = re.match(r"(\d{4}-\d{2}-\d{2})", p.stem)
                if m:
                    try:
                        if date.fromisoformat(m.group(1)) < cutoff:
                            continue
                    except ValueError:
                        pass
                lines = _head_lines(p)
                docs.append(_Doc(
                    label=f"Sessions/{p.name}",
                    title_tokens=_tokens(p.stem),
                    tokens=_tokens(" ".join(lines)),
                    snippet=_clean_snippet(lines),
                    weight=_age_weight(p),
                ))

        # Topics/ — filename-weighted, head only.
        topics = vault / "Topics"
        if topics.is_dir():
            for p in topics.glob("*.md"):
                lines = _head_lines(p, 12)
                docs.append(_Doc(
                    label=f"Topics/{p.name}",
                    title_tokens=_tokens(p.stem),
                    tokens=_tokens(" ".join(lines)),
                    snippet=_clean_snippet(lines),
                    weight=0.9,
                ))

        # Company/FACTS.md — one doc per fact line (high signal density).
        facts = vault / "Company" / "FACTS.md"
        if facts.is_file():
            try:
                for ln in facts.read_text(encoding="utf-8",
                                          errors="replace").splitlines():
                    s = ln.strip().lstrip("-# ").strip()
                    if len(s) < 12 or ":" not in s:
                        continue
                    docs.append(_Doc(
                        label="Company/FACTS.md",
                        title_tokens=set(),
                        tokens=_tokens(s),
                        snippet=s[:_SNIPPET_CHARS],
                        weight=1.15,
                    ))
            except OSError:
                pass

        # Company/DECISIONS.md — one doc per "## " section head.
        decisions = vault / "Company" / "DECISIONS.md"
        if decisions.is_file():
            try:
                text = decisions.read_text(encoding="utf-8", errors="replace")
                for chunk in re.split(r"\n(?=## )", text):
                    chunk = chunk.strip()
                    if len(chunk) < 40 or not chunk.startswith("## "):
                        continue
                    lines = chunk.splitlines()
                    docs.append(_Doc(
                        label="Company/DECISIONS.md",
                        title_tokens=_tokens(lines[0]),
                        tokens=_tokens(" ".join(lines[:12])),
                        snippet=(lines[0].lstrip("# ").strip() + " — " +
                                 _clean_snippet(lines[1:]))[:_SNIPPET_CHARS],
                        weight=1.0,
                    ))
            except OSError:
                pass

    # Agent's own daily memory — "## " sections of the last N days.
    mem_dir = AGENTS_DIR / agent_name / "memory"
    if mem_dir.is_dir():
        for i in range(DAILY_LOOKBACK_DAYS):
            d = date.today() - timedelta(days=i)
            p = mem_dir / f"{d.isoformat()}.md"
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for chunk in re.split(r"\n(?=## )", text):
                chunk = chunk.strip()
                if len(chunk) < 60:
                    continue
                lines = chunk.splitlines()
                head = lines[0].lstrip("# ").strip()
                docs.append(_Doc(
                    label=f"memory/{p.name}",
                    title_tokens=_tokens(head),
                    tokens=_tokens(" ".join(lines[:15])),
                    snippet=(head + " — " + _clean_snippet(lines[1:]))[:_SNIPPET_CHARS],
                    weight=1.1 if i <= 1 else 1.0,
                ))
    return docs


# ---------------------------------------------------------------------------
# Index cache: {agent_name: (built_at_monotonic, docs, df)}
_cache: dict[str, tuple[float, list[_Doc], dict[str, int]]] = {}


def _index_for(agent_name: str) -> tuple[list[_Doc], dict[str, int]]:
    now = time.monotonic()
    hit = _cache.get(agent_name)
    if hit and now - hit[0] < INDEX_TTL_S:
        return hit[1], hit[2]
    vault_env = os.environ.get("VAULT_PATH")
    vault = Path(vault_env) if vault_env else None
    docs = _build_index(agent_name, vault)
    df: dict[str, int] = {}
    for doc in docs:
        for t in doc.tokens:
            df[t] = df.get(t, 0) + 1
    _cache[agent_name] = (now, docs, df)
    return docs, df


def _score(q: set[str], doc: _Doc, df: dict[str, int]) -> tuple[float, int]:
    """(weighted score, distinct matched tokens). Rare tokens count more;
    filename/heading hits count double."""
    score = 0.0
    matched = 0
    for t in q:
        if t in doc.tokens:
            matched += 1
            w = 1.0 / (1.0 + math.log1p(df.get(t, 1)))
            if t in doc.title_tokens:
                w *= 2.0
            score += w
    return score * doc.weight, matched


def build(agent_name: str, message: str, cfg: dict | None) -> str | None:
    """Return a compact recall block for `message`, or None.

    Caller contract (bot.py): non-None → prepend to this turn's prompt
    (below any rotation note, above the operator message).
    """
    try:
        cfg = cfg or {}
        if not cfg.get("enabled", True):
            return None
        if len(message or "") < int(cfg.get("min_message_chars",
                                            DEFAULT_MIN_MESSAGE_CHARS)):
            return None
        q = _tokens(message)
        if len(q) < 2:
            return None

        docs, df = _index_for(agent_name)
        if not docs:
            return None

        scored: list[tuple[float, _Doc]] = []
        for doc in docs:
            s, matched = _score(q, doc, df)
            # Noise floor: demand ≥2 distinct matched tokens, or a single
            # near-unique long token (ids, filenames, project names).
            if matched >= 2 or (matched == 1 and s >= 0.9):
                scored.append((s, doc))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])

        k = int(cfg.get("k", DEFAULT_K))
        max_chars = int(cfg.get("max_chars", DEFAULT_MAX_CHARS))
        lines: list[str] = []
        seen: set[str] = set()
        budget = max_chars
        for s, doc in scored:
            key = doc.label + doc.snippet[:40]
            if key in seen:
                continue
            seen.add(key)
            line = f"- {doc.label}: {doc.snippet}" if doc.snippet else f"- {doc.label}"
            if len(line) > budget:
                break
            lines.append(line)
            budget -= len(line) + 1
            if len(lines) >= k:
                break
        if not lines:
            return None

        block = (
            "[memory recall — auto-retrieved notes matching this message; "
            "may be stale or off-target, verify before relying on them]\n"
            + "\n".join(lines)
            + "\n[/memory recall]"
        )
        log.info("[%s] recall injected %d note(s) (%d chars)",
                 agent_name, len(lines), len(block))
        return block
    except Exception as e:  # noqa: BLE001 — recall must never break a turn
        log.warning("[%s] memory recall failed open: %s", agent_name, e)
        return None
