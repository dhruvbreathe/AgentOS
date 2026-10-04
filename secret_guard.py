"""Secret guard: keep .env values out of transcripts, mirrors and tool calls.

Added 2026-10-03 (Sentry, task T-cacd32) after backend heartbeat runs wrote
live .env secrets into trajectories, CLI transcripts and the Agent NS
session_store mirror.

Three layers, all in this module so call sites stay one-liners:

1. Redaction (write side). ``redact(obj)`` walks str/dict/list and replaces
   every known secret value with ``[REDACTED:<NAME>]``. Known values come
   from the repo .env (reloaded when its mtime changes) plus any secret-ish
   os.environ var, and a few high-confidence token shapes (JWTs, Discord
   webhook tokens, GitHub/Supabase/OpenAI-style keys). Used by:
     - relay.TrajectoryLogger._write        (logs/trajectories/)
     - session_store_supabase / _file append (agents.session_store mirror)
     - web_chat._append_history              (logs/web_chat/)
   ``redact_entries`` is the transcript-entry variant: it also DROPS any
   signed thinking block whose text held a secret, because rewriting signed
   thinking would invalidate the signature on resume.

2. PreToolUse hook (``pre_tool_use``). Denies, before execution:
     - Read / Grep / Edit / Write aimed at .env* (not .env.example etc.),
       *.p8, service-account JSON, the vault credentials note,
       logs/dashboard-creds.txt
     - Bash that dumps the environment or a secrets file: bare env/printenv,
       set/export -p/declare -p, cat|head|tail|grep|... on .env*,
       echo/printf of a secret-ish $VAR, curl -v/--trace on an authed call,
       python print(os.environ)
     - any tool input that contains a literal known secret value (e.g.
       `export SUPABASE_SERVICE_ROLE_KEY=eyJ...`, a webhook URL pasted into
       a curl, a key written into a note)
   The CLI writes tool results to its own transcript before any hook of ours
   can touch them, so blocking the call is the only fix for that copy.

3. PostToolUse hook (``post_tool_use``). If a tool result still carried a
   known value, logs NAMES only to logs/secret_guard.jsonl and tells the
   model not to repeat it (stops the copy cascading into notes/memory).

Mode: env AGENTOS_SECRET_GUARD = deny (default) | warn | off.
Never prints or logs a secret value; logs carry names only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("secret-guard")

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
EVENT_LOG = ROOT / "logs" / "secret_guard.jsonl"

SECRETISH = re.compile(
    r"KEY|TOKEN|SECRET|PASS|PW|WEBHOOK|PRIVATE|CREDENTIAL|AUTH|DSN", re.I
)
# Names that look secret-ish but hold non-secrets (paths, public site keys).
_NOT_SECRET_NAMES = re.compile(
    r"_PATH(_|$)|_KEY_ID$|SITE_KEY$|^SENTRY_ORG$|^(OLD)?PWD$|_DIR$|_FILE$", re.I
)
_MIN_LEN = 12

# High-confidence shapes for values NOT in .env (other projects' keys, new
# tokens an agent minted mid-run). Conservative on purpose.
_SHAPES: list[tuple[str, re.Pattern[str]]] = [
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}")),
    ("DISCORD_WEBHOOK", re.compile(
        r"(?<=discord\.com/api/webhooks/)(\d{15,25}/[A-Za-z0-9_-]{40,})"
        r"|(?<=discordapp\.com/api/webhooks/)(\d{15,25}/[A-Za-z0-9_-]{40,})")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("SUPABASE_KEY", re.compile(r"\b(?:sbp_[a-f0-9]{30,}|sb_secret_[A-Za-z0-9_-]{20,})\b")),
    ("API_KEY", re.compile(r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{30,}|xai-[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_-]{35})\b")),
    ("PRIVATE_KEY", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
]

_lock = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "pairs": [], "values": {}}


# ---- secret inventory ---------------------------------------------------------


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.replace("export ", "").strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def _is_secret(name: str, value: str) -> bool:
    return (
        len(value) >= _MIN_LEN
        and bool(SECRETISH.search(name))
        and not _NOT_SECRET_NAMES.search(name)
        and not value.startswith(("/", "~", "./"))  # filesystem paths
    )


def secrets() -> dict[str, str]:
    """name -> value for every known secret. Cached; reloads on .env change."""
    try:
        mt = ENV_FILE.stat().st_mtime
    except OSError:
        mt = None
    with _lock:
        if _cache["mtime"] == mt and _cache["values"]:
            return _cache["values"]
        vals: dict[str, str] = {}
        for k, v in os.environ.items():
            if _is_secret(k, v):
                vals[k] = v
        for k, v in _parse_env(ENV_FILE).items():
            if _is_secret(k, v):
                vals[k] = v
        # value -> name pairs, longest first so a value that contains another
        # (webhook URL containing a token) is replaced whole.
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for k, v in sorted(vals.items(), key=lambda kv: -len(kv[1])):
            for form in (v, json.dumps(v)[1:-1], v.replace("/", "\\/")):
                if form not in seen:
                    seen.add(form)
                    pairs.append((form, k))
        _cache.update(mtime=mt, values=vals, pairs=pairs)
        return vals


def _pairs() -> list[tuple[str, str]]:
    secrets()
    return _cache["pairs"]


def find_names(text: str) -> list[str]:
    """Names of known secrets whose value appears in text. Never values."""
    if not text:
        return []
    return sorted({name for form, name in _pairs() if form in text})


# ---- redaction ----------------------------------------------------------------


def redact_str(s: str) -> str:
    if not s or len(s) < _MIN_LEN:
        return s
    for form, name in _pairs():
        if form in s:
            s = s.replace(form, f"[REDACTED:{name}]")
    for label, rx in _SHAPES:
        if rx.search(s):
            s = rx.sub(f"[REDACTED:{label}]", s)
    return s


def redact(obj: Any) -> Any:
    """Recursively redact str leaves of dict/list/tuple. Keys untouched."""
    try:
        if isinstance(obj, str):
            return redact_str(obj)
        if isinstance(obj, dict):
            return {k: redact(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [redact(v) for v in obj]
    except Exception as e:  # never break a writer over redaction
        log.warning("redact failed: %s", type(e).__name__)
    return obj


def _strip_secret_thinking(obj: Any) -> Any:
    """Drop signed thinking blocks that contain a secret (can't be rewritten
    without invalidating the signature)."""
    if isinstance(obj, dict):
        return {k: _strip_secret_thinking(v) for k, v in obj.items()}
    if isinstance(obj, list):
        out = []
        for v in obj:
            if (
                isinstance(v, dict)
                and v.get("type") == "thinking"
                and redact_str(v.get("thinking") or "") != (v.get("thinking") or "")
            ):
                continue
            out.append(_strip_secret_thinking(v))
        if obj and not out:
            out = [{"type": "text", "text": "[thinking removed: contained a secret]"}]
        return out
    return obj


def redact_entries(entries: list[Any]) -> list[Any]:
    """Transcript-entry redaction for session_store mirrors."""
    if not entries:
        return entries
    try:
        return [redact(_strip_secret_thinking(e)) for e in entries]
    except Exception as e:
        log.warning("redact_entries failed: %s", type(e).__name__)
        return entries


# ---- tool-call guard ----------------------------------------------------------


def _mode() -> str:
    m = (os.environ.get("AGENTOS_SECRET_GUARD") or "deny").strip().lower()
    return m if m in ("deny", "warn", "off") else "deny"


_SAFE_ENV_SUFFIX = re.compile(r"\.(example|sample|template|dist|defaults)$", re.I)
_ENV_FILE_RX = re.compile(r"(?:^|/)\.env(?:\.[\w.-]+)?$")
_SENSITIVE_FILE_RX = re.compile(
    r"(?:\.p8$|\.pem$|\.p12$|\.keystore$|\.jks$|service[-_]account[^/]*\.json$"
    r"|/dashboard-creds\.txt$|/secrets\.properties$|/credentials\.md$|/\.netrc$"
    r"|/\.pgpass$|/\.npmrc$|/\.pypirc$|/id_(?:rsa|ed25519|ecdsa)$)",
    re.I,
)


def is_sensitive_path(p: str) -> bool:
    if not p:
        return False
    p = p.strip().strip("'\"")
    if _ENV_FILE_RX.search(p):
        return not _SAFE_ENV_SUFFIX.search(p)
    return bool(_SENSITIVE_FILE_RX.search(p))


_SECRET_VAR = r"[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASS|PASSWORD|PW|WEBHOOK|PRIVATE|CREDENTIAL|AUTH|DSN)[A-Z0-9_]*"
_READERS = set(
    "cat bat less more head tail grep egrep fgrep rg ag awk gawk sed strings "
    "xxd od hexdump nl sort uniq cut tr column jq yq base64 pbcopy tee diff "
    "cmp comm paste fold vim vi nano code".split()
)
_BASH_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("dumps the environment (env/printenv)",
     re.compile(r"(?:^|[;&|(`]|\$\()\s*(?:sudo\s+)?(?:printenv\b|env\s*(?:$|[|;&>)`]|-0\b|--null\b))")),
    ("dumps shell variables (set/export -p/declare -p)",
     re.compile(r"(?:^|[;&|(`])\s*(?:set\s*(?:$|[|;&>])|export\s+-p\b|export\s*(?:$|[|;&>])|declare\s+-[a-zA-Z]*[px])")),
    ("prints a secret-ish variable",
     re.compile(r"\b(?:echo|printf|print)\b[^|;&\n]*\$\{?(?!#)(?!(?:OLD)?PWD\b)" + _SECRET_VAR + r"(?![A-Z0-9_]*:\+)")),
    ("curl verbose/trace on an authenticated request",
     re.compile(r"\bcurl\b(?=[^|;&\n]*(?:\s-[a-zA-Z]*v\b|--verbose|--trace))(?=[^|;&\n]*(?:-H\s*['\"]?(?:authorization|apikey|x-api-key|[a-z-]*token)|\s-u\s|--user\b))", re.I)),
    ("dumps os.environ",
     re.compile(r"(?:print|pprint|json\.dumps|repr)\s*\(\s*(?:dict\s*\(\s*)?os\.environ\s*\)")),
]
_NAMES_ONLY = re.compile(
    r"cut\s+-d\s*['\"]?=['\"]?\s+-f\s*1\b|\bgrep\s+-[a-zA-Z]*[clqL]"
    r"|\bgrep\s+-[a-zA-Z]*o[a-zA-Z]*\s+(?:-E\s+)?['\"]\^\[?[A-Z_]"
    r"|sed\s+['\"]?s/=\.\*//"
)
# presence tests ([ -n "$X" ], [[ -z $X ]]) don't print the value
_PRESENCE = re.compile(r"\[\[?\s*-[zn]\s+\"?\$\{?\w+\}?\"?\s*\]\]?")


def _bash_violation(cmd: str) -> str | None:
    cmd = _PRESENCE.sub(" ", cmd)
    for why, rx in _BASH_RULES:
        if rx.search(cmd):
            return why
    # A reader command (cat/grep/head/...) whose own pipeline segment names a
    # secrets file. `source .env && curl ... | jq` stays allowed: jq's segment
    # doesn't name the file. Names-only forms (cut -d= -f1, grep -c) pass.
    for pipeline in re.split(r"\|\||&&|[;&\n]", cmd):
        if _NAMES_ONLY.search(pipeline):
            continue  # e.g. grep X .env | cut -d= -f1
        for seg in pipeline.split("|"):
            try:
                toks = shlex.split(seg, posix=True)
            except ValueError:
                toks = seg.split()
            while toks and (
                toks[0] in ("sudo", "command", "nohup", "time")
                or re.match(r"^[A-Za-z_]\w*=", toks[0])
            ):
                toks = toks[1:]
            if not toks or os.path.basename(toks[0]) not in _READERS:
                continue
            for t in toks[1:]:
                if is_sensitive_path(t):
                    return f"reads a secrets file ({os.path.basename(t)})"
    return None


_HINT = (
    " Secrets reach tool output -> transcripts -> the Supabase mirror. "
    "Reference them as $VAR inside the command instead of printing them. "
    "To check presence: `test -n \"$VAR\" && echo set`, or "
    "`cut -d= -f1 .env` / `grep -c '^VAR=' .env` for names only. "
    "Override (operator only): AGENTOS_SECRET_GUARD=warn."
)


def check_tool_call(tool_name: str, tool_input: dict) -> str | None:
    """Return a human reason if this call would expose a secret, else None."""
    ti = tool_input or {}
    if tool_name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "NotebookRead"):
        p = ti.get("file_path") or ti.get("notebook_path") or ""
        if is_sensitive_path(str(p)):
            return f"{tool_name} on a secrets file ({os.path.basename(str(p))})."
    if tool_name == "Grep":
        p = str(ti.get("path") or "")
        g = str(ti.get("glob") or "")
        if is_sensitive_path(p) or ".env" in g:
            if ti.get("output_mode") in (None, "content"):
                return "Grep content over a secrets file."
    if tool_name == "Bash":
        why = _bash_violation(str(ti.get("command") or ""))
        if why:
            return f"Bash command {why}."
    try:
        blob = json.dumps(ti, ensure_ascii=False)
    except Exception:
        blob = str(ti)
    names = find_names(blob)
    if names:
        return f"tool input contains the literal value of {', '.join(names[:5])}."
    return None


def _event(kind: str, **fields: Any) -> None:
    try:
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "kind": kind, **fields}
        with EVENT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def make_hooks(agent_name: str = ""):
    """(pre_tool_use, post_tool_use) async hooks for ClaudeAgentOptions."""

    async def pre_tool_use(input_data, tool_use_id, context):
        mode = _mode()
        if mode == "off":
            return {}
        tool = input_data.get("tool_name", "")
        try:
            why = check_tool_call(tool, input_data.get("tool_input") or {})
        except Exception as e:
            log.warning("secret guard check failed: %s", type(e).__name__)
            return {}
        if not why:
            return {}
        _event("pre_block" if mode == "deny" else "pre_warn",
               agent=agent_name, tool=tool, reason=redact_str(why))
        if mode == "warn":
            return {"systemMessage": f"secret-guard warning: {why}{_HINT}"}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"secret-guard: {why}{_HINT}",
            }
        }

    async def post_tool_use(input_data, tool_use_id, context):
        if _mode() == "off":
            return {}
        try:
            resp = input_data.get("tool_response")
            blob = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False, default=str)
            names = find_names(blob)
        except Exception:
            return {}
        if not names:
            return {}
        _event("post_leak", agent=agent_name, tool=input_data.get("tool_name", ""),
               names=names)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    "secret-guard: that tool result contained the value of "
                    f"{', '.join(names)}. Do not repeat, quote, store or pass it "
                    "on anywhere (notes, memory, messages, commands); refer to "
                    "the variable name only. Avoid commands that print secrets."
                ),
            }
        }

    return pre_tool_use, post_tool_use
