"""Gate for android-developer/repo_sync_check: Vayu2.0_Android main vs origin/main.
Unsets GITHUB_TOKEN for the fetch, as the task does. See repo_sync.py."""
from repo_sync import main

main("/Users/celainc/Developers/Vayu2.0_Android", unset_github_token=True)
