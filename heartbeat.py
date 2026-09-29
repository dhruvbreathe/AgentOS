"""Phase-1 heartbeats (masterplan item 5): the missing "alive" feel.

Agents were purely reactive — inbound messages + isolated crons. A heartbeat
is a scheduled self-check that stays SILENT unless action is warranted.

Isolated since 2026-09-29 (OpenClaw isolatedSession + lightContext, Hermes
background_review). Beats used to resume the operator's channel session on
a cheaper model: every beat appended its chatter to the operator's context
(68k tokens of it in main's session) and wrote a 100k+ cache on a model the
operator never uses. Now each beat is a FRESH session on the lite prompt,
seeded with a short digest of the agent's daily memory instead of chat
history. It never writes to bot.sessions and doesn't take the channel lock.
A beat that ACTS leaves a one-line note the bot prepends to the channel's
next operator turn, so the conversation still hears about it.

  - Output goes to a CollectingSink: discarded, logged in the trajectory.
    "Posting" is an ACTION the agent takes deliberately (webhook curl or
    send_to_agent), which is exactly the silent-unless-action contract.
  - Quota breaker: beats stand down while quota_state says background work
    should (warning past threshold, or a window rejected).

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

--- RECENT WORKING STATE (tail of your daily memory; this beat runs in a \
fresh session, not your operator conversation) ---
{digest}

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
        # One-shot warning registry — a misconfigured pilot (retired agent
        # still listed, checklist missing/empty) logs ONCE per process
        # instead of silently rotting (the marketing-stale-17h failure) or
        # spamming every 5-min tick.
        self._warned: set[str] = set()

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

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg)

    @staticmethod
    def _checklist_is_empty(text: str) -> bool:
        """True when the checklist has no actionable content — only blanks,
        markdown headings, comments, or horizontal rules. OpenClaw
        suppression-pack semantics: no checklist items, no beat."""
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(("#", "<!--", "---", ">")):
                continue
            return False
        return True

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
        import quota_state
        ok, reason = quota_state.background_allowed()
        if not ok:
            log.info("[%s] heartbeats standing down: %s", self.bot.label, reason)
            return

        # Pilot hygiene lives in bot._run_all (fleet-wide). A per-client
        # pilots-vs-loaded diff false-warns for every pilot owned by a
        # different token's client, drowning the real-retirement signal.

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
                self._warn_once(
                    f"no-checklist:{agent.name}",
                    f"[heartbeat] pilot '{agent.name}' has no HEARTBEAT.md — "
                    f"skipping its beats",
                )
                continue
            # skip_when_busy (OpenClaw suppression pack): a beat queued
            # behind a live operator turn would land minutes late into a
            # conversation nobody invited it to. Skip WITHOUT stamping so
            # the beat retries on the next 5-min tick once the channel is
            # free.
            if cfg.get("skip_when_busy", True) and self.bot._channel_lock(
                channel_id
            ).locked():
                log.debug("[%s] heartbeat deferred — channel busy", agent.name)
                continue
            try:
                checklist_text = hb_file.read_text()
            except Exception as e:
                self._warn_once(
                    f"unreadable:{agent.name}",
                    f"[heartbeat] unreadable {hb_file}: {e}",
                )
                continue
            # skip-when-empty: no actionable checklist items, no model run.
            if self._checklist_is_empty(checklist_text):
                self._warn_once(
                    f"empty-checklist:{agent.name}",
                    f"[heartbeat] pilot '{agent.name}' checklist is empty — "
                    f"skipping its beats until it has items",
                )
                continue
            # Stamp BEFORE running: a crashing beat must not re-fire every
            # tick and hammer the model. Missing one beat is fine.
            state[agent.name] = now
            _save_state(state)
            await self._run_beat(channel_id, agent, hb_file, cfg)

    async def _run_beat(
        self, channel_id: str, agent, hb_file: Path, cfg: dict | None = None
    ) -> None:
        # Imports deferred to avoid a bot↔heartbeat import cycle at load.
        from agent_loader import load_agent
        from relay import CollectingSink, run_agent
        import session_rotation

        cfg = cfg or self._config()
        try:
            checklist = hb_file.read_text().strip()
            # Lite prompt (identity/tools/integrations/memory): a beat is a
            # checklist pulse, not a conversation.
            beat_agent = load_agent(agent.name, lite=True)
        except Exception as e:
            log.warning("[%s] heartbeat: setup failed: %s", agent.name, e)
            return
        prompt = HEARTBEAT_PROMPT.format(
            agent=agent.name,
            checklist=checklist,
            digest=session_rotation._memory_tail(agent.name),
        )

        t0 = time.time()
        sink = CollectingSink()
        outcome: dict = {}
        try:
            # Utility-model beats (Wave 3 P0-3, OpenClaw utilityModel): the
            # pulse runs on a cheaper model + lower effort than the agent's
            # real turns. Config: defaults.heartbeat.model / .effort.
            final_text, _session = await run_agent(
                beat_agent, prompt, sink, resume_session_id=None,
                model_override=cfg.get("model"),
                effort_override=cfg.get("effort"),
                origin="heartbeat",
                outcome=outcome,
            )
        except Exception:
            log.exception("[%s] heartbeat run failed", agent.name)
            return

        if outcome.get("error") or outcome.get("is_error"):
            # Quota / auth errors come back as text; they are not actions.
            log.warning("[%s] heartbeat FAILED (%s) in %.0fs: %s",
                        agent.name, outcome.get("error") or outcome.get("api_error_status"),
                        time.time() - t0, (final_text or "")[:200])
            return
        quiet = "HEARTBEAT_OK" in (final_text or "")
        log.info(
            "[%s] heartbeat %s in %.0fs%s",
            agent.name,
            "all-clear" if quiet else "ACTED",
            time.time() - t0,
            "" if quiet else f" — {(final_text or '')[:200]}",
        )
        if not quiet and final_text:
            note = (
                f"[heartbeat {datetime.now():%H:%M}, separate session] "
                f"{final_text.strip()[:400]}"
            )
            self.bot._pending_notes.setdefault(channel_id, []).append(note)
