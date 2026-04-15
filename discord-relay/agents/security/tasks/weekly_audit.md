---
cron: 0 8 * * 1
---
Run a weekly security audit and post status to channel.

1. Check for known CVEs affecting our core stack: Node.js, Python, Swift, Kotlin. Flag anything with CVSS >= 7.0.

2. Check if the Sentry token is still expired or has been rotated. Note current token status.

3. Review any new dependencies added to the codebase in the past 7 days. Flag any with known vulnerabilities or unusual permissions.

4. Scan for hardcoded secrets, exposed API keys, or .env files committed to git.

Post a security status:
- CVEs found (critical / high / medium)
- Sentry token status
- New dependency risks
- Overall posture: GREEN / YELLOW / RED
