"""check_facts_hygiene.py — facts-vs-guesses hygiene checker for FACTS.md.

Fix #3 from the OS-vs-X improvement map (memory hygiene v2, 2026-06-17).

The shared FACTS.md is where every agent looks up "what is X right now?".
The failure mode it guards against: a guess gets written as if it were a hard
fact, then propagates into investor copy (how "4.8 stars" and "40% anxiety
reduction" leaked). v2 adds a confidence tier to each fact's provenance
comment so the trust level travels with the number.

This checker reads FACTS.md and flags fact lines that:
  - carry a provenance comment but no `conf:<tier>` token, or
  - use an unknown tier, or
  - assert a value but carry no provenance comment at all.

Explicit non-facts (`... : TBD`) are skipped. It is report-only by default;
`--strict` exits non-zero if anything is flagged (for a cron guard).

Usage:
    python scripts/check_facts_hygiene.py
    python scripts/check_facts_hygiene.py --strict
    python scripts/check_facts_hygiene.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FACTS = Path("/Users/celainc/Documents/Vayu/Vayu/Company/FACTS.md")

TIERS = {"confirmed", "estimate", "unverified", "stale"}
_CONF_RE = re.compile(r"conf:([a-z]+)")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
# A fact bullet asserts a value: "- key: value" or a bolded "- **key:** value".
_VALUE_RE = re.compile(r"^\s*-\s+.+:\s*\S")


def _is_skippable(line: str) -> bool:
    stripped = line.strip()
    # explicit non-facts and retired/struck lines need no confidence tag
    if "TBD" in stripped:
        return True
    if stripped.startswith("- ~~"):  # struck-through / retired fact
        return True
    return False


def scan(text: str) -> list[dict]:
    findings: list[dict] = []
    in_body = False  # skip the preamble/rules; facts start at the first "## "
    for i, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            in_body = True
            continue
        if not in_body:
            continue
        if not _VALUE_RE.match(line):
            continue
        if _is_skippable(line):
            continue
        label = line.strip()[:70]
        m = _COMMENT_RE.search(line)
        if not m:
            findings.append({"line": i, "issue": "no-provenance",
                             "detail": "value line has no <!-- ... --> comment",
                             "text": label})
            continue
        comment = m.group(1)
        cm = _CONF_RE.search(comment)
        if not cm:
            findings.append({"line": i, "issue": "no-conf-tier",
                             "detail": "comment present but missing conf:<tier>",
                             "text": label})
        elif cm.group(1) not in TIERS:
            findings.append({"line": i, "issue": "bad-conf-tier",
                             "detail": f"unknown tier conf:{cm.group(1)} "
                                       f"(expected one of {sorted(TIERS)})",
                             "text": label})
    return findings


def _tier_counts(text: str) -> dict[str, int]:
    counts = {t: 0 for t in TIERS}
    for cm in _CONF_RE.finditer(text):
        if cm.group(1) in counts:
            counts[cm.group(1)] += 1
    return counts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--strict", action="store_true",
                   help="exit 1 if any line is flagged")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if not FACTS.exists():
        print(f"FACTS.md not found at {FACTS}", file=sys.stderr)
        return 2

    text = FACTS.read_text(encoding="utf-8")
    findings = scan(text)
    counts = _tier_counts(text)

    if args.json:
        print(json.dumps({"findings": findings, "tier_counts": counts}, indent=2))
        return 1 if (args.strict and findings) else 0

    tiers_str = ", ".join(f"{t} ×{counts[t]}" for t in sorted(counts) if counts[t])
    print(f"Confidence tiers tagged: {tiers_str or 'none'}")
    if not findings:
        print("✓ every value line carries a conf:<tier> + provenance")
        return 0
    print(f"\n{len(findings)} line(s) need attention:")
    for f in findings:
        print(f"  L{f['line']:>3} [{f['issue']}] {f['text']}")
        print(f"        {f['detail']}")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
