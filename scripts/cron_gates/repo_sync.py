"""Repo-sync gate: `git fetch` + compare local `main` vs `origin/main`.

Local main not behind origin/main -> print nothing (the sync tasks stay
silent when there is nothing new, even with a dirty tree or unpushed
commits). Behind -> print the facts (behind/ahead, current branch, dirty
files, incoming commits) so the agent does what its task says. Fetch
failure -> exit non-zero with git's message so the agent reports it.

Read-only apart from `git fetch` (which only updates remote-tracking refs).
Used through the per-repo wrappers (repo_sync_<name>.py); for a manual run:
    .venv/bin/python scripts/cron_gates/repo_sync.py /path/to/repo
"""
from __future__ import annotations

import os
import sys

from gatelib import fail, run

MAX_LINES = 25


def _git(repo: str, *args: str, env: dict | None = None, timeout: float = 30):
    return run(["git", "-C", repo, *args], timeout=timeout, env=env)


def _must(repo: str, *args: str) -> str:
    p = _git(repo, *args)
    if p.returncode != 0:
        fail(f"git {' '.join(args)} failed in {repo} (exit {p.returncode}):\n"
             f"{(p.stderr or p.stdout).strip()}")
    return p.stdout


def main(repo: str, *, branch: str = "main", remote: str = "origin",
         unset_github_token: bool = False) -> None:
    if not os.path.isdir(os.path.join(repo, ".git")):
        fail(f"not a git repo: {repo}")

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=20")
    if unset_github_token:  # mirrors the task's `unset GITHUB_TOKEN &&`
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)

    try:
        f = _git(repo, "fetch", remote, "--quiet", env=env, timeout=90)
    except Exception as e:  # noqa: BLE001 - timeout etc.: wake the agent
        fail(f"git fetch {remote} in {repo} did not complete: {e}")
    if f.returncode != 0:
        fail(f"git fetch {remote} failed in {repo} (exit {f.returncode}):\n"
             f"{(f.stderr or f.stdout).strip()}")

    upstream = f"{remote}/{branch}"
    ahead, behind = (int(x) for x in
                     _must(repo, "rev-list", "--left-right", "--count",
                           f"{branch}...{upstream}").split())
    if behind == 0:
        return  # nothing new upstream: task says stay silent

    current = _must(repo, "branch", "--show-current").strip() or "(detached HEAD)"
    dirty = [ln for ln in _must(repo, "status", "--porcelain").splitlines() if ln.strip()]
    incoming = _must(repo, "log", "--oneline", "--no-decorate",
                     f"{branch}..{upstream}").splitlines()
    changed = _must(repo, "diff", "--name-only", f"{branch}...{upstream}").splitlines()

    state = "DIVERGED" if ahead else "behind only (fast-forward possible)"
    out = [
        f"repo: {repo}",
        f"fetch {remote}: ok",
        f"{branch} vs {upstream}: behind {behind}, ahead {ahead} -> {state}",
        f"current branch: {current}",
        f"working tree: {'clean' if not dirty else f'DIRTY ({len(dirty)} entries)'}",
    ]
    out += [f"  {ln}" for ln in dirty[:MAX_LINES]]
    if len(dirty) > MAX_LINES:
        out.append(f"  ... and {len(dirty) - MAX_LINES} more")
    out.append(f"incoming commits ({len(incoming)}):")
    out += [f"  {ln}" for ln in incoming[:MAX_LINES]]
    if len(incoming) > MAX_LINES:
        out.append(f"  ... and {len(incoming) - MAX_LINES} more")
    out.append(f"files changed upstream: {len(changed)}"
               + (f" (includes {', '.join(c for c in changed if c.endswith('build.gradle.kts'))})"
                  if any(c.endswith("build.gradle.kts") for c in changed) else ""))
    print("\n".join(out))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        fail("usage: repo_sync.py /path/to/repo", 2)
    main(sys.argv[1])
