"""Small wrappers around the git command line. Never uses a shell."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Set


def _git(args: List[str], cwd: Path, stdin: Optional[str] = None, ok_codes=(0,)) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", input=stdin, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode in ok_codes else None


def _split0(out: Optional[str]) -> List[str]:
    return [p for p in (out or "").split("\0") if p]


# Each git spawn costs ~50 ms on Windows; a hook process never moves the repo, so remember these per process.
_ROOTS: dict = {}
_GIT_DIRS: dict = {}


def repo_root(start: Path) -> Optional[Path]:
    key = str(Path(start))
    if key not in _ROOTS:
        lines = (_git(["rev-parse", "--show-toplevel", "--absolute-git-dir"], start) or "").splitlines()
        if len(lines) < 2:
            return None
        _ROOTS[key] = Path(lines[0])
        _GIT_DIRS[str(Path(lines[0]))] = Path(lines[1])
    return _ROOTS[key]


def git_dir(root: Path) -> Optional[Path]:
    key = str(Path(root))
    if key not in _GIT_DIRS:
        out = _git(["rev-parse", "--absolute-git-dir"], root)
        if not out:
            return None
        _GIT_DIRS[key] = Path(out.strip())
    return _GIT_DIRS[key]


def untracked_files(root: Path, pathspecs: Iterable[str] = ()) -> List[str]:
    """Untracked, non-ignored files (repo-relative), optionally limited to pathspecs."""
    specs = list(pathspecs)
    args = ["ls-files", "--others", "--exclude-standard", "-z", "--full-name"]
    if specs:
        args += ["--", *specs]
    return _split0(_git(args, root))


def tracked_files(root: Path) -> List[str]:
    return _split0(_git(["ls-files", "-z"], root))


def ignored(root: Path, paths: Iterable[str]) -> Set[str]:
    paths = [p for p in paths if p]
    if not paths:
        return set()
    out = _git(["check-ignore", "-z", "--stdin", "--no-index"], root,
               stdin="\0".join(paths) + "\0", ok_codes=(0, 1))
    if out is None:  # older git without --no-index
        out = _git(["check-ignore", "-z", "--stdin"], root,
                   stdin="\0".join(paths) + "\0", ok_codes=(0, 1))
    return set(_split0(out))


def staged_new_files(root: Path) -> List[str]:
    return _split0(_git(["diff", "--cached", "--name-only", "--diff-filter=A", "-z"], root))


def staged_files(root: Path) -> List[str]:
    return _split0(_git(["diff", "--cached", "--name-only", "-z"], root))


def staged_line_count(root: Path) -> int:
    return _numstat_total(_git(["diff", "--cached", "--numstat"], root))


def worktree_line_count(root: Path) -> int:
    return _numstat_total(_git(["diff", "HEAD", "--numstat"], root))


def _numstat_total(out: Optional[str]) -> int:
    total = 0
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            total += int(parts[0]) + int(parts[1])
    return total


def files_added_in_range(root: Path, rev_range: str) -> List[str]:
    return _split0(_git(["diff", "--name-only", "--diff-filter=A", "-z", rev_range], root))


def files_changed_in_range(root: Path, rev_range: str) -> List[str]:
    return _split0(_git(["diff", "--name-only", "-z", rev_range], root))


def commit_messages(root: Path, rev_range: str) -> List[tuple]:
    out = _git(["log", "--no-merges", "--format=%H%x00%B%x01", rev_range], root)
    result = []
    for chunk in (out or "").split("\x01"):
        chunk = chunk.strip("\n")
        if "\0" in chunk:
            sha, body = chunk.split("\0", 1)
            result.append((sha[:10], body))
    return result


def head_sha(root: Path) -> str:
    out = _git(["rev-parse", "HEAD"], root)
    return out.strip() if out else ""


def head_message(root: Path) -> str:
    return _git(["log", "-1", "--format=%B"], root) or ""


def current_branch(root: Path) -> str:
    out = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
    return out.strip() if out else ""


def default_branch(root: Path) -> str:
    out = _git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
    if out and out.strip():
        return out.strip().split("/", 1)[-1]
    for name in ("main", "master", "trunk", "develop"):
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], root) is not None:
            return name
    return "main"


def unpushed_range(root: Path) -> Optional[str]:
    if _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root):
        return "@{u}..HEAD"
    base = default_branch(root)
    for ref in (f"origin/{base}", base):
        if _git(["rev-parse", "--verify", "--quiet", ref], root) is not None:
            if ref == base and current_branch(root) == base:
                return None
            return f"{ref}..HEAD"
    return None


def add_local_exclude(root: Path, pattern: str) -> bool:
    """Add a pattern to .git/info/exclude (local only, never committed)."""
    gd = git_dir(root)
    if not gd:
        return False
    exclude = gd / "info" / "exclude"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if pattern in text.split():
            return False
        sep = "" if not text or text.endswith("\n") else "\n"
        exclude.write_text(f"{text}{sep}# added by no-git-slop\n{pattern}\n", encoding="utf-8")
        return True
    except OSError:
        return False
