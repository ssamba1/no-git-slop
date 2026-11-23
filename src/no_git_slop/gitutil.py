"""Small wrappers around the git command line."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


def _git(args: List[str], cwd: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def repo_root(start: Path) -> Optional[Path]:
    out = _git(["rev-parse", "--show-toplevel"], start)
    return Path(out.strip()) if out else None


def git_dir(root: Path) -> Optional[Path]:
    out = _git(["rev-parse", "--absolute-git-dir"], root)
    return Path(out.strip()) if out else None


def untracked_files(root: Path) -> List[str]:
    """Untracked files that are not git-ignored, repo-relative."""
    out = _git(["ls-files", "--others", "--exclude-standard", "-z"], root)
    return [p for p in (out or "").split("\0") if p]


def staged_new_files(root: Path) -> List[str]:
    out = _git(["diff", "--cached", "--name-only", "--diff-filter=A", "-z"], root)
    return [p for p in (out or "").split("\0") if p]


def files_added_in_range(root: Path, rev_range: str) -> List[str]:
    out = _git(["diff", "--name-only", "--diff-filter=A", "-z", rev_range], root)
    return [p for p in (out or "").split("\0") if p]


def commit_messages(root: Path, rev_range: str) -> List[tuple]:
    out = _git(["log", "--no-merges", "--format=%H%x00%B%x01", rev_range], root)
    result = []
    for chunk in (out or "").split("\x01"):
        chunk = chunk.strip("\n")
        if "\0" in chunk:
            sha, body = chunk.split("\0", 1)
            result.append((sha[:10], body))
    return result
