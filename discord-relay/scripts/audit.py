#!/usr/bin/env python3
"""audit.py — run the full security audit suite.

Wraps:
  - audit_skills.py    (prompt-injection + exfiltration patterns)
  - audit_secrets.py   (leaked API keys in tree + history)

Exit 0 if all clean, 1 if either found issues.

Usage:
    python scripts/audit.py                  # tree only
    python scripts/audit.py --history        # include git history
    python scripts/audit.py --json
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def run(cmd: list[str]) -> int:
    print(f"\n{'═' * 60}")
    print(f"$ {' '.join(cmd)}")
    print("═" * 60)
    r = subprocess.run(cmd)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Full audit suite")
    ap.add_argument("--history", action="store_true", help="include git history in secrets scan")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    py = sys.executable
    worst = 0

    skills_args = [py, str(SCRIPTS / "audit_skills.py")]
    if args.json:
        skills_args.append("--json")
    worst = max(worst, run(skills_args))

    secrets_args = [py, str(SCRIPTS / "audit_secrets.py")]
    if args.history:
        secrets_args.append("--history")
    if args.json:
        secrets_args.append("--json")
    worst = max(worst, run(secrets_args))

    print(f"\n{'═' * 60}")
    print("✅ all audits clean" if worst == 0 else f"⚠️  issues found (rc={worst})")
    return worst


if __name__ == "__main__":
    sys.exit(main())
