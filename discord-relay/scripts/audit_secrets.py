#!/usr/bin/env python3
"""audit_secrets.py — scan repo + git history for leaked secrets.

Complements audit_skills.py (which scans for prompt injection in agent
writable files). This one sweeps for accidentally-committed API keys,
.env files in git, and common secret patterns in working tree + history.

Report-only. Doesn't rotate, doesn't delete. Operator reads + acts.

Usage:
    python scripts/audit_secrets.py                  # working tree only
    python scripts/audit_secrets.py --history        # also scan git log -p
    python scripts/audit_secrets.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Exclude dirs from working-tree scan
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    "logs", ".pytest_cache", ".mypy_cache", "dist", "build",
}

# Include extensions
INCLUDE_EXTS = {".py", ".sh", ".md", ".yaml", ".yml", ".json", ".js", ".mjs", ".ts"}

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("openai-key",     re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("anthropic-key",  re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("discord-bot",    re.compile(r"\b[MN][A-Za-z\d]{23}\.[\w-]{6}\.[\w-]{27,}\b")),
    ("discord-webhook",re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+")),
    ("github-pat",     re.compile(r"\bghp_[A-Za-z0-9]{36,}\b")),
    ("github-oauth",   re.compile(r"\bgho_[A-Za-z0-9]{36,}\b")),
    ("aws-access",     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("groq-key",       re.compile(r"\bgsk_[A-Za-z0-9]{40,}\b")),
    ("slack-bot",      re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key",     re.compile(r"\bsk_(live|test)_[A-Za-z0-9]{24,}\b")),
    ("supabase-jwt",   re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("generic-bearer", re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]([A-Za-z0-9_\-]{20,})['\"]")),
]


@dataclass
class Finding:
    location: str  # "working-tree" or "git-history"
    path: str
    line_no: int
    category: str
    snippet: str


def scan_file(path: Path) -> list[Finding]:
    out: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    for i, line in enumerate(text.splitlines(), 1):
        # Skip audit file itself — it contains the patterns
        if path.name == "audit_secrets.py":
            continue
        for cat, pat in SECRET_PATTERNS:
            if pat.search(line):
                snippet = line.strip()
                if len(snippet) > 140:
                    snippet = snippet[:137] + "..."
                out.append(Finding(
                    location="working-tree",
                    path=str(path.relative_to(ROOT)),
                    line_no=i,
                    category=cat,
                    snippet=snippet,
                ))
    return out


def walk_tree() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in INCLUDE_EXTS:
            continue
        out.append(p)
    return out


def scan_working_tree() -> list[Finding]:
    out: list[Finding] = []
    for p in walk_tree():
        out.extend(scan_file(p))
    return out


def scan_git_ignored_env() -> list[Finding]:
    """Flag if any .env file is tracked by git."""
    out: list[Finding] = []
    try:
        tracked = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, timeout=10
        )
    except Exception:
        return out
    for line in tracked.splitlines():
        name = Path(line).name
        if name == ".env" or name.startswith(".env."):
            out.append(Finding(
                location="working-tree",
                path=line,
                line_no=0,
                category=".env-tracked",
                snippet=f"{line} is in git — .env files should be gitignored",
            ))
    return out


def scan_history() -> list[Finding]:
    """Scan `git log -p` output for secret patterns."""
    out: list[Finding] = []
    try:
        log = subprocess.check_output(
            ["git", "log", "-p", "--all", "--no-color"],
            cwd=ROOT, text=True, timeout=60, errors="replace",
        )
    except Exception as e:
        print(f"warn: history scan failed: {e}", file=sys.stderr)
        return out
    current_commit = ""
    current_file = ""
    for line in log.splitlines():
        if line.startswith("commit "):
            current_commit = line.split()[1][:12]
        elif line.startswith("+++"):
            current_file = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            content = line[1:]
            for cat, pat in SECRET_PATTERNS:
                if pat.search(content):
                    snippet = content.strip()
                    if len(snippet) > 140:
                        snippet = snippet[:137] + "..."
                    out.append(Finding(
                        location=f"git-history@{current_commit}",
                        path=current_file,
                        line_no=0,
                        category=cat,
                        snippet=snippet,
                    ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Repo + git history secrets audit")
    ap.add_argument("--history", action="store_true", help="also scan git history")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    findings: list[Finding] = []
    findings.extend(scan_working_tree())
    findings.extend(scan_git_ignored_env())
    if args.history:
        findings.extend(scan_history())

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        if not findings:
            print("✅ no secrets found")
            return 0
        print(f"⚠️  {len(findings)} findings:\n")
        by_cat: dict[str, list[Finding]] = {}
        for f in findings:
            by_cat.setdefault(f.category, []).append(f)
        for cat, items in sorted(by_cat.items()):
            print(f"  {cat} ({len(items)})")
            for f in items[:5]:
                print(f"    {f.location}  {f.path}:{f.line_no}")
                print(f"        {f.snippet}")
            if len(items) > 5:
                print(f"    ... and {len(items) - 5} more")
            print()

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
