"""Audit agent-writable files for prompt-injection + exfiltration patterns.

Pattern set borrowed and adapted from Nous Research's hermes-agent
`skills_guard.py`. Agents can write to their own `skills/`, `MEMORY.md`,
`LEARNINGS.md`, `memory/`, and the full layered file set (SOUL, IDENTITY,
AGENTS, TOOLS, INTEGRATIONS, SCHEDULING). A malicious or careless write
could:

- Inject "ignore previous instructions"-style prompts into the next session
- Leak secrets via curl|sh or a base64 payload
- Fake `<tool_use>` markup that could confuse Claude's tool-call parsing
- Reassign the agent's role ("you are now a different agent")

This auditor is a **report-only** tool. It prints findings grouped by
agent + file + category. It does NOT quarantine or delete anything —
operator reads the report and decides.

Usage:
    python scripts/audit_skills.py                  # audit all agents
    python scripts/audit_skills.py main marketing   # specific ones
    python scripts/audit_skills.py --strict         # also flag medium-risk
    python scripts/audit_skills.py --json           # machine-readable output

Run as a cron `kind: systemEvent` task for weekly automated audits.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"

# Files under each agent dir that agents commonly write to. If an agent
# writes outside this list, that's either the vault (not our scope) or
# an intentional new file type the operator should notice on its own.
AGENT_SCAN_GLOBS = [
    "skills/**/*.md",
    "MEMORY.md",
    "LEARNINGS.md",
    "memory/*.md",
    # Layered identity files are in AGENT_SCAN_GLOBS so that if someone
    # (human or agent) quietly injects instructions into IDENTITY.md,
    # we'd catch it.
    "SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md",
    "TOOLS.md", "INTEGRATIONS.md", "SCHEDULING.md",
    "DREAMS.md", "HEARTBEAT.md",
]

# Shared files — operator-owned but worth checking for drift.
SHARED_DIR = ROOT / "shared"


@dataclass
class Finding:
    agent: str
    path: str
    severity: str  # "high" | "medium"
    category: str
    pattern: str
    line_no: int
    line_snippet: str


# ---- Patterns ------------------------------------------------------------

# HIGH severity — almost always malicious or a bug. Flag loudly.
HIGH_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("prompt-injection: ignore previous",
     re.compile(r"\bignore\s+(all\s+|any\s+|)(prior\s+|previous\s+|above\s+|)(instructions|rules|(system\s+)?prompt)",
                re.IGNORECASE)),
    ("prompt-injection: disregard",
     re.compile(r"\bdisregard\s+(all|any|previous|the)\s+(instructions|above|prior|prompts?)",
                re.IGNORECASE)),
    ("prompt-injection: new instructions",
     re.compile(r"\byour\s+new\s+(instructions|role|task|system\s+prompt)\s+(is|are|:)",
                re.IGNORECASE)),
    ("role-reassignment",
     re.compile(r"\byou\s+are\s+(now|no\s+longer)\s+(a\s+|an\s+)?(different|helpful|harmful|admin|root|super)",
                re.IGNORECASE)),
    ("fake tool-call markup",
     re.compile(r"<tool_(use|result|invoke)\b"),
     ),
    ("pipe-to-shell exfil",
     re.compile(r"(curl|wget)\s+[^|;]*\|\s*(sh|bash|zsh|python)", re.IGNORECASE)),
    ("literal API key: Anthropic",
     re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{30,}")),
    ("literal API key: OpenAI",
     re.compile(r"\bsk-[A-Za-z0-9]{30,}")),
    ("literal API key: GitHub PAT",
     re.compile(r"\bgh[ps]_[A-Za-z0-9]{30,}")),
    ("literal API key: Slack",
     re.compile(r"\bxox[bparsoe]-[0-9]{10,}-[0-9]{10,}-[A-Za-z0-9]{20,}")),
    ("literal API key: AWS Access Key",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("literal bearer token",
     re.compile(r"\bbearer\s+[A-Za-z0-9_\-\.=]{40,}", re.IGNORECASE)),
    ("base64 blob (>500 chars on one line)",
     re.compile(r"(^|\s)[A-Za-z0-9+/=]{500,}($|\s)")),
    ("suspicious .onion URL",
     re.compile(r"https?://[a-z2-7]{16,56}\.onion\b")),
]

# MEDIUM severity — sometimes legitimate (e.g., examples in docs). Flag
# only when --strict.
MEDIUM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override instructions pattern",
     re.compile(r"\b(override|bypass|hijack)\s+(the\s+)?(system|agent|user)\s+(prompt|instruction|role)",
                re.IGNORECASE)),
    ("credentials-as-text",
     re.compile(r"\bpassword\s*[:=]\s*[A-Za-z0-9!@#$%^&*_\-]{8,}")),
]


def _scan_file(path: Path, agent_name: str, strict: bool) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return findings

    # Skip the humanizer-full.md reference (it cites injection examples
    # as part of its own guide — false positives).
    if path.name == "humanizer-full.md":
        return findings

    rel = str(path.relative_to(ROOT))
    for line_no, line in enumerate(text.splitlines(), start=1):
        for label, pat in HIGH_PATTERNS:
            if pat.search(line):
                findings.append(Finding(
                    agent=agent_name, path=rel, severity="high",
                    category=label, pattern=pat.pattern,
                    line_no=line_no, line_snippet=line.strip()[:140],
                ))
        if strict:
            for label, pat in MEDIUM_PATTERNS:
                if pat.search(line):
                    findings.append(Finding(
                        agent=agent_name, path=rel, severity="medium",
                        category=label, pattern=pat.pattern,
                        line_no=line_no, line_snippet=line.strip()[:140],
                    ))
    return findings


def _scan_agent(agent_dir: Path, strict: bool) -> list[Finding]:
    findings: list[Finding] = []
    name = agent_dir.name
    for glob in AGENT_SCAN_GLOBS:
        for path in agent_dir.glob(glob):
            if path.is_file():
                findings.extend(_scan_file(path, name, strict))
    return findings


def _scan_shared(strict: bool) -> list[Finding]:
    findings: list[Finding] = []
    for path in SHARED_DIR.glob("*.md"):
        findings.extend(_scan_file(path, "<shared>", strict))
    return findings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("agents", nargs="*", help="Agent names; empty = all")
    p.add_argument("--strict", action="store_true",
                   help="Also flag medium-risk patterns (more false positives)")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.add_argument("--shared", action="store_true",
                   help="Also scan shared/*.md")
    args = p.parse_args()

    if args.agents:
        targets = [AGENTS / n for n in args.agents if (AGENTS / n).is_dir()]
    else:
        targets = [
            d for d in AGENTS.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_"))
            and (d / "agent.yaml").exists()
        ]

    all_findings: list[Finding] = []
    for d in sorted(targets, key=lambda x: x.name):
        all_findings.extend(_scan_agent(d, args.strict))
    if args.shared:
        all_findings.extend(_scan_shared(args.strict))

    if args.json:
        print(json.dumps([f.__dict__ for f in all_findings], indent=2))
        return 0 if not any(f.severity == "high" for f in all_findings) else 1

    high = [f for f in all_findings if f.severity == "high"]
    medium = [f for f in all_findings if f.severity == "medium"]

    if not all_findings:
        print("✅ clean — no flagged patterns in any audited file")
        return 0

    print(f"== Skill audit report ==")
    print(f"  agents scanned: {len(targets)}")
    print(f"  findings: {len(high)} high, {len(medium)} medium")
    print()

    if high:
        print("🔥 HIGH SEVERITY")
        for f in high:
            print(f"  [{f.agent}] {f.path}:{f.line_no}")
            print(f"      category: {f.category}")
            print(f"      matched : /{f.pattern[:80]}/")
            print(f"      snippet : {f.line_snippet!r}")
            print()

    if medium:
        print("⚠️  MEDIUM SEVERITY")
        for f in medium:
            print(f"  [{f.agent}] {f.path}:{f.line_no}  — {f.category}")
            print(f"      snippet : {f.line_snippet!r}")
            print()

    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
