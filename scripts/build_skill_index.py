"""Generate shared/skills/INDEX.md from skill frontmatter.

Scans every shared skill (SKILL.md bundle or flat .md file) and every
per-agent local skill, pulls `name` and `description` from frontmatter,
and writes a discoverable catalog at shared/skills/INDEX.md.

Run anytime new skills land. Also wired into the weekly maintenance cron
(see _template/tasks/weekly_skill_index.md) so the catalog stays fresh
without manual triggering.

Output shape:
  # Skill Catalog (auto-generated YYYY-MM-DD)
  ## Shared skills
  - **<name>** (`skill:<name>`) — <description>
  ## Per-agent skills
  ### <agent>
  - **<name>** (`local:<name>`) — <description>
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "shared" / "skills"
AGENTS = ROOT / "agents"
OUT = SHARED / "INDEX.md"

FM_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Pull YAML frontmatter. Handles literal blocks (`description: |`) and
    quoted scalars. Returns {} on parse failure."""
    m = FM_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}
    out: dict[str, str] = {}
    for key in ("name", "description"):
        val = data.get(key)
        if val is None:
            continue
        # Normalise multi-line literal block into single line for one-row display.
        out[key] = " ".join(str(val).split())
    return out


def _skill_meta(path: Path, fallback_name: str) -> dict[str, str]:
    """Extract name + description; fall back to filename if missing."""
    try:
        text = path.read_text()
    except Exception:
        return {"name": fallback_name, "description": "(could not read)"}
    fm = _parse_frontmatter(text)
    return {
        "name": fm.get("name") or fallback_name,
        "description": fm.get("description") or "(no description)",
    }


def _collect_shared() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not SHARED.exists():
        return items
    for entry in sorted(SHARED.iterdir()):
        if entry.name == "INDEX.md":
            continue
        if entry.is_dir():
            skill_file = entry / "SKILL.md"
            if skill_file.exists():
                meta = _skill_meta(skill_file, entry.name)
                meta["ref"] = f"skill:{entry.name}"
                items.append(meta)
        elif entry.suffix == ".md":
            meta = _skill_meta(entry, entry.stem)
            meta["ref"] = f"skill:{entry.stem}"
            items.append(meta)
    return items


def _collect_per_agent() -> dict[str, list[dict[str, str]]]:
    by_agent: dict[str, list[dict[str, str]]] = {}
    for agent_dir in sorted(AGENTS.iterdir()):
        if agent_dir.name.startswith((".", "_")):
            continue
        skills_dir = agent_dir / "skills"
        if not skills_dir.exists():
            continue
        items: list[dict[str, str]] = []
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir():
                skill_file = entry / "SKILL.md"
                if skill_file.exists():
                    meta = _skill_meta(skill_file, entry.name)
                    meta["ref"] = f"local:{entry.name}"
                    items.append(meta)
            elif entry.suffix == ".md":
                meta = _skill_meta(entry, entry.stem)
                meta["ref"] = f"local:{entry.stem}"
                items.append(meta)
        if items:
            by_agent[agent_dir.name] = items
    return by_agent


def _render(shared: list[dict[str, str]], per_agent: dict[str, list[dict[str, str]]]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append(f"# Skill Catalog (auto-generated {ts})")
    lines.append("")
    lines.append(
        "Discoverable index of every skill the AgentOS knows about. "
        "Regenerate with `python scripts/build_skill_index.py`."
    )
    lines.append("")
    lines.append(
        "**How to use a skill in `agent.yaml`:** add the ref under `skills:`. "
        "`skill:<name>` resolves to shared/skills/<name>/SKILL.md. "
        "`local:<name>` resolves to agents/<agent>/skills/<name>/SKILL.md."
    )
    lines.append("")
    lines.append(f"## Shared skills ({len(shared)})")
    lines.append("")
    for item in shared:
        lines.append(f"- **{item['name']}** (`{item['ref']}`): {item['description']}")
    lines.append("")
    total_local = sum(len(v) for v in per_agent.values())
    lines.append(f"## Per-agent skills ({total_local} across {len(per_agent)} agents)")
    lines.append("")
    for agent in sorted(per_agent.keys()):
        items = per_agent[agent]
        lines.append(f"### `{agent}` ({len(items)})")
        lines.append("")
        for item in items:
            lines.append(f"- **{item['name']}** (`{item['ref']}`): {item['description']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    shared = _collect_shared()
    per_agent = _collect_per_agent()
    OUT.write_text(_render(shared, per_agent))
    total = len(shared) + sum(len(v) for v in per_agent.values())
    print(
        f"Wrote {OUT.relative_to(ROOT)} — {len(shared)} shared + "
        f"{sum(len(v) for v in per_agent.values())} per-agent = {total} skills "
        f"across {len(per_agent)} agents."
    )


if __name__ == "__main__":
    main()
