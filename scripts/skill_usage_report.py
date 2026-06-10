#!/usr/bin/env python3
"""Skill usage report - find prompt dead weight.

Every skill listed in an agent.yaml is injected into that agent's system
prompt on every single turn. A skill that never gets used is a pure token
tax (slower turns, bigger cache writes). Pattern borrowed from Hermes
v0.16's Curator (per-skill usage tracking + pruning of unused built-ins).

Heuristic: a skill "shows evidence of use" if its name appears anywhere in
the agent's recent trajectory JSONLs (tool inputs, bash commands, text).
That over-counts (mentioning != using) - which is the safe direction for a
prune REPORT. Anything with zero mentions across N days of trajectories is
a strong prune candidate.

Usage:
    ./.venv/bin/python scripts/skill_usage_report.py [--days 30] [--agent NAME]

Report-only. Never edits agent.yaml.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
SHARED_SKILLS = ROOT / "shared" / "skills"
TRAJ_ROOT = ROOT / "logs" / "trajectories"


def resolve_skill(ref: str, agent_dir: Path) -> tuple[str, Path | None]:
    """Return (display_name, path) for a skills: entry."""
    ref = str(ref).strip()
    if ref.startswith("skill:"):
        name = ref.split(":", 1)[1]
        for cand in (SHARED_SKILLS / name / "SKILL.md", SHARED_SKILLS / f"{name}.md"):
            if cand.exists():
                return name, cand
        return name, None
    if ref.startswith("local:"):
        name = ref.split(":", 1)[1]
        cand = agent_dir / "skills" / name / "SKILL.md"
        return name, cand if cand.exists() else None
    # legacy path form
    p = (agent_dir / ref).resolve()
    name = p.parent.name if p.name == "SKILL.md" else p.stem
    return name, p if p.exists() else None


def trajectory_blob(agent: str, days: int) -> str:
    """Concatenate recent trajectory files for one agent (lowercased)."""
    tdir = TRAJ_ROOT / agent
    if not tdir.is_dir():
        return ""
    cutoff = time.time() - days * 86400
    parts: list[str] = []
    for f in tdir.glob("*.jsonl"):
        try:
            if f.stat().st_mtime >= cutoff:
                parts.append(f.read_text(errors="replace").lower())
        except OSError:
            continue
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--agent", default=None, help="limit to one agent")
    args = ap.parse_args()

    total_dead_kb = 0.0
    rows: list[tuple[str, str, float, int]] = []  # agent, skill, kb, mentions

    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        if agent_dir.name.startswith(("_", ".")) or not (agent_dir / "agent.yaml").exists():
            continue
        if args.agent and agent_dir.name != args.agent:
            continue
        try:
            cfg = yaml.safe_load((agent_dir / "agent.yaml").read_text()) or {}
        except yaml.YAMLError as e:
            print(f"! {agent_dir.name}: agent.yaml parse error: {e}")
            continue
        skills = cfg.get("skills") or []
        if not skills:
            continue
        blob = trajectory_blob(agent_dir.name, args.days)
        if not blob:
            print(f"~ {agent_dir.name}: no trajectories in last {args.days}d, skipping")
            continue
        for ref in skills:
            name, path = resolve_skill(ref, agent_dir)
            kb = round(path.stat().st_size / 1024, 1) if path else 0.0
            # name match, tolerant of - vs _ variants
            needles = {name.lower(), name.lower().replace("-", "_"), name.lower().replace("_", "-")}
            mentions = sum(blob.count(n) for n in needles)
            rows.append((agent_dir.name, name, kb, mentions))
            if mentions == 0:
                total_dead_kb += kb

    dead = [r for r in rows if r[3] == 0]
    if not rows:
        print("No skills found.")
        return

    print(f"# Skill usage report: last {args.days} days")
    print(f"# {len(rows)} skill loads across agents, {len(dead)} with ZERO trajectory mentions")
    print(f"# Estimated dead prompt weight: {total_dead_kb:.0f} KB injected EVERY turn\n")
    if dead:
        print("## Prune candidates (zero mentions, sorted by prompt weight)")
        for agent, name, kb, _ in sorted(dead, key=lambda r: -r[2]):
            print(f"  - {agent:28s} {name:30s} {kb:6.1f} KB")
    print("\n## Full table (mentions are a loose proxy, review before pruning)")
    for agent, name, kb, mentions in sorted(rows, key=lambda r: (r[0], r[3])):
        flag = "  <- prune?" if mentions == 0 else ""
        print(f"  {agent:28s} {name:30s} {kb:6.1f} KB  {mentions:5d} mentions{flag}")


if __name__ == "__main__":
    main()
