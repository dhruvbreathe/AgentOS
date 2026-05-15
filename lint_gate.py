"""Post-write lint dispatch for the PostToolUse hook.

Called when an agent writes/edits a file. Detects file type, runs the
relevant linter if available, and returns a (status, summary) tuple.
Non-blocking: a missing linter or a soft warning never blocks the write
that just happened — this hook is observational, not a gate.

Lint scope (extend as new languages need it):
  - `.py`      → built-in `ast.parse()` (always available)
  - `.md`      → `markdownlint-cli2` if installed, else skipped
  - `.swift`   → `swiftlint lint --quiet --path` if installed, else skipped
  - `.json`    → built-in `json.loads()` (always available)
  - `.yaml`    → `yaml.safe_load()` if PyYAML available (already a dep)

Hooks call `lint_file(path)` after a Write or Edit completes. The return
value is a `LintResult` — `ok` means clean, `warn` means soft issues
found (logged + optionally surfaced to channel), `error` means hard
syntax breakage (logged + always surfaced).
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


LintStatus = Literal["ok", "warn", "error", "skipped"]


@dataclass
class LintResult:
    status: LintStatus
    linter: str
    message: str = ""

    @property
    def is_problem(self) -> bool:
        return self.status in ("warn", "error")


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _lint_python(path: Path) -> LintResult:
    try:
        text = path.read_text()
    except Exception as e:
        return LintResult("skipped", "ast", f"could not read file: {e}")
    try:
        ast.parse(text, filename=str(path))
        return LintResult("ok", "ast")
    except SyntaxError as e:
        return LintResult(
            "error",
            "ast",
            f"SyntaxError at {path.name}:{e.lineno}: {e.msg}",
        )


def _lint_json(path: Path) -> LintResult:
    try:
        json.loads(path.read_text())
        return LintResult("ok", "json")
    except json.JSONDecodeError as e:
        return LintResult(
            "error",
            "json",
            f"JSONDecodeError at {path.name}:{e.lineno}:{e.colno}: {e.msg}",
        )
    except Exception as e:
        return LintResult("skipped", "json", f"could not read file: {e}")


def _lint_yaml(path: Path) -> LintResult:
    try:
        import yaml  # type: ignore
    except ImportError:
        return LintResult("skipped", "yaml", "PyYAML not installed")
    try:
        yaml.safe_load(path.read_text())
        return LintResult("ok", "yaml")
    except yaml.YAMLError as e:
        return LintResult("error", "yaml", f"YAMLError in {path.name}: {e}")
    except Exception as e:
        return LintResult("skipped", "yaml", f"could not read file: {e}")


def _lint_markdown(path: Path) -> LintResult:
    if not _which("markdownlint-cli2"):
        # Linter not installed — fail open, observational only.
        return LintResult("skipped", "markdownlint-cli2", "not installed")
    try:
        r = subprocess.run(
            ["markdownlint-cli2", str(path)],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return LintResult("skipped", "markdownlint-cli2", "timed out")
    except Exception as e:
        return LintResult("skipped", "markdownlint-cli2", f"runner error: {e}")
    if r.returncode == 0:
        return LintResult("ok", "markdownlint-cli2")
    # markdownlint-cli2 exits non-zero with findings on stdout/stderr.
    msg = (r.stdout + r.stderr).strip()
    return LintResult("warn", "markdownlint-cli2", msg[:600])


def _lint_swift(path: Path) -> LintResult:
    if not _which("swiftlint"):
        return LintResult("skipped", "swiftlint", "not installed")
    try:
        r = subprocess.run(
            ["swiftlint", "lint", "--quiet", "--path", str(path)],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return LintResult("skipped", "swiftlint", "timed out")
    except Exception as e:
        return LintResult("skipped", "swiftlint", f"runner error: {e}")
    msg = (r.stdout + r.stderr).strip()
    if r.returncode == 0 and not msg:
        return LintResult("ok", "swiftlint")
    return LintResult("warn", "swiftlint", msg[:600])


_DISPATCH = {
    ".py": _lint_python,
    ".json": _lint_json,
    ".yaml": _lint_yaml,
    ".yml": _lint_yaml,
    ".md": _lint_markdown,
    ".markdown": _lint_markdown,
    ".swift": _lint_swift,
}


def lint_file(path: Path) -> LintResult:
    """Lint a file based on its extension. Returns a LintResult.

    Never raises — any internal failure is converted to a `skipped` result.
    Caller is the PostToolUse hook; it decides what to do with warnings.
    """
    try:
        if not path.is_file():
            return LintResult("skipped", "none", "not a file")
        ext = path.suffix.lower()
        handler = _DISPATCH.get(ext)
        if handler is None:
            return LintResult("skipped", "none", f"no linter for {ext}")
        return handler(path)
    except Exception as e:
        return LintResult("skipped", "dispatch", f"unhandled error: {e}")
