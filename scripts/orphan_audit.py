#!/usr/bin/env python3
"""Orphan-note audit.

Scans the vault for markdown notes that have zero inbound wiki-links from any
other note. Excludes Sessions/, Archive/, Templates/, and dotfolders.

Writes Company/Orphans.md.

Usage:
    python scripts/orphan_audit.py
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

VAULT = Path(os.environ.get("VAULT_PATH", "/Users/celainc/Documents/Vayu/Vayu"))
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]+)?\]\]")

EXCLUDE_DIRS = {"Sessions", "Archive", "Templates", "Inbox", "OCR",
                "Conversations", ".obsidian", ".smart-env", ".git",
                ".claude", ".mcp_data"}


def iter_notes():
    for root, dirs, files in os.walk(VAULT):
        # Prune
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        rel_root = Path(root).relative_to(VAULT)
        for f in files:
            if not f.endswith(".md"):
                continue
            yield Path(root) / f


def main():
    inbound: defaultdict[str, list[str]] = defaultdict(list)
    all_notes: list[Path] = []

    for p in iter_notes():
        all_notes.append(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in WIKILINK_RE.finditer(text):
            inbound[m.group(1).strip().lower()].append(p.stem)

    # Also count inbound from Sessions/ — sessions count as a backlink source
    sessions_dir = VAULT / "Sessions"
    if sessions_dir.exists():
        for p in sessions_dir.glob("*.md"):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in WIKILINK_RE.finditer(text):
                inbound[m.group(1).strip().lower()].append(p.stem)

    orphans: list[Path] = []
    for p in all_notes:
        if p.stem.lower() not in inbound or not inbound[p.stem.lower()]:
            orphans.append(p)

    # Sort: oldest first (least likely to still matter)
    orphans.sort(key=lambda p: p.stat().st_mtime)

    lines = [
        "---",
        "type: dashboard",
        "tags: [dashboard, orphan-notes]",
        f"generated: {datetime.now().strftime('%Y-%m-%dT%H:%M')}",
        "---",
        "",
        "# Orphan Notes",
        "",
        f"Notes outside `Sessions/`, `Archive/`, `Templates/` with **zero inbound "
        f"wiki-links**. Total: **{len(orphans)}**.",
        "",
        "Sessions and Archive don't count toward backlink scoring (sessions are "
        "ephemeral, archive is dead by design).",
        "",
        "_Regenerate: `python scripts/orphan_audit.py`._",
        "",
        "| Note | Folder | Last touched |",
        "|---|---|---|",
    ]
    for p in orphans[:200]:
        rel = p.relative_to(VAULT)
        folder = str(rel.parent) or "(root)"
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        lines.append(f"| [[{p.stem}]] | {folder} | {mtime} |")

    if len(orphans) > 200:
        lines.append("")
        lines.append(f"_…and {len(orphans) - 200} more._")

    target = VAULT / "Company" / "Orphans.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {target} ({len(orphans)} orphans)")


if __name__ == "__main__":
    main()
