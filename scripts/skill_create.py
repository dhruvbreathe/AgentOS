#!/usr/bin/env python3
"""skill_create.py — scaffold a new skill bundle.

Creates the standard skill layout:

    shared/skills/<name>/SKILL.md        (if --shared)
    agents/<agent>/skills/<name>/SKILL.md (if --agent <agent>)

SKILL.md is pre-populated with valid frontmatter + the canonical section
headers (When to reach for this / How it works / What NOT to do).

Usage:
    python scripts/skill_create.py my-skill --shared
    python scripts/skill_create.py my-skill --agent main
    python scripts/skill_create.py my-skill --agent main --description "..."
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEMPLATE = """---
name: {name}
description: {description}
---

# {title}

## When to reach for this

- <concrete trigger 1>
- <concrete trigger 2>

## How it works

<short, opinionated body. Code-ready examples beat prose. Show don't explain.>

```bash
# example invocation
```

## What NOT to do

- <failure mode 1>
- <failure mode 2>

## Cost sense

<optional: when this skill earns its token cost vs when to skip>
"""


def slug_to_title(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a new skill bundle")
    ap.add_argument("name", help="skill name (kebab-case)")
    ap.add_argument("--shared", action="store_true", help="create under shared/skills/")
    ap.add_argument("--agent", help="create under agents/<agent>/skills/")
    ap.add_argument(
        "--description",
        default="<one-sentence trigger description — what + when>",
        help="skill description for frontmatter",
    )
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z][a-z0-9-]+", args.name):
        print(f"error: name must be kebab-case (got: {args.name!r})", file=sys.stderr)
        return 2
    if bool(args.shared) == bool(args.agent):
        print("error: pick exactly one of --shared or --agent <name>", file=sys.stderr)
        return 2

    if args.shared:
        base = ROOT / "shared" / "skills" / args.name
    else:
        agent_dir = ROOT / "agents" / args.agent
        if not agent_dir.exists():
            print(f"error: no agent at {agent_dir}", file=sys.stderr)
            return 2
        base = agent_dir / "skills" / args.name

    skill_md = base / "SKILL.md"
    if skill_md.exists():
        print(f"error: already exists: {skill_md}", file=sys.stderr)
        return 1

    base.mkdir(parents=True, exist_ok=True)
    (base / "scripts").mkdir(exist_ok=True)
    (base / "references").mkdir(exist_ok=True)

    skill_md.write_text(
        TEMPLATE.format(
            name=args.name,
            description=args.description,
            title=slug_to_title(args.name),
        )
    )

    ref = f"skill:{args.name}" if args.shared else f"local:{args.name}"
    print(f"✅ created {skill_md}")
    print(f"   reference in agent.yaml as:  {ref}")
    print()
    print("Next steps:")
    print(f"  1. Fill in SKILL.md body")
    print(f"  2. Add to target agent.yaml under `skills:`")
    print(f"  3. Run: python scripts/audit_skills.py")
    print(f"  4. touch logs/.restart-requested  (pick up the change)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
