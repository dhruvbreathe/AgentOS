# LESSONS.md — Fleet-Wide Hard Lessons (every agent, every session)

Cross-agent distillation of mistakes already paid for once. Each rule exists
because an agent burned real turns on it. Keep entries to 1-2 lines; promote
here only what applies to EVERY agent. (Per-agent lessons stay in your own
LEARNINGS.md.)

1. **Read before Edit/Write.** The Edit/Write tools hard-fail on any file not
   Read in the current session. A `limit:1` Read satisfies it. Backend burned
   10 retries/session on this.
2. **Dash gate BEFORE the send, chained with `&&`.** Any webhook/outbound
   text: `python3 -c "assert no em/en-dashes" && curl ...`. A scan after the
   POST is decoration. And never `grep -P` on macOS (BSD grep lacks it and
   fails OPEN) — use python.
3. **Set a real User-Agent on every urllib webhook POST** or Cloudflare
   returns 403 `error code: 1010`. curl is fine (has its own UA).
4. **`curl --form-string` for Discord `payload_json`,** never `-F` (mangles
   JSON). Never post with `-o /dev/null` — capture the body so failures
   self-diagnose.
5. **Supabase `created_at` is a SYNC time, not the event time.** Count
   revenue events by `purchase_date`. Store ledger (ASC + Play) is the
   paid-count truth, not `status='active'`.
6. **Check git state before claiming code state.** "The commit exists" ≠
   "it's on main" ≠ "it's deployed". `git fetch` first; local clones and
   other machines drift.
7. **Numbers in outbound copy need a FACTS.md conf tier.** No unsourced
   stats in anything an investor/customer reads. If FACTS disagrees with
   the live deck, the deck wins — then fix FACTS.
8. **Verify a "missing" credential in trajectories/vault before escalating**
   to the operator; keys usually survive in an agent's history.
9. **Prove process state, don't infer it.** `ps` output on this Mac
   truncates and misleads; billing/usage consoles and telemetry files are
   ground truth. Never assert a negative ("X never ran") from process
   inspection alone.
10. **Timestamps/units: sanity-check magnitudes before alarming.** A "3.6GB
    log" was 3.4MB; a "-67% signup crash" was a campaign-spike comparison.
    Check the baseline and the unit before escalating.
11. **Every cron with temporal lookback writes a memory note EVERY fire,**
    even when nothing happened — a missing day breaks the next run's chain.
12. **Background subagents die at turn end.** For work that must survive,
    finish it in-turn (block on it) or schedule a cron; never promise
    "running in background" across turns.
