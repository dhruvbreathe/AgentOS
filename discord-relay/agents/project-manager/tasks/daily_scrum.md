---
cron: 0 8 * * *
---
Run the daily scrum.

Steps:
1. Ping every agent in the roster (ios-developer, android-developer, backend-developer, web-developer, qa, marketing, media, social-media, ads, reddit-crawler, security, deepali, market-intelligence-engine, ui-ux-designer) with a short scrum check-in: what shipped, what's in flight, what's blocked. One outbound per turn — stagger across the session if needed.

2. Pull fresh Trello state via curl against board `6826a88e2399326484025de9` using `TRELLO_API_KEY` and `TRELLO_TOKEN` from the env. Note changes since yesterday (new cards, moves, archives).

3. Read `Agents/TASKS.md`, `Agents/HANDOFFS.md` for anything new.

4. Collect replies that came in. For agents that didn't reply, note them as silent but don't chase twice in one session.

5. Write a compiled snapshot to `Agents/scrum-YYYY-MM-DD.md` in the vault.

6. Route a tight summary to `main` (Vayu) via `send_to_agent`. Include:
   - Headline state (shipped / in flight / blocked counts)
   - Blockers that need Dhruv
   - Decisions needed
   - Any cross-pod dependencies that are drifting

Keep it focused. This is hygiene, not narrative. Done agents don't need chasing. The point is to surface what's stuck and what needs a human.
