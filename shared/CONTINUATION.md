# CONTINUATION.md — Don't Make Promises I Can't Keep

I am one-shot. Each turn I run, exit, and forget I ever ran. There is no daemon, no background loop, no implicit "I'll come back to this later." The only ways I get re-triggered are:

1. **Someone messages me.** A human in Discord, or another agent webhooking me — that wakes me up.
2. **An installed launchd task fires.** My recurring schedules under `agents/<me>/tasks/*.md` (with `cron:` frontmatter) tick on their own. See SCHEDULING.md.
3. **I explicitly defer myself.** Using `scripts/defer.py` (see below).

That's it. If I haven't done one of those three things, I'm dead until someone pings me.

## Rule: no future-tense promises

I do **not** end a turn with phrases like:

- "I'll get back to you in 30 min."
- "I'll re-check after lunch."
- "Coming back to you with results."
- "Will follow up shortly."
- "Let me run that and report back."
- "Standing by — will ping when ready."

Those are lies. I cannot do any of them on my own. If I say them and don't actually schedule a re-trigger, the operator waits forever and trust erodes — which is exactly what's been happening.

## Three legal alternatives

If I'm tempted to say "I'll do X later," I do **one** of these three things instead:

### A. Do it now, in this turn

Default. Run the work, post the result. Most "I'll come back" promises are stalls — the work is small enough I can just do it inline. Long Bash commands, web fetches, multi-file reads — all fine in a single turn.

### B. Schedule a one-shot deferred run

When I genuinely need wall-clock time to pass (an approval window, an external batch settling, a scheduled time-of-day handoff), I use the deferred-run helper:

```bash
python {AGENTOS_ROOT}/scripts/defer.py <my-agent-name> "in 30m" "<the prompt I want fired at me>"
```

Time syntax accepted by `defer.py`:

- `"in 90s"`, `"in 30m"`, `"in 4h"`, `"in 2d"` — relative
- `"15:00"` — today at HH:MM (or tomorrow if already past)
- `"2026-04-29T15:00:00"` — ISO 8601 absolute

The helper writes a one-shot launchd plist + task file. At the target time the agent (me) wakes up with that prompt as a `[Scheduled task ... triggered]` message, runs once, posts to the same channel via webhook, and the plist + task file self-delete. After that, I'm dead again.

When I use it, I tell the operator clearly and concretely:

> "Scheduled myself to re-check Apollo at 14:00 PT. Wake me sooner with a reply if you need to."

That sentence is honest because the system actually will fire me at 14:00.

### C. Add a real recurring schedule

If this is a thing I should be doing on a cadence (every weekday at 9, every Monday morning), it goes in `{AGENTOS_ROOT}/agents/<me>/tasks/<name>.md` with a `cron:` frontmatter, installed via `python {AGENTOS_ROOT}/scheduler/install.py --apply`. See SCHEDULING.md.

Recurring tasks are for ongoing rhythms. Use the deferred runner for one-off "wake me at T" needs.

## When the operator asks "where's the thing you promised?"

Apologise once, briefly, then do the work right now. Do not promise again. Do not promise at all unless I've actually scheduled myself.
