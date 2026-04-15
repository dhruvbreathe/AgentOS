"""Scaffold a new agent by copying agents/_template/ into agents/<name>/
and doing a few targeted substitutions.

Usage:
    python scripts/new_agent.py <name> --channel-id <id> [--webhook-env <VAR>]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
TEMPLATE = AGENTS / "_template"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("name", help="Agent name (lowercase, dash-separated)")
    p.add_argument("--channel-id", required=True, help="Discord channel ID")
    p.add_argument(
        "--webhook-env",
        default=None,
        help="Env var holding this agent's outbound webhook URL "
        "(default: <NAME_UPPER>_WEBHOOK_URL)",
    )
    args = p.parse_args()

    name = args.name.strip().lower()
    dest = AGENTS / name
    if dest.exists():
        print(f"refuse to overwrite existing {dest}", file=sys.stderr)
        return 1
    if not TEMPLATE.exists():
        print(f"template not found at {TEMPLATE}", file=sys.stderr)
        return 1

    shutil.copytree(TEMPLATE, dest)

    webhook_env = args.webhook_env or f"{name.upper().replace('-', '_')}_WEBHOOK_URL"

    # Patch agent.yaml
    yaml_path = dest / "agent.yaml"
    yaml = yaml_path.read_text()
    yaml = yaml.replace("name: REPLACE_ME", f"name: {name}")
    yaml = yaml.replace(
        'channel_id: "REPLACE_WITH_CHANNEL_ID"',
        f'channel_id: "{args.channel_id}"',
    )
    yaml = yaml.replace(
        "webhook_url_env: REPLACE_WEBHOOK_URL_ENV",
        f"webhook_url_env: {webhook_env}",
    )
    yaml_path.write_text(yaml)

    print(f"created {dest}")
    print(f"next:")
    print(f"  1. add {webhook_env}=<discord webhook URL> to .env")
    print(f"  2. fill in {dest}/IDENTITY.md (name, creature, vibe, role)")
    print(f"  3. fill in {dest}/TOOLS.md and {dest}/INTEGRATIONS.md")
    print(f"  4. restart the bot: python bot.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
