---
cron: 0 9 * * *
---
Run daily Flowise health checks against all 6 chatflows and post results to #qa.

Base URL: https://flowise-i7wm.onrender.com
Prediction endpoint: POST /api/v1/prediction/{chatflowId}
Auth: Authorization: Bearer <key from QA_FLOWISE_API_KEY env or memory/2026-04-15.md>

Chatflows to check:
1. Training — dbd361e2-d6ee-4287-9679-621f54381fe7 (expect `text` field)
2. Prana Web Chat — c9e2a8e4-dc4d-4c37-abff-6d7154739df4 (expect `text` field)
3. iOS/Android App — 0555c447-4cc6-4d4e-96d4-67bf181e8281 (expect `text` field)
4. In-App Journaling — fb79f4fa-fb99-4b1e-bfa0-f35e40329636 (expect `text` field)
5. App Notifications — 179c90b3-05d5-4d2a-a937-3cf03c11ee4b (expect `json.motivation` field)
6. Guided Meditation — 6d6dd512-3eba-4b0b-8eca-09b8e6c52ce7 (expect `json.meditation` field)

For each chatflow:
- Send POST with {"question":"health check ping"}
- Record HTTP status code, response time, and whether the expected response field is present
- Flag any chatflow that returns non-200, takes >30s (except Guided Meditation baseline is ~25s), or is missing expected content

Post a summary to Discord #qa:
- Table of results (chatflow, status, time)
- Overall PASS/FAIL
- Any anomalies flagged with severity

If all 6 fail with the same error, it's likely an LLM key expiration — flag as Sev-1 blocker and tag Dhruv.
