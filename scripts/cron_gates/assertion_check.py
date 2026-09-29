"""Gate for main/assertion_check: runs `scripts/assert_trajectories.py --all
--days 1` (stateless, read-only).

exit 0 (total fail == 0; warns and dash-drift are not news) -> print nothing.
exit 1 (at least one fail) -> print the report and wake the agent.
Anything else -> non-zero exit with the output.
"""
from __future__ import annotations

from gatelib import ROOT, VENV_PY, fail, load_env, run


def main() -> None:
    load_env()
    p = run([str(VENV_PY), str(ROOT / "scripts" / "assert_trajectories.py"),
             "--all", "--days", "1"], timeout=100)
    if p.returncode == 0:
        return  # zero fails: stay silent, as the task says
    if p.returncode == 1:
        print("assert_trajectories.py --all --days 1 reported FAILS:")
        print(p.stdout.strip()[-10000:])
        return
    fail(f"assert_trajectories.py exit {p.returncode}:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")


if __name__ == "__main__":
    main()
