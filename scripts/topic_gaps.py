#!/usr/bin/env python3
"""Topic-gap report.

Scans Sessions/ for [[WikiLinks]] referenced N+ times that DON'T have a
corresponding Topics/*.md note. Writes the report to
Company/Topic-Gaps.md (overwriting).

Usage:
    python scripts/topic_gaps.py [--min 3] [--lookback-days 90]
"""
from __future__ import annotations

import argparse
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("VAULT_PATH", "/Users/celainc/Documents/Vayu/Vayu"))
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]+)?\]\]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=3,
                    help="Minimum references to flag as a gap")
    ap.add_argument("--lookback-days", type=int, default=90)
    args = ap.parse_args()

    cutoff = datetime.now() - timedelta(days=args.lookback_days)

    # Existing Topics/ note slugs (case-insensitive)
    topics_dir = VAULT / "Topics"
    existing = {p.stem.lower() for p in topics_dir.glob("*.md")} if topics_dir.exists() else set()

    counts: Counter[str] = Counter()
    sources: dict[str, list[str]] = {}

    for p in (VAULT / "Sessions").glob("*.md"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in WIKILINK_RE.finditer(text):
            tgt = m.group(1).strip()
            counts[tgt] += 1
            sources.setdefault(tgt, []).append(p.stem)

    # Gaps = referenced >= min, no Topics/ note
    gaps = [
        (name, count) for name, count in counts.most_common()
        if count >= args.min and name.lower() not in existing
    ]

    out_lines = [
        "---",
        "type: dashboard",
        "tags: [dashboard, topic-gaps]",
        f"generated: {datetime.now().strftime('%Y-%m-%dT%H:%M')}",
        "---",
        "",
        "# Topic Gaps",
        "",
        f"Wiki-links referenced ≥{args.min} times in Sessions/ from the last "
        f"{args.lookback_days} days that don't have a `Topics/` note yet.",
        "",
        f"_{len(gaps)} candidates. Regenerate: `python scripts/topic_gaps.py`._",
        "",
        "| References | Topic | Recent sessions |",
        "|---|---|---|",
    ]
    for name, count in gaps[:80]:
        sample = sorted(set(sources[name]))[:3]
        sample_str = ", ".join(f"[[{s}]]" for s in sample)
        out_lines.append(f"| {count} | [[{name}]] | {sample_str} |")

    target = VAULT / "Company" / "Topic-Gaps.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"wrote {target} ({len(gaps)} gaps)")


if __name__ == "__main__":
    main()
