"""Gate for main/daily_doctor_check: runs `scripts/doctor.py --delta`.

first_run or no regressions -> print nothing (doctor has persisted its
snapshot, exactly as a normal delta run does; recoveries alone are never
posted). Regressions -> restore the previous snapshot in
logs/doctor-state.json so the agent's own `doctor.py --delta` reproduces the
same regressions (otherwise it would diff against the snapshot this gate
just wrote and see nothing), then print the delta JSON and wake the agent.
Any other doctor failure -> non-zero exit.
"""
from __future__ import annotations

import json

from gatelib import ROOT, VENV_PY, fail, load_env, run

STATE_FILE = ROOT / "logs" / "doctor-state.json"


def main() -> None:
    load_env()  # doctor reads VAULT_PATH etc. from the environment
    before = STATE_FILE.read_bytes() if STATE_FILE.exists() else None
    p = run([str(VENV_PY), str(ROOT / "scripts" / "doctor.py"), "--delta"], timeout=100)
    try:
        delta = json.loads(p.stdout)
    except ValueError:
        fail(f"doctor.py --delta exit {p.returncode}, unparseable output:\n"
             f"{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    if p.returncode not in (0, 1) or not isinstance(delta, dict):
        fail(f"doctor.py --delta exit {p.returncode}:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")

    if delta.get("first_run") or not delta.get("regressions"):
        return  # baseline set / clean / recoveries only: silence

    note = "previous snapshot restored"
    try:
        if before is None:
            STATE_FILE.unlink(missing_ok=True)
        else:
            tmp = STATE_FILE.with_suffix(".json.gate-tmp")
            tmp.write_bytes(before)
            tmp.replace(STATE_FILE)
    except OSError as e:
        note = f"COULD NOT restore previous snapshot ({e}); use the JSON below, not a rerun"
    print(f"doctor --delta: {len(delta['regressions'])} regression(s) "
          f"({note}, so your own `doctor.py --delta` run reproduces them):")
    print(json.dumps(delta, indent=1))


if __name__ == "__main__":
    main()
