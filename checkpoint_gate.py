"""Checkpoint + rollback for AgentOS.

Snapshots an agent's working state before risky edits so /rollback can
restore later. Two storage modes, auto-detected from the agent's cwd:

  - **git-stash mode** (cwd has `.git`): `git stash push -u -m "<label>"`
    captures working tree + untracked. Restore = `git stash apply` of the
    matching stash. Used by code-writing agents (Aria, Ravi, Indra, Atlas).
  - **file-snapshot mode** (no `.git` in cwd): copy each soon-to-be-edited
    file into `agents/<name>/checkpoints/<id>/`. Restore = copy back.
    Used by vault-writing agents (main, Tempo, Deepali).

Both modes share an `agents/<name>/checkpoints/index.jsonl` ledger so
listing and lookup are uniform. Stash refs slide (`stash@{0}` →
`stash@{1}` after each new stash) so we always resolve git mode by the
label string, never the slot index.

All operations fail-open from the PreToolUse hook's perspective — a
checkpoint failure logs a warning and lets the write proceed. The
agent's safety bet is "rollback if you regret it", not "block if you
might regret it".

CLI usage (driven by `/checkpoint` and `/rollback` skills):

    python -m checkpoint_gate create <agent> <cwd> [file1 file2 ...] [--message "..."]
    python -m checkpoint_gate list <agent> [--limit 10]
    python -m checkpoint_gate peek <agent> <id>
    python -m checkpoint_gate restore <agent> <id> [--force]
    python -m checkpoint_gate prune <agent> [--keep-count 20] [--keep-days 7]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal


# AgentOS root — checkpoints live under agents/<name>/checkpoints/
AGENTOS_ROOT = Path(__file__).resolve().parent

CheckpointMode = Literal["git-stash", "file-snapshot"]
RestoreStatus = Literal["ok", "error", "skipped"]

# Files we never checkpoint — too noisy or pointless.
SKIP_GLOBS = {
    "logs",
    "__pycache__",
    ".venv",
    "node_modules",
    ".git",
    ".obsidian",
}
# Same idea but path substring match (for nested workspaces).
SKIP_SUBSTRINGS = (
    "/logs/",
    "/__pycache__/",
    "/.venv/",
    "/node_modules/",
    "/.git/",
    "/.obsidian/",
)
# Filename patterns to skip outright (daily journal is noisy + recoverable from trajectories).
SKIP_FILENAME_PREFIXES = ("memory/",)  # we'd snapshot the journal every Stop hook otherwise


# How long after the last checkpoint before a new edit triggers a fresh one.
# Default 60s: fast-fire edits in the same turn bundle into one snapshot.
DEFAULT_DEBOUNCE_SECONDS = 60


@dataclass
class CheckpointMeta:
    id: str
    agent: str
    mode: CheckpointMode
    cwd: str
    files_touched: list[str] = field(default_factory=list)
    stash_label: str | None = None  # git-stash mode only
    snapshot_dir: str | None = None  # file-snapshot mode only
    tool: str = ""
    message: str = ""
    created_at: str = ""

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


@dataclass
class RestoreResult:
    status: RestoreStatus
    mode: CheckpointMode | None = None
    pre_rollback_checkpoint_id: str | None = None
    files_restored: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _new_id() -> str:
    ts = _now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)  # 4 hex chars, plenty of entropy at this scale
    return f"ckpt-{ts}-{suffix}"


def _checkpoint_root(agent: str) -> Path:
    return AGENTOS_ROOT / "agents" / agent / "checkpoints"


def _index_path(agent: str) -> Path:
    return _checkpoint_root(agent) / "index.jsonl"


def _is_git_repo(cwd: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _detect_mode(cwd: Path) -> CheckpointMode:
    return "git-stash" if _is_git_repo(cwd) else "file-snapshot"


def _should_skip(file_path: Path) -> bool:
    s = str(file_path)
    if any(part in SKIP_GLOBS for part in file_path.parts):
        return True
    if any(sub in s for sub in SKIP_SUBSTRINGS):
        return True
    # The daily memory journal is special — high write frequency, recoverable
    # from trajectory logs. Avoid checkpointing it.
    if "memory" in file_path.parts and file_path.suffix == ".md":
        # Match YYYY-MM-DD.md or YYYY-MM-DD-<slug>.md
        stem = file_path.stem
        if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
            return True
    return False


def _encode_path(rel_path: Path) -> str:
    """Encode `a/b/c.md` → `a__b__c.md` for flat snapshot storage."""
    return "__".join(rel_path.parts)


def _decode_path(encoded: str) -> Path:
    return Path(*encoded.split("__"))


def _load_index(agent: str) -> list[CheckpointMeta]:
    p = _index_path(agent)
    if not p.exists():
        return []
    out = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out.append(CheckpointMeta(**d))
            except Exception:
                # Skip malformed lines rather than crashing — index is recoverable.
                continue
    return out


def _append_index(agent: str, meta: CheckpointMeta) -> None:
    p = _index_path(agent)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(meta.to_jsonl() + "\n")


def _rewrite_index(agent: str, entries: list[CheckpointMeta]) -> None:
    """Atomic rewrite — used by prune. Write tmp then rename."""
    p = _index_path(agent)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for m in entries:
            f.write(m.to_jsonl() + "\n")
    tmp.replace(p)


def _find_recent_for_bundling(
    agent: str, cwd: Path, debounce_seconds: int
) -> CheckpointMeta | None:
    """Return the most recent checkpoint for this agent+cwd within the debounce window.

    Bundling only makes sense within the same working directory. If the most
    recent checkpoint was in a different cwd (rare, but possible when an
    agent flips between repos), we create a fresh one rather than mixing.
    """
    entries = _load_index(agent)
    if not entries:
        return None
    cwd_s = str(cwd)
    # Walk newest-first so we stop at the first cwd-matching entry.
    for m in reversed(entries):
        if m.cwd != cwd_s:
            continue
        try:
            created = datetime.fromisoformat(m.created_at)
        except Exception:
            return None
        if (_now() - created).total_seconds() <= debounce_seconds:
            return m
        return None  # most recent for this cwd is too old; no fallback
    return None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_checkpoint(
    agent: str,
    cwd: Path,
    files: list[Path],
    *,
    tool: str = "",
    message: str = "",
    debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
) -> CheckpointMeta | None:
    """Create a checkpoint covering `files` (about to be edited).

    Returns the checkpoint metadata, or `None` if nothing was checkpointed
    (e.g. all files filtered, or no files at all). Never raises — internal
    failures are logged via the return message of a "skipped" meta.

    If a checkpoint exists within `debounce_seconds`, this call **bundles**
    the new files into that existing checkpoint instead of creating a new
    one. Returns the bundled meta.
    """
    cwd = cwd.resolve()
    keep = [f for f in files if f and not _should_skip(f) and f.exists()]
    if not keep:
        return None

    # Try to bundle into a recent checkpoint for this same cwd.
    existing = _find_recent_for_bundling(agent, cwd, debounce_seconds)
    if existing is not None:
        return _bundle_into_existing(agent, existing, cwd, keep)

    # Fresh checkpoint.
    mode = _detect_mode(cwd)
    cid = _new_id()
    created_at = _now().isoformat(timespec="seconds")

    if mode == "git-stash":
        meta = _create_git_checkpoint(agent, cid, cwd, keep, tool, message, created_at)
    else:
        meta = _create_file_checkpoint(agent, cid, cwd, keep, tool, message, created_at)

    if meta is not None:
        _append_index(agent, meta)
    return meta


def _create_git_checkpoint(
    agent: str,
    cid: str,
    cwd: Path,
    files: list[Path],
    tool: str,
    message: str,
    created_at: str,
) -> CheckpointMeta | None:
    label = f"agentos-checkpoint {cid}"

    # If working tree is clean, there's nothing for `git stash` to stash —
    # the file states are already pinned by HEAD. We still record a
    # checkpoint pointing to the current HEAD commit for restore.
    try:
        status = subprocess.run(
            ["git", "-C", str(cwd), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        if status.returncode != 0:
            return None
        is_dirty = bool(status.stdout.strip())
    except Exception:
        return None

    if is_dirty:
        try:
            r = subprocess.run(
                ["git", "-C", str(cwd), "stash", "push", "-u", "-m", label],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode != 0:
                # Stash failed (rare — usually permission / detached HEAD).
                # Record nothing rather than a half-checkpoint.
                return None
            # `git stash push` MOVES changes off the working tree. We need
            # them BACK so the edit can proceed against the right state.
            # `git stash apply stash@{0}` re-applies without dropping the
            # stash entry — the snapshot is preserved.
            apply = subprocess.run(
                ["git", "-C", str(cwd), "stash", "apply", "--quiet", "stash@{0}"],
                capture_output=True, text=True, timeout=20,
            )
            if apply.returncode != 0:
                # If re-apply fails we're in a weird half-state — bail.
                # Drop the stash to clean up.
                subprocess.run(
                    ["git", "-C", str(cwd), "stash", "drop", "stash@{0}"],
                    capture_output=True, text=True, timeout=10,
                )
                return None
        except Exception:
            return None
    # else: clean working tree, no stash needed — HEAD pins the state.

    return CheckpointMeta(
        id=cid,
        agent=agent,
        mode="git-stash",
        cwd=str(cwd),
        files_touched=[str(_relative_or_abs(f, cwd)) for f in files],
        stash_label=label if is_dirty else None,
        tool=tool,
        message=message,
        created_at=created_at,
    )


def _create_file_checkpoint(
    agent: str,
    cid: str,
    cwd: Path,
    files: list[Path],
    tool: str,
    message: str,
    created_at: str,
) -> CheckpointMeta | None:
    snapshot_dir = _checkpoint_root(agent) / cid
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    touched: list[str] = []
    for f in files:
        try:
            rel = f.resolve().relative_to(cwd)
            dst = snapshot_dir / _encode_path(rel)
        except ValueError:
            # File is outside cwd — store under __abspath/<encoded-full-path>
            abs_encoded = _encode_path(Path(*f.resolve().parts[1:]))  # drop leading "/"
            dst = snapshot_dir / "__abspath" / abs_encoded
            dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(f, dst)
            touched.append(str(_relative_or_abs(f, cwd)))
        except Exception:
            # One file failed — skip it, keep the rest.
            continue

    if not touched:
        # Nothing actually got snapshotted — clean up empty dir.
        try:
            shutil.rmtree(snapshot_dir)
        except Exception:
            pass
        return None

    return CheckpointMeta(
        id=cid,
        agent=agent,
        mode="file-snapshot",
        cwd=str(cwd),
        files_touched=touched,
        snapshot_dir=str(snapshot_dir),
        tool=tool,
        message=message,
        created_at=created_at,
    )


def _bundle_into_existing(
    agent: str,
    existing: CheckpointMeta,
    cwd: Path,
    files: list[Path],
) -> CheckpointMeta:
    """Add new files to a recent checkpoint instead of creating a fresh one.

    Git mode: nothing to do — the stash already captured the state at
    creation time. We just record the new file paths.

    File-snapshot mode: copy any files we haven't already snapshotted into
    the existing checkpoint dir. Skip files already there (first-snapshot
    wins — that's the state we want to restore to).
    """
    already = set(existing.files_touched)
    added: list[str] = []

    if existing.mode == "file-snapshot" and existing.snapshot_dir:
        snapshot_dir = Path(existing.snapshot_dir)
        for f in files:
            rel_str = str(_relative_or_abs(f, cwd))
            if rel_str in already:
                continue
            try:
                rel = f.resolve().relative_to(cwd)
                dst = snapshot_dir / _encode_path(rel)
            except ValueError:
                abs_encoded = _encode_path(Path(*f.resolve().parts[1:]))
                dst = snapshot_dir / "__abspath" / abs_encoded
                dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, dst)
                added.append(rel_str)
            except Exception:
                continue
    else:
        # Git mode — stash already captured state. Just track the new paths.
        for f in files:
            rel_str = str(_relative_or_abs(f, cwd))
            if rel_str not in already:
                added.append(rel_str)

    if not added:
        return existing

    existing.files_touched.extend(added)
    # Rewrite the index with the updated entry.
    entries = _load_index(agent)
    for i, m in enumerate(entries):
        if m.id == existing.id:
            entries[i] = existing
            break
    _rewrite_index(agent, entries)
    return existing


def _relative_or_abs(path: Path, cwd: Path) -> Path:
    try:
        return path.resolve().relative_to(cwd)
    except ValueError:
        return path.resolve()


# ---------------------------------------------------------------------------
# List / peek / restore / prune
# ---------------------------------------------------------------------------


def list_checkpoints(agent: str, limit: int = 10) -> list[CheckpointMeta]:
    entries = _load_index(agent)
    return list(reversed(entries))[:limit]


def peek_checkpoint(agent: str, cid: str) -> dict:
    """Return a structured diff summary between current state and the checkpoint.

    Format:
      {"id": ..., "mode": ..., "files": [{"path": ..., "diff_lines": int, "status": "modified|deleted|matches"}], ...}
    """
    meta = _find_meta(agent, cid)
    if not meta:
        return {"error": f"checkpoint not found: {cid}"}

    cwd = Path(meta.cwd)
    summary: dict = {
        "id": meta.id,
        "mode": meta.mode,
        "agent": meta.agent,
        "created_at": meta.created_at,
        "message": meta.message,
        "files": [],
    }

    if meta.mode == "git-stash":
        if meta.stash_label:
            ref = _resolve_stash_slot(cwd, meta.stash_label)
            if not ref:
                summary["error"] = f"stash not found for label '{meta.stash_label}' — may have been dropped"
                return summary
            try:
                r = subprocess.run(
                    ["git", "-C", str(cwd), "stash", "show", "--name-status", ref],
                    capture_output=True, text=True, timeout=10,
                )
                summary["files"] = [
                    {"line": l} for l in r.stdout.strip().splitlines()
                ]
            except Exception as e:
                summary["error"] = str(e)
        else:
            summary["note"] = "clean working tree at checkpoint — restore = HEAD pinned"
        return summary

    # file-snapshot mode
    if not meta.snapshot_dir:
        summary["error"] = "no snapshot_dir recorded"
        return summary
    snapshot_dir = Path(meta.snapshot_dir)
    for f_rel in meta.files_touched:
        f_abs = cwd / f_rel if not Path(f_rel).is_absolute() else Path(f_rel)
        snap_path = snapshot_dir / _encode_path(Path(f_rel))
        if not snap_path.exists():
            summary["files"].append({"path": f_rel, "status": "snapshot-missing"})
            continue
        if not f_abs.exists():
            summary["files"].append({"path": f_rel, "status": "current-deleted"})
            continue
        try:
            snap_text = snap_path.read_text(errors="replace")
            cur_text = f_abs.read_text(errors="replace")
            if snap_text == cur_text:
                summary["files"].append({"path": f_rel, "status": "matches"})
            else:
                # Cheap diff metric — line count delta + change presence.
                snap_lines = snap_text.splitlines()
                cur_lines = cur_text.splitlines()
                summary["files"].append({
                    "path": f_rel,
                    "status": "modified",
                    "line_delta": len(cur_lines) - len(snap_lines),
                })
        except Exception as e:
            summary["files"].append({"path": f_rel, "status": f"error: {e}"})

    return summary


def restore_checkpoint(agent: str, cid: str, *, force: bool = False) -> RestoreResult:
    """Restore the agent's working state to `cid`.

    Always auto-creates a "pre-rollback" checkpoint of the current state
    before restoring, so undo-undo works. Pass `force=True` to skip the
    "you have uncommitted-since-checkpoint changes" safety check.
    """
    meta = _find_meta(agent, cid)
    if not meta:
        return RestoreResult("error", message=f"checkpoint not found: {cid}")

    cwd = Path(meta.cwd)
    if not cwd.exists():
        return RestoreResult(
            "error", mode=meta.mode,
            message=f"checkpoint's cwd no longer exists: {meta.cwd}",
        )

    # Auto-create pre-rollback checkpoint covering the files we're about to overwrite.
    files_to_protect: list[Path] = []
    for rel in meta.files_touched:
        p = cwd / rel if not Path(rel).is_absolute() else Path(rel)
        if p.exists():
            files_to_protect.append(p)
    pre_meta = None
    if files_to_protect:
        pre_meta = create_checkpoint(
            agent,
            cwd,
            files_to_protect,
            tool="rollback-auto",
            message=f"pre-rollback snapshot before restoring {cid}",
            debounce_seconds=0,  # don't bundle — we want a discrete pre-rollback point
        )

    if meta.mode == "git-stash":
        return _restore_git(meta, pre_meta, force)
    return _restore_file(meta, pre_meta)


def _find_meta(agent: str, cid: str) -> CheckpointMeta | None:
    for m in _load_index(agent):
        if m.id == cid:
            return m
    return None


def _resolve_stash_slot(cwd: Path, label: str) -> str | None:
    """Find the current `stash@{N}` ref matching the given label.

    Stash slots slide whenever a new stash is pushed, so we never store
    the slot — we always re-resolve by message string.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "stash", "list"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        # Format: stash@{0}: On branch: <label-message>
        if label in line:
            slot = line.split(":", 1)[0].strip()
            return slot
    return None


def _restore_git(meta: CheckpointMeta, pre_meta: CheckpointMeta | None, force: bool) -> RestoreResult:
    cwd = Path(meta.cwd)
    pre_id = pre_meta.id if pre_meta else None

    # Clean working tree to a known state. The pre-rollback checkpoint we
    # just created has the user's current changes safely stashed (if dirty).
    # So we can stash-reset here without losing anything.
    if pre_meta and pre_meta.stash_label:
        # Working tree was dirty — pre-rollback already stashed it via apply.
        # Reset hard to drop the working copy of those changes.
        try:
            subprocess.run(
                ["git", "-C", str(cwd), "reset", "--hard", "--quiet"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception as e:
            return RestoreResult(
                "error", mode="git-stash", pre_rollback_checkpoint_id=pre_id,
                message=f"failed to reset working tree: {e}",
            )

    if meta.stash_label:
        ref = _resolve_stash_slot(cwd, meta.stash_label)
        if not ref:
            return RestoreResult(
                "error", mode="git-stash", pre_rollback_checkpoint_id=pre_id,
                message=f"stash entry '{meta.stash_label}' not found — dropped or pruned",
            )
        try:
            r = subprocess.run(
                ["git", "-C", str(cwd), "stash", "apply", "--quiet", ref],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode != 0:
                return RestoreResult(
                    "error", mode="git-stash", pre_rollback_checkpoint_id=pre_id,
                    message=f"git stash apply failed: {r.stderr.strip()}",
                )
        except Exception as e:
            return RestoreResult(
                "error", mode="git-stash", pre_rollback_checkpoint_id=pre_id,
                message=f"git stash apply error: {e}",
            )
        return RestoreResult(
            "ok", mode="git-stash",
            pre_rollback_checkpoint_id=pre_id,
            files_restored=len(meta.files_touched),
            message=f"applied {ref} ({meta.stash_label})",
        )
    else:
        # Clean working tree at checkpoint time — nothing to apply, HEAD is the state.
        return RestoreResult(
            "ok", mode="git-stash",
            pre_rollback_checkpoint_id=pre_id,
            files_restored=0,
            message="working tree was clean at checkpoint — HEAD already pinned the state",
        )


def _restore_file(meta: CheckpointMeta, pre_meta: CheckpointMeta | None) -> RestoreResult:
    if not meta.snapshot_dir:
        return RestoreResult("error", mode="file-snapshot", message="no snapshot_dir")
    snapshot_dir = Path(meta.snapshot_dir)
    if not snapshot_dir.exists():
        return RestoreResult(
            "error", mode="file-snapshot",
            pre_rollback_checkpoint_id=pre_meta.id if pre_meta else None,
            message=f"snapshot dir missing: {snapshot_dir}",
        )

    cwd = Path(meta.cwd)
    restored = 0
    errors: list[str] = []

    for rel in meta.files_touched:
        p = cwd / rel if not Path(rel).is_absolute() else Path(rel)
        snap = snapshot_dir / _encode_path(Path(rel))
        if not snap.exists():
            # Could be __abspath path
            abs_alt = snapshot_dir / "__abspath" / _encode_path(Path(rel).relative_to("/")) if Path(rel).is_absolute() else None
            if abs_alt and abs_alt.exists():
                snap = abs_alt
            else:
                errors.append(f"{rel}: snapshot missing")
                continue
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snap, p)
            restored += 1
        except Exception as e:
            errors.append(f"{rel}: {e}")

    if errors and restored == 0:
        return RestoreResult(
            "error", mode="file-snapshot",
            pre_rollback_checkpoint_id=pre_meta.id if pre_meta else None,
            files_restored=0,
            message="; ".join(errors[:5]),
        )
    msg = f"restored {restored} file(s)"
    if errors:
        msg += f"; {len(errors)} error(s): " + "; ".join(errors[:3])
    return RestoreResult(
        "ok", mode="file-snapshot",
        pre_rollback_checkpoint_id=pre_meta.id if pre_meta else None,
        files_restored=restored,
        message=msg,
    )


def prune_checkpoints(
    agent: str,
    *,
    keep_count: int = 20,
    keep_days: int = 7,
) -> dict:
    """Drop expired checkpoints. Keep the newer of: last N OR last D days.

    Returns a summary dict with counts.
    """
    entries = _load_index(agent)
    if not entries:
        return {"agent": agent, "before": 0, "after": 0, "dropped": 0}

    now = _now()
    cutoff = now - timedelta(days=keep_days)

    # Sort by created_at desc.
    def _parse(m: CheckpointMeta) -> datetime:
        try:
            return datetime.fromisoformat(m.created_at)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    entries.sort(key=_parse, reverse=True)
    keep: list[CheckpointMeta] = []
    drop: list[CheckpointMeta] = []
    for i, m in enumerate(entries):
        if i < keep_count or _parse(m) >= cutoff:
            keep.append(m)
        else:
            drop.append(m)

    # Actually delete dropped checkpoints' storage.
    for m in drop:
        if m.mode == "git-stash" and m.stash_label:
            slot = _resolve_stash_slot(Path(m.cwd), m.stash_label)
            if slot:
                try:
                    subprocess.run(
                        ["git", "-C", m.cwd, "stash", "drop", "--quiet", slot],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception:
                    pass
        elif m.mode == "file-snapshot" and m.snapshot_dir:
            try:
                shutil.rmtree(m.snapshot_dir, ignore_errors=True)
            except Exception:
                pass

    # Rewrite index with kept entries (oldest first for append-only semantics).
    keep.sort(key=_parse)
    _rewrite_index(agent, keep)

    return {
        "agent": agent,
        "before": len(entries),
        "after": len(keep),
        "dropped": len(drop),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    p = argparse.ArgumentParser(prog="checkpoint_gate")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create", help="Create a checkpoint")
    pc.add_argument("agent")
    pc.add_argument("cwd")
    pc.add_argument("files", nargs="*")
    pc.add_argument("--message", default="")
    pc.add_argument("--tool", default="manual")

    pl = sub.add_parser("list", help="List recent checkpoints")
    pl.add_argument("agent")
    pl.add_argument("--limit", type=int, default=10)

    pp = sub.add_parser("peek", help="Show diff between current and checkpoint")
    pp.add_argument("agent")
    pp.add_argument("id")

    pr = sub.add_parser("restore", help="Restore to a checkpoint")
    pr.add_argument("agent")
    pr.add_argument("id")
    pr.add_argument("--force", action="store_true")

    pp2 = sub.add_parser("prune", help="Prune old checkpoints")
    pp2.add_argument("agent")
    pp2.add_argument("--keep-count", type=int, default=20)
    pp2.add_argument("--keep-days", type=int, default=7)

    args = p.parse_args()

    if args.cmd == "create":
        files = [Path(f).resolve() for f in args.files]
        meta = create_checkpoint(
            args.agent, Path(args.cwd).resolve(),
            files, tool=args.tool, message=args.message,
        )
        if meta is None:
            print(json.dumps({"status": "skipped", "reason": "nothing to checkpoint"}))
            return 0
        print(meta.to_jsonl())
        return 0

    if args.cmd == "list":
        items = list_checkpoints(args.agent, limit=args.limit)
        print(json.dumps([asdict(m) for m in items], indent=2))
        return 0

    if args.cmd == "peek":
        print(json.dumps(peek_checkpoint(args.agent, args.id), indent=2))
        return 0

    if args.cmd == "restore":
        r = restore_checkpoint(args.agent, args.id, force=args.force)
        print(json.dumps(asdict(r), indent=2))
        return 0 if r.status == "ok" else 1

    if args.cmd == "prune":
        out = prune_checkpoints(
            args.agent, keep_count=args.keep_count, keep_days=args.keep_days,
        )
        print(json.dumps(out, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli())
