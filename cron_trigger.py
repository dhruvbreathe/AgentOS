"""Cron entry point. Runs a task prompt through an agent and posts the
result to the agent's webhook.

Usage:
    python cron_trigger.py <agent_name> <task_name>

`<task_name>` refers to agents/<agent_name>/tasks/<task_name>.md — the file's
contents are used as the prompt.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import yaml
from dotenv import load_dotenv

import quota_state
from agent_loader import load_agent
from relay import CollectingSink, incomplete_notice, run_agent, stopped_early

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "com.agentos"

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Strip YAML frontmatter from a task file. Returns (fm_dict, body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, text[m.end():]

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("cron-trigger")

CHUNK_LIMIT = 1900  # Discord message cap is 2000; leave room.


# Cloudflare blocks UA-less / default-aiohttp-UA posts with error 1010 → 403.
_WEBHOOK_UA = "DiscordBot (PranaAgentOS, 1.0)"
_WEBHOOK_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def _post_webhook(webhook_url: str, content: str, username: str) -> bool:
    """Post (chunked) to a Discord webhook. Returns True if every chunk landed."""
    # Strip em-dashes / en-dashes before chunking (operator directive 2026-05-19).
    from text_lint import sanitize as _strip_emdash
    content = _strip_emdash(content, agent=username, surface="cron_webhook")
    # Split into chunks to respect Discord's 2000-char limit.
    chunks = [content[i : i + CHUNK_LIMIT] for i in range(0, len(content), CHUNK_LIMIT)] or [content]
    ok = True
    async with aiohttp.ClientSession(
        timeout=_WEBHOOK_TIMEOUT, headers={"User-Agent": _WEBHOOK_UA}
    ) as session:
        for chunk in chunks:
            payload = {"content": chunk, "username": username}
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status >= 300:
                    body = await resp.text()
                    log.error("webhook post failed %s: %s", resp.status, body)
                    ok = False
    return ok


async def _run(agent_name: str, task_name: str) -> int:
    # Peek at the task file before loading the agent — `bootstrap: lite`
    # in the frontmatter changes how we load the system prompt.
    tasks_dir = Path(__file__).resolve().parent / "agents" / agent_name / "tasks"
    task_file = tasks_dir / f"{task_name}.md"
    if not task_file.exists():
        log.error("No task file at %s", task_file)
        return 2

    raw = task_file.read_text()
    fm, body = _parse_frontmatter(raw)

    # Task kind — "systemEvent" (or silent: true) means internal housekeeping:
    # run the agent, capture the output in the trajectory + stdout, do NOT
    # post to the Discord webhook. Use for memory maintenance, LEARNINGS
    # distillation, vault hygiene, etc. The default is "post" — output goes
    # to the agent's channel via webhook.
    kind = str(fm.get("kind", "")).strip().lower()
    silent = bool(fm.get("silent")) or kind in ("systemevent", "system_event", "internal")
    # One-shot deferred runs created via scripts/defer.py — after this run
    # we delete the plist + task file so the agent doesn't re-fire.
    oneshot = bool(fm.get("oneshot"))
    # Lite bootstrap — strip SOUL/USER/HUMANIZER/etc. for single-shot
    # housekeeping crons that don't need the full personality. ~70% token cut.
    bootstrap = str(fm.get("bootstrap", "")).strip().lower()
    lite = bootstrap in ("lite", "minimal", "light")

    # Quota breaker (2026-09-29): under quota pressure, stand down and let
    # bot.py replay this run after the window resets. `critical: true` in
    # the frontmatter opts a task out (it runs regardless).
    if not fm.get("critical"):
        ok, reason = quota_state.background_allowed()
        if not ok:
            log.warning("quota: deferring %s/%s (%s)", agent_name, task_name, reason)
            quota_state.defer_cron(agent_name, task_name, reason)
            return 0

    # Script gate (2026-09-29, Hermes cron/scheduler.py). `script:` runs a
    # cheap pre-check before any model call:
    #   non-zero exit          -> wake the agent with the failure (still alerts)
    #   empty stdout           -> nothing changed: no model call, no post
    #   last line {"wakeAgent": false, "deliver": "..."} -> no model call; the
    #                             optional `deliver` text is posted as-is
    #   `no_agent: true`       -> stdout posted as-is, never a model call
    #   otherwise              -> stdout appended to the prompt
    script_block = ""
    if fm.get("script"):
        rc, out, err = await _run_gate_script(
            str(fm["script"]), float(fm.get("script_timeout", 120))
        )
        out = out.strip()
        wake, deliver = _parse_wake_gate(out) if rc == 0 else (True, None)
        if rc == 0 and (not out or not wake or fm.get("no_agent")):
            text = deliver if deliver is not None else (out if fm.get("no_agent") else "")
            log.info("gate %s/%s: no model call (%s)", agent_name, task_name,
                     "delivered" if text else "nothing to report")
            if text:
                return await _deliver(load_agent(agent_name, lite=True), task_name, text, silent)
            return 0
        if rc != 0:
            script_block = (
                f"\n\n## Script Output (gate script FAILED, exit {rc})\n"
                f"```\n{(out + chr(10) + err).strip()[:8000]}\n```"
            )
        else:
            script_block = f"\n\n## Script Output\n```\n{out[:12000]}\n```"

    agent = load_agent(agent_name, lite=lite)
    if lite:
        log.info("Loaded %s in lite-bootstrap mode (task=%s)", agent_name, task_name)
        # Default lite cron fires to Sonnet. Mechanical housekeeping
        # (memory maintenance, doctor checks, health pings, audits) doesn't
        # need Opus-grade reasoning. Task frontmatter `model:` overrides.
        lite_model = str(fm.get("model", "")).strip() or "claude-sonnet-5-5"
        if getattr(agent.options, "model", None) != lite_model:
            try:
                agent.options.model = lite_model
                log.info("Lite override → model=%s", lite_model)
            except Exception:
                # ClaudeAgentOptions could be frozen in some SDK versions;
                # fail open — Opus still runs, just slower/pricier.
                log.warning("Could not downshift to %s; keeping default", lite_model)
        # The CLI refuses to start if --fallback-model equals --model. Lite
        # crons downshift the primary to Sonnet, which collides with the
        # default fallback_model (also Sonnet since 2026-06-13). When they
        # match, drop the fallback so the run can start. (This silently
        # killed every lite housekeeping cron across all agents for ~12 days.)
        try:
            if getattr(agent.options, "fallback_model", None) == getattr(
                agent.options, "model", None
            ):
                agent.options.fallback_model = None
                log.info("Cleared fallback_model (collided with lite model)")
        except Exception:
            log.warning("Could not reconcile fallback_model; run may fail")

    max_turns = getattr(agent.options, "max_turns", None)
    prompt = (
        f"[Scheduled task `{task_name}` triggered at "
        f"{datetime.now().isoformat(timespec='seconds')}]\n\n{body}"
        + script_block
        + "\n\nOnly your FINAL message (the text after your last tool call) gets "
        "posted. Make it the complete deliverable, not progress narration. If you "
        "already posted the deliverable yourself (e.g. a webhook card), or there is "
        "genuinely nothing worth posting, make the final message exactly "
        "`[SILENT]` and nothing will be posted."
        + (
            f" This run stops hard at {max_turns} turns (one per tool-call round). "
            "If it stops before your final message, only a one-line failure "
            "notice gets posted, so budget turns to land the final message "
            "well before the cap."
            if max_turns else ""
        )
    )

    sink = CollectingSink()
    try:
        outcome: dict = {}
        text, _session = await run_agent(
            agent, prompt, sink, origin="cron", outcome=outcome
        )
        # A quota / auth failure comes back as assistant text ("You've hit
        # your limit", "401 ..."). Never post that as if it were the digest.
        if outcome.get("error") in ("rate_limit", "billing_error"):
            log.warning("quota error on %s/%s: deferring", agent.name, task_name)
            quota_state.defer_cron(agent_name, task_name, str(outcome["error"]))
            return 4
        if outcome.get("error") == "authentication_failed":
            log.error("auth failed on %s/%s: %s", agent.name, task_name, text[:200])
            return 5

        action, out = _final_action(text, outcome)
        if action == "silent":
            log.info("task %s/%s replied [SILENT]: nothing posted", agent.name, task_name)
            return 0
        if action == "notice":
            # Marker string is in doctor.py's fail signatures, keep in sync.
            log.warning("task %s/%s INCOMPLETE run: %s\nwithheld narration (tail):\n%s",
                        agent.name, task_name, out, text[-4000:])
            return await _deliver(agent, task_name, out, silent) or 6
        return await _deliver(agent, task_name, out, silent)
    finally:
        if oneshot:
            _cleanup_oneshot(agent_name, task_name, task_file)


def _final_action(text: str, outcome: dict) -> tuple[str, str]:
    """Decide what a finished run delivers: ("post", text), ("silent", "")
    or ("notice", one line).

    A run with no text after its last tool call has no final message, so
    `text` is relay's narration fallback ("Pulling Sentry now", "Rendering
    the chart"). That is never the deliverable. It gets a one-line notice
    with the trace path instead (Aria, 2026-10-01: daily_crash_triage hit
    max_turns 41/40 mid render and posted 1.4K chars of notes). The one
    exception: a run that ended on its own with [SILENT] as its last note
    still meant silence."""
    if outcome.get("reply_complete", True):
        return ("silent", "") if _is_silent(text) else ("post", text)
    if not stopped_early(outcome) and _is_silent(outcome.get("last_note") or ""):
        return ("silent", "")
    return ("notice", "⚠️ " + incomplete_notice(outcome))


def _is_silent(text: str) -> bool:
    """[SILENT] as the reply's first or last non-empty line. run_agent now
    returns the reply without the trace, but an agent that writes one
    closing line before the marker ("Card posted.\\n\\n[SILENT]") must still
    stay silent. Before 2026-09-30 only startswith() was checked, against a
    buffer that began with tool lines, so [SILENT] never matched after a
    tool call and the whole trace got posted."""
    lines = [_bare(ln) for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    return lines[0].startswith("[SILENT]") or lines[-1].endswith("[SILENT]")


def _bare(line: str) -> str:
    """Drop markdown wrapping and trailing punctuation so `[SILENT]` in
    backticks, **[SILENT]** and "[SILENT]." all count. The prompt itself
    quotes the marker in backticks, so models echo them (review 2026-10-01)."""
    return line.strip().strip("`*_ ").rstrip(".!").rstrip("`*_ ")


async def _deliver(agent, task_name: str, text: str, silent: bool) -> int:
    """Post a task's output to the agent's channel (or stdout when silent)."""
    if silent:
        log.info("systemEvent task %s/%s completed: output in trajectory, no webhook post",
                 agent.name, task_name)
        print(text)
        return 0
    if not agent.webhook_url:
        log.warning("Agent %s has no webhook_url; printing to stdout instead", agent.name)
        print(text)
        return 0
    posted = await _post_webhook(
        agent.webhook_url, f"**[{task_name}]**\n" + text,
        username=f"{agent.name} (scheduled)",
    )
    # Output exists in the trajectory + task log, but the operator never saw
    # it. Nonzero exit so launchd logs show the failure.
    return 0 if posted else 3


async def _run_gate_script(script: str, timeout: float) -> tuple[int, str, str]:
    """Run a gate script (path relative to the AgentOS root). .py runs on the
    venv interpreter, .sh on bash, anything else is exec'd directly."""
    root = Path(__file__).resolve().parent
    path = Path(script) if Path(script).is_absolute() else root / script
    if path.suffix == ".py":
        argv = [str(root / ".venv" / "bin" / "python"), str(path)]
    elif path.suffix == ".sh":
        argv = ["/bin/bash", str(path)]
    else:
        argv = [str(path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "", f"gate script timed out after {timeout:.0f}s"
    except Exception as e:  # noqa: BLE001 — a broken gate must still wake the agent
        return 127, "", f"gate script failed to start: {e}"


def _parse_wake_gate(out: str) -> tuple[bool, str | None]:
    """Last stdout line {"wakeAgent": false, "deliver": "..."} skips the model."""
    import json as _json
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if not last.startswith("{"):
        return True, None
    try:
        d = _json.loads(last)
    except ValueError:
        return True, None
    if not isinstance(d, dict) or d.get("wakeAgent", True):
        return True, None
    deliver = d.get("deliver")
    return False, str(deliver) if deliver else None


def _cleanup_oneshot(agent_name: str, task_name: str, task_file: Path) -> None:
    """Bootout + delete the launchd plist + task file for a one-shot deferred run.

    Order matters: unlink the files BEFORE issuing `launchctl bootout`. We
    are running INSIDE the launchd-managed process; bootout sends SIGTERM
    to the calling process before returning, so any work after the bootout
    call is unreliable. Files-first guarantees the on-disk state is clean
    even if our process gets killed.

    Best-effort: failures are logged but never raised — a stuck plist is
    inconvenient but not data-corrupting, and we don't want cleanup errors
    to mask the actual run result.
    """
    label = f"{LABEL_PREFIX}.{agent_name}-{task_name}"
    plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
    for p in (plist_path, task_file):
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            log.warning("oneshot unlink failed for %s: %s", p, e)
    log.info("oneshot files removed: %s", label)
    # Bootout last. launchd will SIGTERM us once it processes this; we may
    # never return from this subprocess.run call. That's fine — the on-disk
    # cleanup above is what matters.
    try:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{label}"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        log.warning("oneshot bootout failed for %s: %s", label, e)


def _record_run(agent: str, task: str, rc: int, started: float) -> None:
    """Append a structured run record to logs/cron_runs.jsonl. Pattern from
    OpenClaw's gateway cron (JSONL run history) — gives doctor.py and weekly
    health digests something better than grepping per-task text logs."""
    import json as _json
    import time as _time
    from datetime import datetime, timezone
    try:
        path = Path(__file__).resolve().parent / "logs" / "cron_runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "agent": agent,
            "task": task,
            "exit": rc,
            "duration_s": round(_time.monotonic() - started, 1),
        }
        with open(path, "a") as f:
            f.write(_json.dumps(rec) + "\n")
    except Exception as e:
        log.warning("run record write failed: %s", e)


def main() -> None:
    import time as _time
    p = argparse.ArgumentParser()
    p.add_argument("agent", help="Agent name (folder under agents/)")
    p.add_argument("task", help="Task name (file under agents/<agent>/tasks/<task>.md)")
    args = p.parse_args()
    started = _time.monotonic()
    try:
        rc = asyncio.run(_run(args.agent, args.task))
    except Exception:
        _record_run(args.agent, args.task, rc=1, started=started)
        raise
    _record_run(args.agent, args.task, rc=rc, started=started)
    sys.exit(rc)


if __name__ == "__main__":
    main()
