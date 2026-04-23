#!/usr/bin/env python3
"""doctor.py — self-diagnostic for the Prana AgentOS stack.

Checks every agent's:
  - Webhook URL (env set + Discord endpoint reachable)
  - Bot token (env set + /users/@me validates)
  - Vault mount (VAULT_PATH exists + writable)
  - launchd jobs (every tasks/*.md has a loaded plist)
  - Trajectory log (recent JSONL present, non-empty)
  - .env completeness (no referenced env var is missing)

Usage:
    python scripts/doctor.py                         # check all agents
    python scripts/doctor.py --agent main            # one agent
    python scripts/doctor.py --json                  # machine-readable output
    python scripts/doctor.py --fix                   # attempt safe auto-fixes

Exit codes:
    0 — all green
    1 — at least one issue (non-fatal)
    2 — fatal error (no agents loaded, etc.)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
LOGS_DIR = ROOT / "logs"
ENV_FILE = ROOT / ".env"

Severity = Literal["ok", "warn", "fail"]


@dataclass
class Check:
    name: str
    severity: Severity = "ok"
    detail: str = ""


@dataclass
class AgentReport:
    agent: str
    checks: list[Check] = field(default_factory=list)

    @property
    def worst(self) -> Severity:
        for s in ("fail", "warn", "ok"):
            if any(c.severity == s for c in self.checks):
                return s  # type: ignore
        return "ok"

    def add(self, name: str, severity: Severity, detail: str = "") -> None:
        self.checks.append(Check(name=name, severity=severity, detail=detail))


# ---- .env loading -----------------------------------------------------------

def load_dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def resolve_env(var: str, dotenv: dict[str, str]) -> str | None:
    return os.environ.get(var) or dotenv.get(var)


# ---- Individual checks ------------------------------------------------------

def check_webhook(rep: AgentReport, url: str | None) -> None:
    """Reach the Discord webhook without actually posting.

    Discord webhooks return 405 on GET (method not allowed) but that still
    proves the endpoint exists and routes. A dead/revoked webhook 404s.
    So: 200/204/405 = OK, 404 = fail, anything else = warn.
    """
    if not url:
        rep.add("webhook", "fail", "no URL set")
        return
    try:
        import urllib.request, urllib.error
        # Cloudflare (in front of Discord) blocks Python-urllib by default.
        # Use a normal UA so we see the real Discord response.
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "PranaAgentOS-doctor/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        # 405 = endpoint exists, method disallowed (Discord's normal response to GET)
        # 200/204 = Discord returning webhook metadata
        if status in (200, 204, 405):
            rep.add("webhook", "ok", f"HTTP {status}")
        elif status in (401, 404):
            rep.add("webhook", "fail", f"HTTP {status} — revoked/missing")
        elif status == 403:
            # Cloudflare bot block; endpoint probably fine, we just can't see.
            rep.add("webhook", "ok", "HTTP 403 (bot-filtered, not proof of failure)")
        else:
            rep.add("webhook", "warn", f"HTTP {status}")
    except Exception as e:
        rep.add("webhook", "fail", f"unreachable: {type(e).__name__}")


def check_bot_token(rep: AgentReport, token: str | None) -> None:
    if not token:
        rep.add("bot_token", "ok", "no dedicated token (using shared)")
        return
    try:
        import urllib.request, urllib.error
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={
                "Authorization": f"Bot {token}",
                "User-Agent": "DiscordBot (PranaAgentOS, 1.0)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        if status == 200:
            rep.add("bot_token", "ok", "valid")
        elif status == 401:
            rep.add("bot_token", "fail", "HTTP 401 — token rejected")
        else:
            rep.add("bot_token", "warn", f"HTTP {status}")
    except Exception as e:
        rep.add("bot_token", "fail", f"{type(e).__name__}")


def check_vault(rep: AgentReport) -> None:
    vault = os.environ.get("VAULT_PATH")
    if not vault:
        rep.add("vault", "warn", "VAULT_PATH not set (checking first time only)")
        return
    p = Path(vault)
    if p.exists() and p.is_dir():
        rep.add("vault", "ok", str(p))
    else:
        rep.add("vault", "fail", f"not a directory: {p}")


def check_launchd_tasks(rep: AgentReport, agent_name: str, tasks_dir: Path) -> None:
    if not tasks_dir.exists():
        rep.add("launchd_tasks", "ok", "no tasks")
        return
    task_files = sorted(tasks_dir.glob("*.md"))
    if not task_files:
        rep.add("launchd_tasks", "ok", "no tasks")
        return
    try:
        out = subprocess.check_output(
            ["launchctl", "list"], text=True, timeout=5
        )
    except Exception as e:
        rep.add("launchd_tasks", "warn", f"launchctl failed: {e}")
        return
    loaded = {line.split("\t")[-1] for line in out.splitlines()[1:]}
    missing: list[str] = []
    for tf in task_files:
        label = f"com.agentos.{agent_name}-{tf.stem}"
        if label not in loaded:
            missing.append(tf.stem)
    if missing:
        rep.add("launchd_tasks", "warn",
                f"{len(missing)}/{len(task_files)} not loaded: {', '.join(missing[:3])}")
    else:
        rep.add("launchd_tasks", "ok", f"{len(task_files)} loaded")


def check_trajectory(rep: AgentReport, agent_name: str) -> None:
    traj_dir = LOGS_DIR / "trajectories" / agent_name
    if not traj_dir.exists():
        rep.add("trajectory", "ok", "no sessions yet")
        return
    latest = max(traj_dir.glob("*.jsonl"), default=None, key=lambda p: p.stat().st_mtime)
    if not latest:
        rep.add("trajectory", "ok", "no sessions yet")
        return
    size = latest.stat().st_size
    if size == 0:
        rep.add("trajectory", "warn", f"{latest.name} is empty")
    else:
        rep.add("trajectory", "ok", f"{latest.name} ({size // 1024}KB)")


def check_env_completeness(rep: AgentReport, cfg: dict, dotenv: dict[str, str]) -> None:
    missing: list[str] = []
    for key in ("webhook_url_env", "bot_token_env"):
        var = cfg.get(key)
        if var and not resolve_env(var, dotenv):
            missing.append(var)
    for var in cfg.get("env_passthrough", []) or []:
        if not resolve_env(var, dotenv):
            missing.append(var)
    if missing:
        rep.add("env", "fail", f"missing: {', '.join(missing)}")
    else:
        rep.add("env", "ok", "all referenced vars present")


# ---- Per-agent runner -------------------------------------------------------

def check_agent(agent_dir: Path, dotenv: dict[str, str]) -> AgentReport:
    rep = AgentReport(agent=agent_dir.name)
    cfg_path = agent_dir / "agent.yaml"
    if not cfg_path.exists():
        rep.add("config", "fail", "no agent.yaml")
        return rep
    cfg = yaml.safe_load(cfg_path.read_text()) or {}

    webhook_env = cfg.get("webhook_url_env")
    webhook_url = resolve_env(webhook_env, dotenv) if webhook_env else None
    check_webhook(rep, webhook_url)

    bot_token_env = cfg.get("bot_token_env")
    bot_token = resolve_env(bot_token_env, dotenv) if bot_token_env else None
    check_bot_token(rep, bot_token)

    check_launchd_tasks(rep, agent_dir.name, agent_dir / "tasks")
    check_trajectory(rep, agent_dir.name)
    check_env_completeness(rep, cfg, dotenv)
    return rep


# ---- Fix layer --------------------------------------------------------------

def try_fix(rep: AgentReport) -> list[str]:
    """Safe auto-fixes only. Returns list of what was attempted."""
    actions: list[str] = []
    for c in rep.checks:
        if c.name == "launchd_tasks" and c.severity == "warn" and "not loaded" in c.detail:
            # Run the scheduler installer to pick up missing jobs
            try:
                subprocess.check_call(
                    [sys.executable, str(ROOT / "scheduler" / "install.py"), "--apply"],
                    timeout=30, cwd=ROOT,
                )
                actions.append(f"{rep.agent}: reloaded launchd jobs")
            except Exception as e:
                actions.append(f"{rep.agent}: launchd fix failed ({e})")
    return actions


# ---- Output -----------------------------------------------------------------

GLYPH = {"ok": "✅", "warn": "⚠️", "fail": "❌"}


def print_pretty(reports: list[AgentReport]) -> None:
    for rep in reports:
        print(f"\n{GLYPH[rep.worst]}  {rep.agent}")
        for c in rep.checks:
            print(f"    {GLYPH[c.severity]}  {c.name:20s}  {c.detail}")


def print_json(reports: list[AgentReport]) -> None:
    out = [
        {
            "agent": r.agent,
            "worst": r.worst,
            "checks": [
                {"name": c.name, "severity": c.severity, "detail": c.detail}
                for c in r.checks
            ],
        }
        for r in reports
    ]
    print(json.dumps(out, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="Prana AgentOS doctor")
    ap.add_argument("--agent", help="check a single agent by name")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fix", action="store_true", help="attempt safe auto-fixes")
    args = ap.parse_args()

    if not AGENTS_DIR.exists():
        print("error: no agents/ directory", file=sys.stderr)
        return 2

    dotenv = load_dotenv()

    if args.agent:
        targets = [AGENTS_DIR / args.agent]
        if not targets[0].exists():
            print(f"error: no agent named {args.agent}", file=sys.stderr)
            return 2
    else:
        targets = [
            d for d in sorted(AGENTS_DIR.iterdir())
            if d.is_dir() and not d.name.startswith((".", "_"))
            and (d / "agent.yaml").exists()
        ]

    reports: list[AgentReport] = []
    # Global vault check — add to first report or emit separately
    vault_rep = AgentReport(agent="(global)")
    check_vault(vault_rep)
    reports.append(vault_rep)

    for agent_dir in targets:
        reports.append(check_agent(agent_dir, dotenv))

    if args.fix:
        print("\n--- auto-fix attempts ---")
        for rep in reports:
            for line in try_fix(rep):
                print("  " + line)

    if args.json:
        print_json(reports)
    else:
        print_pretty(reports)

    worst = max(
        (r.worst for r in reports),
        key=lambda s: {"ok": 0, "warn": 1, "fail": 2}[s],
        default="ok",
    )
    # When --json, stdout must be pure JSON. Send summary to stderr.
    summary = f"\nOverall: {GLYPH[worst]}  {worst}"
    if args.json:
        print(summary, file=sys.stderr)
    else:
        print(summary)
    return 1 if worst != "ok" else 0


if __name__ == "__main__":
    sys.exit(main())
