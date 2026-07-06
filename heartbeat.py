"""Phase-1 heartbeats (masterplan item 5): the missing "alive" feel.

Agents were purely reactive — inbound messages + isolated crons. A heartbeat
is a scheduled self-check that runs IN the agent's channel session, so it has
full conversational context, and stays SILENT unless action is warranted.

Why relay-level (in the bot process) instead of a launchd cron:
  - launchd crons run in a separate process and can't hold the channel lock,
    so a cron resuming the channel session could race a live operator turn
    (two writers, one transcript). In-process, the scheduler acquires the
    SAME per-channel asyncio.Lock the message handler uses — a heartbeat can
    never collide with a real turn; it just waits its turn.
  - The channel session is resumed (with the normal rotation check), so the
    heartbeat sees what the operator and agent have been talking about and
    its context lands where follow-ups happen.
  - Output goes to a CollectingSink: discarded, logged in the trajectory.
    "Posting" is an ACTION the agent takes deliberately (webhook curl or
    send_to_agent), which is exactly the silent-unless-action contract.

Config (config.yaml → defaults.heartbeat):
    heartbeat:
      enabled: true
      interval_minutes: 240          # per-agent beat cadence
      active_hours: [8, 22]          # local-time window, [start, end)
      agents: [main, project-manager, backend-developer, marketing]

An agent must ALSO have agents/<name>/HEARTBEAT.md — no checklist, no beat.
State (last beat per agent) persists in logs/heartbeats.json so restarts
don't re-fire everything at boot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "logs" / "heartbeats.json"

log = logging.getLogger("heartbeat")

_TICK_SECONDS = 300          # how often we check for due beats
_BOOT_GRACE_SECONDS = 180    # let the bot settle before the first tick

HEARTBEAT_PROMPT = """\
[heartbeat] Scheduled self-check. This is NOT an operator message — nobody \
is waiting on a reply, and your final text here is DISCARDED (never posted \
to Discord).

Run your heartbeat checklist below. Contract:
1. Investigate cheaply first (files, logs, ledger, cheap curls). Most beats \
should find nothing.
2. Act ONLY when a checklist item genuinely warrants it. Acting means: post \
to your channel via your webhook, route via send_to_agent, update the task \
ledger, or fix something small in your own workspace.
3. If nothing needs action: append one line to today's memory file \
(`HH:MM heartbeat: all clear`) and reply with exactly `HEARTBEAT_OK`.
4. If you act: append one line to today's memory file saying what you did.
5. Budget: aim for under ~15 tool calls. This is a pulse, not a work session.
6. Never wake the operator for something that can wait for the daily digest \
— channel posts from a heartbeat are for things that are timely AND \
actionable now.

--- YOUR HEARTBEAT CHECKLIST (agents/{agent}/HEARTBEAT.md) ---
{checklist}
"""


def _load_state() -> dict[str, float]:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict[str, float]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1))
        tmp.replace(STATE_FILE)
    except Exception as e:
        log.warning("could not persist heartbeat state: %s", e)


class HeartbeatScheduler:
    """One per RelayBot instance; beats only the agents that client owns.

    Every failure path is fail-open: a broken heartbeat must never take the
    relay down or wedge a channel lock.
    """

    def __init__(self, bot) -> None:
        self.bot = bot
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Idempotent — on_ready re-fires on every reconnect."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._loop(), name=f"heartbeat-{self.bot.label}"
            )
            log.info("[%s] heartbeat scheduler started", self.bot.label)

    def _config(self) -> dict:
        return (
            (self.bot.global_cfg.get("defaults", {}) or {}).get("heartbeat")
            or {}
        )

    async def _loop(self) -> None:
        await asyncio.sleep(_BOOT_GRACE_SECONDS)
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("[%s] heartbeat tick failed", self.bot.label)
            await asyncio.sleep(_TICK_SECONDS)

    async def _tick(self) -> None:
        cfg = self._config()
        if not cfg.get("enabled"):
            return
        pilots = set(cfg.get("agents") or [])
        if not pilots:
            return
        interval_s = float(cfg.get("interval_minutes", 240)) * 60
        start_h, end_h = (cfg.get("active_hours") or [8, 22])[:2]
        now_hour = datetime.now().hour  # local time — bot runs on the laptop
        if not (start_h <= now_hour < end_h):
            return

        state = _load_state()
        now = time.time()
        for channel_id, agent in list(self.bot.agents.items()):
            if agent.name not in pilots:
                continue
            # Beat only on the agent's PRIMARY channel — extra channels of
            # the same agent share the AgentConfig and would double-beat.
            if channel_id != agent.channel_id:
                continue
            if now - state.get(agent.name, 0.0) < interval_s:
                continue
            hb_file = ROOT / "agents" / agent.name / "HEARTBEAT.md"
            if not hb_file.exists():
                continue  # opted in by config but no checklist yet — skip
            # Stamp BEFORE running: a crashing beat must not re-fire every
            # tick and hammer the model. Missing one beat is fine.
            state[agent.name] = now
            _save_state(state)
            await self._run_beat(channel_id, agent, hb_file)

    async def _run_beat(self, channel_id: str, agent, hb_file: Path) -> None:
        # Imports deferred to avoid a bot↔heartbeat import cycle at load.
        from bot import _save_session
        from relay import CollectingSink, run_agent
        import session_rotation

        try:
            checklist = hb_file.read_text().strip()
        except Exception as e:
            log.warning("[%s] heartbeat: unreadable %s: %s",
                        agent.name, hb_file, e)
            return
        prompt = HEARTBEAT_PROMPT.format(agent=agent.name, checklist=checklist)

        t0 = time.time()
        async with self.bot._channel_lock(channel_id):
            resume = self.bot.sessions.get(channel_id)
            rot_note = session_rotation.check(
                agent.name,
                resume,
                (self.bot.global_cfg.get("defaults", {}) or {}).get(
                    "session_rotation"
                ),
            )
            if rot_note is not None:
                resume = None
                prompt = f"{rot_note}\n\n{prompt}"
            sink = CollectingSink()
            try:
                final_text, session_id = await run_agent(
                    agent, prompt, sink, resume_session_id=resume
                )
            except Exception:
                log.exception("[%s] heartbeat run failed", agent.name)
                return
            if session_id:
                self.bot.sessions[channel_id] = session_id
                _save_session(channel_id, session_id)

        quiet = "HEARTBEAT_OK" in (final_text or "")
        log.info(
            "[%s] heartbeat %s in %.0fs%s",
            agent.name,
            "all-clear" if quiet else "ACTED",
            time.time() - t0,
            "" if quiet else f" — {(final_text or '')[:200]}",
        )
