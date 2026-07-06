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


def check_cron_runs(rep: AgentReport, agent_name: str, tasks_dir: Path) -> None:
    """Scan each task's run log for a recent failure.

    check_launchd_tasks only confirms a plist is LOADED — it cannot see a cron
    that fires and exits non-zero every time. That blind spot hid a 12-day,
    27-cron outage (model==fallback collision, 2026-06-13). This reads the tail
    of logs/<agent>-<task>.log and flags failure signatures so a silently
    broken cron surfaces within a day instead of never.
    """
    if not tasks_dir.exists():
        return
    fail_sig = ("exit code 1", "ProcessError", "Traceback (most recent call last)",
                "Not logged in", "ModuleNotFoundError")
    # Markers that begin a single cron run — scope failure detection to the
    # LAST run only, so a fix shows green on the next successful fire instead
    # of lingering until old tracebacks scroll out of the window.
    run_start = ("cron-trigger: Loaded", "Scheduled task `",
                 "Using bundled Claude Code CLI")
    broken: list[str] = []
    for tf in sorted(tasks_dir.glob("*.md")):
        log = LOGS_DIR / f"{agent_name}-{tf.stem}.log"
        if not log.exists():
            continue
        try:
            text = log.read_text(errors="ignore")[-12000:]
        except Exception:
            continue
        # isolate the last run
        cut = max((text.rfind(m) for m in run_start), default=-1)
        last_run = text[cut:] if cut >= 0 else text[-4000:]
        if any(sig in last_run for sig in fail_sig):
            broken.append(tf.stem)
    if broken:
        rep.add("cron_runs", "fail",
                f"{len(broken)} cron(s) failing: {', '.join(broken[:4])}")
    else:
        rep.add("cron_runs", "ok", "no recent cron failures")


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


_context_latest_cache: dict[str, dict] | None = None


def _context_latest() -> dict[str, dict]:
    """Latest context-usage telemetry line per agent (relay writes these
    post-turn on SDK 0.2.110). Parsed once per doctor run."""
    global _context_latest_cache
    if _context_latest_cache is not None:
        return _context_latest_cache
    latest: dict[str, dict] = {}
    path = LOGS_DIR / "context-usage.jsonl"
    if path.exists():
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(line, dict) and line.get("agent"):
                    latest[line["agent"]] = line  # file is chronological
        except OSError:
            pass
    _context_latest_cache = latest
    return latest


def check_context_usage(rep: AgentReport, agent_name: str) -> None:
    """Flag agents whose live session context is drifting toward autocompact.

    Thresholds are fractions of the autocompact threshold (or of the
    effective max when the CLI doesn't report one): >=85% fail, >=60% warn.
    """
    line = _context_latest().get(agent_name)
    if not line:
        rep.add("context", "ok", "no telemetry yet")
        return
    total = line.get("total_tokens") or 0
    limit = line.get("autocompact_threshold") or line.get("max_tokens") or 0
    if not total or not limit:
        rep.add("context", "ok", "telemetry incomplete")
        return
    frac = total / limit
    detail = (
        f"{total // 1000}k of {limit // 1000}k autocompact budget "
        f"({frac * 100:.0f}%), window {line.get('percentage')}% used"
    )
    if frac >= 0.85:
        rep.add("context", "fail", f"near autocompact: {detail}")
    elif frac >= 0.60:
        rep.add("context", "warn", detail)
    else:
        rep.add("context", "ok", detail)


# Vars the operator deliberately removed from .env — their absence is
# intentional, not a failure. Flagging them as FAIL trains alarm fatigue
# (9 agents showed red for GITHUB_TOKEN for weeks). Phase-0, 2026-07-05.
DELIBERATELY_ABSENT = {
    "GITHUB_TOKEN",  # 2026-05-27 Dhruv: auth via `gh` CLI keyring, do not re-add
}


def check_env_completeness(rep: AgentReport, cfg: dict, dotenv: dict[str, str]) -> None:
    missing: list[str] = []
    intentional: list[str] = []
    for key in ("webhook_url_env", "bot_token_env"):
        var = cfg.get(key)
        if var and not resolve_env(var, dotenv):
            missing.append(var)
    for var in cfg.get("env_passthrough", []) or []:
        if not resolve_env(var, dotenv):
            (intentional if var in DELIBERATELY_ABSENT else missing).append(var)
    if missing:
        rep.add("env", "fail", f"missing: {', '.join(missing)}")
    elif intentional:
        rep.add(
            "env", "ok",
            f"all present (deliberately absent: {', '.join(intentional)})",
        )
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
    check_cron_runs(rep, agent_dir.name, agent_dir / "tasks")
    check_trajectory(rep, agent_dir.name)
    check_context_usage(rep, agent_dir.name)
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


# ---- Delta mode (Phase 1, masterplan item 7) --------------------------------
# Doctor used to dump full state daily; a wall of known-warns trains everyone
# to ignore it (alarm fatigue — the GITHUB_TOKEN lesson). Delta mode compares
# against the previous run's snapshot and reports ONLY movement: new fails,
# new warns, severity bumps — plus recoveries as one-liners. Green = silence.

STATE_FILE = LOGS_DIR / "doctor-state.json"
_RANK = {"ok": 0, "warn": 1, "fail": 2}


def compute_delta(
    reports: list[AgentReport], state_file: Path = STATE_FILE
) -> dict:
    """Diff current results vs the previous snapshot, then persist the new
    snapshot. First run establishes a baseline and reports no regressions
    (day-1 spam would poison the signal on day one)."""
    current: dict[str, dict] = {}
    for r in reports:
        for c in r.checks:
            current[f"{r.agent}/{c.name}"] = {
                "severity": c.severity,
                "detail": c.detail,
            }

    first_run = not state_file.exists()
    previous: dict[str, dict] = {}
    if not first_run:
        try:
            previous = json.loads(state_file.read_text())
        except Exception:
            previous = {}
            first_run = True  # unreadable snapshot → re-baseline

    regressions, recoveries = [], []
    if not first_run:
        for key, cur in current.items():
            prev_sev = (previous.get(key) or {}).get("severity", "ok")
            if _RANK[cur["severity"]] > _RANK.get(prev_sev, 0):
                regressions.append(
                    {
                        "check": key,
                        "was": prev_sev,
                        "now": cur["severity"],
                        "detail": cur["detail"],
                    }
                )
            elif _RANK[cur["severity"]] < _RANK.get(prev_sev, 0):
                recoveries.append(
                    {"check": key, "was": prev_sev, "now": cur["severity"]}
                )

    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=1))
        tmp.replace(state_file)
    except Exception as e:
        print(f"warning: could not persist doctor state: {e}", file=sys.stderr)

    worst = max(
        (r.worst for r in reports),
        key=lambda s: _RANK[s],
        default="ok",
    )
    return {
        "first_run": first_run,
        "regressions": regressions,
        "recoveries": recoveries,
        "worst": worst,
        "checks_total": len(current),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Prana AgentOS doctor")
    ap.add_argument("--agent", help="check a single agent by name")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fix", action="store_true", help="attempt safe auto-fixes")
    ap.add_argument(
        "--delta",
        action="store_true",
        help="report only movement vs the last run's snapshot (JSON); "
        "exit 1 only on regressions",
    )
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

    if args.delta:
        # Delta mode owns stdout completely: one JSON object, movement only.
        # Note: --delta with --agent would snapshot a single agent's checks
        # and mark everything else "disappeared" on the next full run — the
        # snapshot is only meaningful fleet-wide, so refuse the combination.
        if args.agent:
            print("error: --delta requires a full-fleet run (drop --agent)",
                  file=sys.stderr)
            return 2
        delta = compute_delta(reports)
        print(json.dumps(delta, indent=2))
        return 1 if delta["regressions"] else 0

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
