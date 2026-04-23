"""Recall helper — grep across every surface AgentOS persists to.

Searches: trajectory JSONL, per-agent memory/, per-agent LEARNINGS.md,
shared/, and (if --vault) the Obsidian vault's Sessions/ + Topics/.

Operator-facing. Meant to answer questions like "the last time I asked
an agent about X, what did it say" without having to remember which
agent or which day.

Usage:
    python scripts/recall.py "crontab"
    python scripts/recall.py "supabase rls" --agent backend-developer
    python scripts/recall.py "ios release" --vault
    python scripts/recall.py "Tempo" --since 7
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
TRAJ_ROOT = ROOT / "logs" / "trajectories"
SHARED = ROOT / "shared"
_VAULT_RAW = os.environ.get("VAULT_PATH")
if not _VAULT_RAW:
    raise RuntimeError("VAULT_PATH is not set — copy .env.example to .env and fill it in.")
VAULT = Path(_VAULT_RAW)


def _iter_files_under(dir: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not dir.exists():
        return []
    return [p for p in dir.rglob("*") if p.is_file() and p.suffix in suffixes]


def _mtime_after(path: Path, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    return datetime.fromtimestamp(path.stat().st_mtime) >= cutoff


def _search_jsonl(path: Path, pattern: re.Pattern) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    with path.open() as f:
        for i, line in enumerate(f, start=1):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = str(rec.get("content", ""))
            if pattern.search(content):
                snippet = content[:160].replace("\n", " ")
                hits.append((i, f"[{rec.get('type','?')}] {snippet}"))
    return hits


def _search_md(path: Path, pattern: re.Pattern) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text()
    except Exception:
        return hits
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            hits.append((i, line.strip()[:160]))
    return hits


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("query", help="Text pattern (regex-safe)")
    p.add_argument("--agent", help="Restrict to one agent")
    p.add_argument("--vault", action="store_true",
                   help="Also search Sessions/ and Topics/ in the Obsidian vault")
    p.add_argument("--shared", action="store_true",
                   help="Include shared/ docs")
    p.add_argument("--since", type=int, default=None,
                   help="Only files modified in the last N days")
    p.add_argument("--max", type=int, default=80, help="Max results per surface")
    p.add_argument("-i", "--ignore-case", action="store_true", default=True)
    args = p.parse_args()

    flags = re.IGNORECASE if args.ignore_case else 0
    pattern = re.compile(re.escape(args.query), flags)
    cutoff = datetime.now() - timedelta(days=args.since) if args.since else None

    agent_dirs = (
        [AGENTS / args.agent] if args.agent
        else [d for d in AGENTS.iterdir()
              if d.is_dir() and not d.name.startswith((".", "_"))
              and (d / "agent.yaml").exists()]
    )

    total = 0

    # 1. Trajectories
    traj_dirs = (
        [TRAJ_ROOT / args.agent] if args.agent else
        [d for d in TRAJ_ROOT.iterdir() if d.is_dir()] if TRAJ_ROOT.exists() else []
    )
    for d in traj_dirs:
        for path in sorted(d.glob("*.jsonl"))[-args.max:]:
            if not _mtime_after(path, cutoff):
                continue
            hits = _search_jsonl(path, pattern)
            if hits:
                print(f"\n📋 trajectory: {path.relative_to(ROOT)}")
                for lineno, line in hits[:5]:
                    print(f"  {lineno}: {line}")
                if len(hits) > 5:
                    print(f"  ({len(hits)-5} more)")
                total += len(hits)

    # 2. Per-agent memory + LEARNINGS
    for agent_dir in sorted(agent_dirs, key=lambda d: d.name):
        paths = []
        paths.extend(sorted((agent_dir / "memory").glob("*.md"))
                     if (agent_dir / "memory").exists() else [])
        lp = agent_dir / "LEARNINGS.md"
        if lp.exists():
            paths.append(lp)
        for path in paths:
            if not _mtime_after(path, cutoff):
                continue
            hits = _search_md(path, pattern)
            if hits:
                print(f"\n🧠 {agent_dir.name}: {path.relative_to(ROOT)}")
                for lineno, line in hits[:5]:
                    print(f"  {lineno}: {line}")
                if len(hits) > 5:
                    print(f"  ({len(hits)-5} more)")
                total += len(hits)

    # 3. Shared docs (optional)
    if args.shared:
        for path in _iter_files_under(SHARED, (".md",)):
            hits = _search_md(path, pattern)
            if hits:
                print(f"\n📎 shared: {path.relative_to(ROOT)}")
                for lineno, line in hits[:5]:
                    print(f"  {lineno}: {line}")
                total += len(hits)

    # 4. Obsidian vault (optional)
    if args.vault:
        for sub in ("Sessions", "Topics", "Company"):
            for path in _iter_files_under(VAULT / sub, (".md",)):
                if not _mtime_after(path, cutoff):
                    continue
                hits = _search_md(path, pattern)
                if hits:
                    print(f"\n📓 vault: {path.relative_to(VAULT)}")
                    for lineno, line in hits[:5]:
                        print(f"  {lineno}: {line}")
                    if len(hits) > 5:
                        print(f"  ({len(hits)-5} more)")
                    total += len(hits)

    print(f"\n── {total} total hit(s) for '{args.query}' ──")
    return 0


if __name__ == "__main__":
    sys.exit(main())
