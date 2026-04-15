---
cron: 0 9 * * *
---
Run daily API health checks against vayu-prana.com public endpoints and post results to #qa.

Base URL: https://vayu-prana.com

## Primary probe (run first)

GET /api/status — returns { overall, services[], checkedAt, uptime }
  - Check `overall` is "operational"
  - Check each service in `services[]` has status "operational": Database, Authentication, Storage, API, Website, AI Service
  - Flag any service with responseTime > 500ms
  - If overall != "operational", this is Sev-1 — stop here and report

## Secondary probes (run if primary passes)

| Route | Method | Expected key in response |
|---|---|---|
| /api/health | GET | `status` |
| /api/blog?limit=1 | GET | `posts` (array, length >= 1) |
| /api/public/facts | GET | `name` |
| /api/public/faq | GET | `count` (number > 0) |
| /api/public/techniques | GET | `count` (number > 0) |
| /api/public/comparisons | GET | `count` (number > 0) |
| /api/public/changelog | GET | `count` (number > 0) |
| /api/public/openapi.json | GET | `openapi` (string, should be "3.0.0") |
| /api/mcp | GET | `name` |

No auth needed on any of these.

## What to flag

- Any non-200 response: Sev-2 (single endpoint) or Sev-1 (multiple)
- Any endpoint > 2s response time: note as slow
- Missing expected keys in response body: Sev-2
- /api/status showing any service as non-operational: Sev-1

## Reporting

Post a summary to #qa with pass/fail table, response times, and overall status.
Baseline times (2026-04-15): all under 1.1s, /api/status at 1.09s being the slowest.
