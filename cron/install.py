"""Scans agents/*/tasks/*.md for a `cron:` frontmatter line and generates
crontab entries pointing at cron_trigger.py.

Usage:
    python cron/install.py            # print entries only
    python cron/install.py --apply    # install via `crontab -`

Task-file frontmatter example:

    ---
    cron: 0 8 * * *
    ---
    Produce the 8am daily digest...
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
TRIGGER = ROOT / "cron_trigger.py"
LOG_DIR = ROOT / "logs"

# Prefer the venv Python (has aiohttp, discord.py, claude-agent-sdk, etc.).
# Fall back to sys.executable only if the venv isn't present — which is the
# reason crons were silently crashing with ModuleNotFoundError before.
_VENV_PY = ROOT / ".venv" / "bin" / "python"
PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_frontmatter(path: Path) -> dict:
    text = path.read_text()
    m = FM_RE.match(text)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def build_entries() -> list[str]:
    entries: list[str] = []
    if not AGENTS_DIR.exists():
        return entries
    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        tasks = agent_dir / "tasks"
        if not tasks.is_dir():
            continue
        for task in sorted(tasks.glob("*.md")):
            fm = _read_frontmatter(task)
            schedule = fm.get("cron")
            if not schedule:
                continue
            logf = LOG_DIR / f"{agent_dir.name}-{task.stem}.log"
            entries.append(
                f"{schedule} cd {ROOT} && {PYTHON} {TRIGGER} {agent_dir.name} "
                f"{task.stem} >> {logf} 2>&1"
            )
    return entries


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Install into user crontab")
    args = p.parse_args()

    entries = build_entries()
    if not entries:
        print("# No tasks with `cron:` frontmatter found.")
        return

    marker = "# --- agentos (managed) ---"
    end_marker = "# --- /agentos ---"
    block = "\n".join([marker, *entries, end_marker])

    if not args.apply:
        print(block)
        return

    # Merge with existing crontab, replacing any previous managed block.
    try:
        current = subprocess.check_output(["crontab", "-l"], text=True)
    except subprocess.CalledProcessError:
        current = ""

    stripped = re.sub(
        rf"{re.escape(marker)}.*?{re.escape(end_marker)}\n?",
        "",
        current,
        flags=re.DOTALL,
    ).rstrip()
    new_crontab = (stripped + "\n\n" + block + "\n").lstrip()
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    print(f"Installed {len(entries)} cron entries.")


if __name__ == "__main__":
    main()
