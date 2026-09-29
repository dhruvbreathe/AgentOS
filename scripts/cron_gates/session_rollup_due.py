"""Gate for main/monthly_session_rollup: is the previous month's rollup due?

Same test scripts/session_rollup.py uses (read-only here): the rollup is due
when Sessions/<prev-month>-rollup.md does not exist AND raw
Sessions/<prev-month>-*.md files are still there. Not due -> print nothing.
Due -> print the facts and wake the agent, which runs the script.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from gatelib import fail, load_env


def prev_month(today: date) -> str:
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{y:04d}-{m:02d}"


def main() -> None:
    load_env()
    vault = Path(os.environ.get("VAULT_PATH", "/Users/celainc/Documents/Vayu/Vayu"))
    sessions = vault / "Sessions"
    if not sessions.is_dir():
        fail(f"vault Sessions/ not found at {sessions}")
    month = prev_month(date.today())
    rollup = sessions / f"{month}-rollup.md"
    if rollup.exists():
        return
    raw = [p for p in sessions.glob(f"{month}-*.md") if p.name != rollup.name]
    if not raw:
        return
    print(f"Rollup due: {rollup} does not exist and {len(raw)} raw session files "
          f"for {month} are still in {sessions}. Run the script as the task says.")


if __name__ == "__main__":
    main()
