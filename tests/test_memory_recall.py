"""Unit suite for memory_recall (Phase-2 item 8). Run:
    ./.venv/bin/python tests/test_memory_recall.py
Builds a throwaway vault + agent memory tree, points VAULT_PATH at it,
and exercises the recall contract: match, skip, noise-floor, fail-open,
budget, config-off.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import memory_recall  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def fresh_cache() -> None:
    memory_recall._cache.clear()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="recall-test-"))
    vault = tmp / "vault"
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=60)).isoformat()

    # --- vault fixtures -------------------------------------------------
    (vault / "Sessions").mkdir(parents=True)
    (vault / "Sessions" / f"{today}-agentos-flowise-key-recovery.md").write_text(
        "---\ndate: x\n---\n# Session\n\n## Summary\n"
        "Recovered the Flowise API key from qa trajectories after the "
        "Sev-1; re-injected into .env and live-tested.\n",
        encoding="utf-8",
    )
    (vault / "Sessions" / f"{old}-agentos-ancient-flowise-note.md").write_text(
        "# Old\nFlowise ancient history that should be outside the window.\n",
        encoding="utf-8",
    )
    (vault / "Topics").mkdir(parents=True)
    (vault / "Topics" / "Flowise.md").write_text(
        "# Flowise\nSelf-hosted LLM flow tool used by qa for test pipelines.\n",
        encoding="utf-8",
    )
    (vault / "Company").mkdir(parents=True)
    (vault / "Company" / "FACTS.md").write_text(
        "# FACTS\n- MRR: ~$6K CAD <!-- conf:estimate | 2026-06-05 main -->\n"
        "- Flowise owner: qa/Kestrel <!-- conf:confirmed | 2026-06-10 main -->\n",
        encoding="utf-8",
    )
    (vault / "Company" / "DECISIONS.md").write_text(
        "# Decisions\n\n## 2026-06-05 Revenue is the core metric\n"
        "Every digest opens with revenue movement first.\n",
        encoding="utf-8",
    )

    # --- agent memory fixture -------------------------------------------
    agent_mem = memory_recall.AGENTS_DIR / "test-recall-agent" / "memory"
    agent_mem.mkdir(parents=True, exist_ok=True)
    (agent_mem / f"{today}.md").write_text(
        "## Flowise key recovered\n- found key in qa trajectories, "
        "live-tested against the Flowise endpoint, Sev-1 closed.\n",
        encoding="utf-8",
    )

    os.environ["VAULT_PATH"] = str(vault)

    # 1. Relevant message → recall block with the right notes.
    fresh_cache()
    out = memory_recall.build(
        "test-recall-agent",
        "what happened with the Flowise key recovery for qa?",
        {},
    )
    check("match returns block", out is not None)
    check("block labelled", out is not None and out.startswith("[memory recall"))
    check("session note surfaced", out is not None and "flowise-key-recovery" in out)
    check("old session outside window excluded",
          out is not None and "ancient" not in out)

    # 2. Bare ack → None (short-message + stopword floors).
    fresh_cache()
    check("short ack skipped",
          memory_recall.build("test-recall-agent", "Continue", {}) is None)
    check("ok skipped",
          memory_recall.build("test-recall-agent", "ok go ahead please", {}) is None)

    # 3. Unrelated message with enough tokens → None (noise floor).
    fresh_cache()
    out3 = memory_recall.build(
        "test-recall-agent",
        "zebra quantum harpsichord volcano trampoline xylophone",
        {},
    )
    check("unrelated message yields nothing", out3 is None, repr(out3))

    # 4. Config disable respected.
    fresh_cache()
    check("enabled:false respected",
          memory_recall.build("test-recall-agent",
                              "what happened with the Flowise key recovery?",
                              {"enabled": False}) is None)

    # 5. Budget: max_chars honored.
    fresh_cache()
    out5 = memory_recall.build(
        "test-recall-agent",
        "what happened with the Flowise key recovery for qa?",
        {"max_chars": 300},
    )
    check("max_chars cap holds", out5 is None or len(out5) < 480, str(len(out5 or "")))

    # 6. Fail-open: broken VAULT_PATH → None or valid block, never raises.
    fresh_cache()
    os.environ["VAULT_PATH"] = "/nonexistent/nope"
    try:
        out6 = memory_recall.build(
            "test-recall-agent",
            "what happened with the Flowise key recovery for qa?",
            {},
        )
        # dailies still exist, so a block from memory/ alone is fine
        check("broken vault fails open", True)
        check("dailies still recall without vault",
              out6 is None or "memory/" in out6)
    except Exception as e:  # noqa: BLE001
        check("broken vault fails open", False, str(e))

    # 7. Fail-open: garbage cfg types → never raises.
    fresh_cache()
    os.environ["VAULT_PATH"] = str(vault)
    try:
        memory_recall.build("test-recall-agent", "flowise key recovery status",
                            {"k": "banana", "max_chars": None})
        check("garbage cfg fails open", True)
    except Exception as e:  # noqa: BLE001
        check("garbage cfg fails open", False, str(e))

    # 8. Unknown agent (no memory dir) → still works off vault alone.
    fresh_cache()
    out8 = memory_recall.build("no-such-agent",
                               "flowise key recovery for qa status?", {})
    check("unknown agent ok", out8 is None or "Flowise" in out8 or "flowise" in out8)

    # cleanup the test agent dir so it never pollutes real agents/
    import shutil
    shutil.rmtree(memory_recall.AGENTS_DIR / "test-recall-agent",
                  ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
