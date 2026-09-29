"""Gate for ios-developer/daily_sentry_report (Sentry project `apple-ios`).
Quiet-day line carries the 7-day received trend the task asks for.
See sentry_digest.py."""
from sentry_digest import main

main("apple-ios", "iOS", trend=True)
