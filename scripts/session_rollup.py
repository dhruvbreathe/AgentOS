#!/usr/bin/env python3
"""Monthly session log rollup.

Walks the vault Sessions/ folder for a given YYYY-MM, distills each session into
a one-line digest (date, slug, summary first sentence, topics referenced), writes
a single Sessions/YYYY-MM-rollup.md, then moves the raw files to
Archive/Sessions/YYYY-MM/.

Default month = previous calendar month. Override with --month YYYY-MM. Dry-run
with --dry-run.

Run from a cron task:
    python /Users/celainc/Developers/ClaudeAgentSDK/scripts/session_rollup.py
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

VAULT = Path(os.environ.get("VAULT_PATH", "/Users/celainc/Documents/Vayu/Vayu"))
SESSIONS = VAULT / "Sessions"
ARCHIVE_ROOT = VAULT / "Archive" / "Sessions"


def prev_month(today: date) -> str:
    y, m = today.year, today.month - 1
    if m == 0:
        m = 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def parse_session(path: Path) -> dict:
    """Pull frontmatter project + first sentence of Summary + Topics Referenced."""
    text = path.read_text(encoding="utf-8", errors="replace")
    project = ""
    fm_match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if line.startswith("project:"):
                project = line.split(":", 1)[1].strip()
                break

    summary = ""
    sm = re.search(r"## Summary\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    if sm:
        block = sm.group(1).strip()
        # First sentence-ish: up to first . ! ? or newline if short
        first = re.split(r"(?<=[.!?])\s+|\n\n", block, maxsplit=1)[0].strip()
        summary = first[:300]

    topics = []
    tm = re.search(r"## Topics Referenced\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    if tm:
        for m in re.finditer(r"\[\[([^\]]+)\]\]", tm.group(1)):
            topics.append(m.group(1).strip())

    return {
        "path": path,
        "stem": path.stem,
        "project": project,
        "summary": summary,
        "topics": topics,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", help="YYYY-MM (default: previous calendar month)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-raw", action="store_true",
                        help="Skip moving originals to Archive/")
    args = parser.parse_args()

    month = args.month or prev_month(date.today())
    if not re.match(r"^\d{4}-\d{2}$", month):
        print(f"bad --month {month}", file=sys.stderr)
        sys.exit(2)

    # Idempotent: if a rollup already exists for this month, exit quiet.
    # Lets us schedule this daily and have it only fire on the first run
    # after month-end (when raw files still exist).
    rollup_check = SESSIONS / f"{month}-rollup.md"
    if rollup_check.exists() and not args.dry_run:
        print(f"rollup for {month} already exists; skipping")
        return

    pattern = f"{month}-*.md"
    files = sorted(SESSIONS.glob(pattern))
    # Skip the rollup file itself if rerun
    files = [f for f in files if not f.name.endswith(f"{month}-rollup.md")]

    if not files:
        print(f"no sessions for {month}")
        return

    print(f"{month}: {len(files)} sessions")

    parsed = [parse_session(f) for f in files]

    # Group by project
    by_project: dict[str, list[dict]] = {}
    for p in parsed:
        by_project.setdefault(p["project"] or "unfiled", []).append(p)

    # Topic frequency
    topic_count: dict[str, int] = {}
    for p in parsed:
        for t in p["topics"]:
            topic_count[t] = topic_count.get(t, 0) + 1

    rollup_path = SESSIONS / f"{month}-rollup.md"
    lines = [
        "---",
        f"date: {month}",
        "tags: [session-rollup]",
        f"sessions: {len(files)}",
        "---",
        "",
        f"# {month} — Session Rollup",
        "",
        f"{len(files)} sessions across {len(by_project)} projects. "
        f"Raw files archived to `Archive/Sessions/{month}/`.",
        "",
    ]

    # Top topics
    top_topics = sorted(topic_count.items(), key=lambda x: -x[1])[:15]
    if top_topics:
        lines.append("## Most-referenced topics")
        for t, c in top_topics:
            lines.append(f"- [[{t}]] — {c}")
        lines.append("")

    for project in sorted(by_project):
        lines.append(f"## {project}")
        for p in by_project[project]:
            slug = p["stem"]
            s = p["summary"] or "_(no summary)_"
            lines.append(f"- **[[{slug}]]** — {s}")
        lines.append("")

    if args.dry_run:
        print("--- DRY RUN ---")
        print("\n".join(lines[:50]))
        print(f"... and {len(files)} session files would move to Archive/Sessions/{month}/")
        return

    rollup_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {rollup_path}")

    if args.keep_raw:
        return

    dest = ARCHIVE_ROOT / month
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in files:
        target = dest / f.name
        if target.exists():
            target.unlink()
        shutil.move(str(f), str(target))
        moved += 1
    print(f"moved {moved} files to {dest}")


if __name__ == "__main__":
    main()
