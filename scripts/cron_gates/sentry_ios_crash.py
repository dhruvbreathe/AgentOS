"""Gate for ios-developer/daily_crash_triage. Checks the sources a script can
check cheaply: Sentry `apple-ios` (wakes on ANY event in 24h, not just new
issues) and the local Xcode Organizer crash store (wakes if it changed in
24h). App Store Connect crash metrics are not checked here; recent triage
runs used Sentry only. See sentry_digest.py."""
from pathlib import Path

from sentry_digest import main

main("apple-ios", "iOS crash triage", crash_mode=True,
     xcode_crash_dir=Path.home() / "Library/Developer/Xcode/Products/com.vayuprana.app/Crashes")
