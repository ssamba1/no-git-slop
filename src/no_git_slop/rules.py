"""Rules that decide whether a new file is slop.

Every check works on a repo-relative POSIX path and returns a Verdict.
Only *new* files are judged: editing an existing file is always allowed.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

CONFIG_FILE = ".no-git-slop.json"

DEFAULT_CONFIG = {
    # Globs that are always allowed, even if a rule would block them.
    "allow": [],
    # Extra globs that are always blocked.
    "deny": [],
    # Git-ignored folder where throwaway scripts and notes belong.
    "scratchDir": ".scratch",
    # Block `git add -A`, `git add .` and `git add --all` from Claude.
    "blockBulkAdd": True,
    # "conventional" enforces Conventional Commits; "off" disables message checks.
    "commitStyle": "conventional",
    "maxSubjectLength": 72,
}

# Markdown files that are expected at the repo root.
ROOT_DOC_ALLOW = {
    "readme.md", "changelog.md", "contributing.md", "code_of_conduct.md",
    "security.md", "support.md", "license.md", "agents.md", "claude.md",
    "governance.md", "citation.md", "authors.md", "maintainers.md",
}

# Words that mark an agent's status report rather than real documentation.
REPORT_WORDS = (
    "summary", "implementation", "notes", "progress", "status", "report",
    "analysis", "changes_made", "fixes", "fix", "refactor", "refactoring",
    "completion", "complete", "final", "session", "worklog", "work_log",
    "plan", "todo", "results", "overview", "update", "updates", "verification",
    "test_results", "debug", "investigation", "solution", "done",
)
_REPORT_RE = re.compile(
    r"(^|[_\-.])(" + "|".join(REPORT_WORDS) + r")([_\-.]|$)", re.IGNORECASE
)

# Suffixes that always mean "a copy of another file".
_STRONG_COPY_RE = re.compile(
    r"[_\-. ](backup|bak|copy|orig|fixed|final|tmp|temp)(\d*)$", re.IGNORECASE
)
# Suffixes that mean a copy only when the unsuffixed sibling already exists.
_WEAK_COPY_RE = re.compile(
    r"[_\-. ](v\d+|new|old|updated|improved|refactored|alt|working)$", re.IGNORECASE
)
_COPY_DIR_RE = re.compile(
    r"^(backups?|old|archive[sd]?|bak|copy|copies|attempts?|\w*[_\-]?backup"
    r"|(error|bug|nuclear|modular|quick)?[_\-]?fix(es|ed)?)$", re.IGNORECASE
)
_BACKUP_EXT = {".bak", ".orig", ".old", ".tmp", ".swp", ".rej"}
_COPY_OF_RE = re.compile(r"(^copy of |\s\(\d+\)$| - copy$)", re.IGNORECASE)

# Throwaway scripts: debug_x.py, scratch.js, tmp_check.sh ...
_SCRIPT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".rb", ".go", ".php"}
_SCRATCH_ANYWHERE_RE = re.compile(
    r"^(debug|scratch|tmp|temp|try|quick|playground|sandbox)([_\-]|$)", re.IGNORECASE
)
# test_*.py etc. are fine inside a tests folder, slop at the repo root.
_SCRATCH_ROOT_RE = re.compile(
    r"^(test|check|verify|demo|example|fix|simple)([_\-]|$)", re.IGNORECASE
)

_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "nul", "npm-debug.log"}
_JUNK_EXT = {".log", ".pyc", ".pyo"}


@dataclass
class Verdict:
    decision: str  # "ok", "ask" or "deny"
    reason: str = ""
    path: str = ""

    @property
    def blocked(self) -> bool:
        return self.decision == "deny"


@dataclass
class Config:
    allow: list = field(default_factory=list)
    deny: list = field(default_factory=list)
    scratchDir: str = ".scratch"
    blockBulkAdd: bool = True
    commitStyle: str = "conventional"
    maxSubjectLength: int = 72


def load_config(root: Path) -> Config:
    data = dict(DEFAULT_CONFIG)
    path = root / CONFIG_FILE
    if path.is_file():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                data.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
        except (OSError, ValueError):
            pass  # A broken config must never stop work; fall back to defaults.
    return Config(**data)


def _matches(path: str, globs) -> bool:
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(PurePosixPath(path).name, g)
               for g in globs)


def _in_tests_dir(parts) -> bool:
    return any(p.lower() in {"tests", "test", "__tests__", "spec", "specs", "testing"}
               for p in parts[:-1])


def _sibling_exists(root: Path, rel: PurePosixPath, base_stem: str) -> bool:
    folder = root / rel.parent
    if not folder.is_dir():
        return False
    for entry in folder.iterdir():
        if entry.name == rel.name:
            continue
        if entry.stem.lower() == base_stem.lower():
            return True
    return False


def judge_new_file(rel_path: str, root: Path, config: Config) -> Verdict:
    """Decide whether creating `rel_path` (repo-relative) is acceptable."""
    rel = PurePosixPath(rel_path.replace(os.sep, "/"))
    parts = rel.parts
    name = rel.name
    lower = name.lower()
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    scratch = config.scratchDir.strip("/")

    if not parts:
        return Verdict("ok", path=rel_path)
    if scratch and parts[0] == scratch:
        return Verdict("ok", path=rel_path)
    if _matches(rel_path, config.allow):
        return Verdict("ok", path=rel_path)
    if _matches(rel_path, config.deny):
        return Verdict("deny", f"`{rel_path}` matches a deny pattern in {CONFIG_FILE}.", rel_path)

    # 1. OS and tool junk.
    if lower in _JUNK_NAMES or ext in _JUNK_EXT:
        return Verdict("deny", f"`{rel_path}` is a junk or generated file. Don't create it; "
                               "add it to .gitignore if a tool produces it.", rel_path)

    # 2. Backup and copy files, including copies parked in folders like NuclearFix/.
    for folder in parts[:-1]:
        if _COPY_DIR_RE.search(folder):
            return Verdict("deny", f"`{rel_path}` is inside `{folder}/`, which looks like a "
                                   "backup or fix-attempt folder. Edit the original file in "
                                   "place instead.", rel_path)
    if ext in _BACKUP_EXT or _COPY_OF_RE.search(stem) or _STRONG_COPY_RE.search(stem):
        return Verdict("deny", f"`{rel_path}` looks like a backup or copy. Edit the original "
                               "file in place; git already keeps the history.", rel_path)
    weak = _WEAK_COPY_RE.search(stem)
    if weak and _sibling_exists(root, rel, stem[: weak.start()]):
        return Verdict("deny", f"`{rel_path}` duplicates an existing file "
                               f"(`{stem[: weak.start()]}{ext}`). Edit that file instead of "
                               "creating a new version.", rel_path)

    # 3. Status-report markdown (IMPLEMENTATION_SUMMARY.md, FIX_NOTES.md ...).
    if ext in {".md", ".txt", ".rst"} and lower not in ROOT_DOC_ALLOW:
        shouty = stem.upper() == stem and any(c.isalpha() for c in stem)
        if _REPORT_RE.search(stem) and (shouty or len(parts) == 1):
            return Verdict("deny", f"`{rel_path}` looks like a status report. Put the summary "
                                   "in your reply to the user, not in a file. If it is real "
                                   "documentation, update README.md or a page in docs/.", rel_path)
        if len(parts) == 1:
            return Verdict("ask", f"New top-level document `{rel_path}`. Docs usually belong in "
                                  "README.md or docs/.", rel_path)

    # 4. Throwaway scripts.
    if ext in _SCRIPT_EXT:
        if _SCRATCH_ANYWHERE_RE.match(stem) and not _in_tests_dir(parts):
            return Verdict("deny", f"`{rel_path}` looks like a throwaway script. Put it in "
                                   f"`{scratch}/{name}` (git-ignored) or write a real test.", rel_path)
        if len(parts) == 1 and _SCRATCH_ROOT_RE.match(stem):
            return Verdict("deny", f"`{rel_path}` is a loose script at the repo root. Put tests "
                                   f"in the tests folder and one-off checks in `{scratch}/`.", rel_path)

    return Verdict("ok", path=rel_path)


# ---------------------------------------------------------------- commits

CONVENTIONAL_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test",
                      "build", "ci", "chore", "revert")
_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>" + "|".join(CONVENTIONAL_TYPES) + r")(\([^)\s]+\))?!?: (?P<desc>\S.*)$"
)
_VAGUE = {"update", "updates", "updated", "change", "changes", "fix", "fixes", "fixed",
          "wip", "misc", "stuff", "minor", "minor changes", "tweaks", "cleanup",
          "improvements", "various fixes", "work in progress"}


def check_commit_message(message: str, config: Config) -> list:
    """Return a list of problems with a commit message (empty means fine)."""
    if config.commitStyle == "off":
        return []
    lines = [ln for ln in message.splitlines() if not ln.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["The commit message is empty."]
    subject = lines[0].rstrip()
    if subject.startswith(("Merge ", "Revert \"", "fixup! ", "squash! ", "amend! ")):
        return []
    problems = []
    match = _CONVENTIONAL_RE.match(subject)
    if not match:
        problems.append(
            f"Subject `{subject}` is not a Conventional Commit. Use `type(scope): description` "
            f"with a type from: {', '.join(CONVENTIONAL_TYPES)}."
        )
    elif match.group("desc").strip().rstrip(".").lower() in _VAGUE:
        problems.append(f"Subject `{subject}` is too vague. Say what changed and why.")
    if len(subject) > config.maxSubjectLength:
        problems.append(f"Subject is {len(subject)} characters; keep it under "
                        f"{config.maxSubjectLength}.")
    if len(lines) > 1 and lines[1].strip():
        problems.append("Leave a blank line between the subject and the body.")
    return problems
