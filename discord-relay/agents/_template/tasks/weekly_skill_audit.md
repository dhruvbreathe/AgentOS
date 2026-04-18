<!--
cron: 0 4 * * 0
kind: systemEvent
-->
# Weekly skill audit (silent)

Run the skill-audit scanner across every agent's writable files, flag
prompt-injection / exfiltration / leaked-secrets patterns, and save the
result.

Steps:

1. `cd /Users/celainc/Developers/ClaudeAgentSDK/discord-relay`
2. `./.venv/bin/python scripts/audit_skills.py --shared > logs/audit-$(date +%Y-%m-%d).txt 2>&1`
3. `Read` the output. If there are any HIGH findings:
   - Route a message to `main` (Vayu) via `send_to_agent` with the agent + file + category, so the operator notices in the main channel
   - Do NOT modify the flagged file yourself — the operator decides
4. If no HIGH findings, silent exit. No webhook post (this is a systemEvent).

To enable: copy this file into `agents/<me>/tasks/weekly_skill_audit.md`,
uncomment the frontmatter, dry-run `python scheduler/install.py`, get
operator approval, then `--apply`.
