"""Decide whether a new file is slop.

`judge_new_file` works on a repo-relative POSIX path and returns a Finding.
Only *new* files are judged; edits to existing files are never slop (but
edits to guardrail files are checked by `protected_finding`).
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path, PurePosixPath
from typing import Optional

from . import gitutil, intent
from .config import Config
from .findings import Finding
from .presets import active_presets, glob_match, is_harness_artifact, plan_preset_for

# Markdown files that are expected at the repo root.
ROOT_DOC_ALLOW = {
    "readme.md", "changelog.md", "contributing.md", "code_of_conduct.md",
    "security.md", "support.md", "license.md", "agents.md", "claude.md", "gemini.md",
    "governance.md", "citation.md", "authors.md", "maintainers.md", "history.md",
    "readme.rst", "readme.txt", "changes.rst", "license.txt", "notice.txt", "notice.md",
    "requirements.txt", "requirements-dev.txt", "constraints.txt", "robots.txt", "llms.txt",
    "cmakelists.txt", "claude.local.md",
}

# Words that mark an agent's status report rather than real documentation.
REPORT_WORDS = (
    "summary", "summaries", "implementation", "notes", "progress", "status", "report",
    "analysis", "changes_made", "fixes", "fix", "refactor", "refactoring", "findings",
    "completion", "complete", "completed", "final", "session", "worklog", "work_log",
    "plan", "todo", "results", "overview", "update", "updates", "verification",
    "test_results", "debug", "investigation", "solution", "done", "recap", "handoff",
    "walkthrough", "changelog_draft", "audit", "review", "explanation",
)
_REPORT_RE = re.compile(r"(^|[_\-. ])(" + "|".join(REPORT_WORDS) + r")([_\-. ]|$)", re.I)

# Suffixes that always mean "a copy of another file" (incl. anthropics/claude-code#11569 list).
_STRONG_COPY_RE = re.compile(
    r"[_\-. ](backup|bak|orig|fixed|final|tmp|temp|broken|wip)(\d*)$|[_. ]copy\d*$", re.I)
# Suffixes that mean a copy only when the unsuffixed sibling already exists.
_WEAK_COPY_RE = re.compile(
    r"[_\-. ](v\d+|new|old|updated|improved|refactored|alt|enhanced|better|rewrite|fix|copy|"
    r"draft|optimized|optimised|working|\d{8}|\d{4}-\d{2}-\d{2}|\d)$", re.I)
_BACKUP_EXT = {".bak", ".orig", ".old", ".tmp", ".swp", ".swo", ".rej", ".backup"}
_COPY_OF_RE = re.compile(r"(^copy of |\s\(\d+\)$| - copy$|\scopy( \d+)?$)", re.I)
_COPY_DIR_RE = re.compile(
    r"^(backups?|old|archive[sd]?|bak|copy|copies|attempts?|\w*[_\-]?backup|"
    r"(error|bug|nuclear|modular|quick|temp|tmp)?[_\-]?fix(es|ed)?|\w*[_\-]old|"
    r"\w+[_\-]copy)$", re.I)

_SCRIPT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".rb", ".go", ".php",
               ".ps1", ".pl", ".r", ".jl"}
_SCRATCH_ANYWHERE_RE = re.compile(
    r"^(debug|scratch|tmp|temp|try|playground|sandbox|throwaway|adhoc|ad_hoc)"
    r"([_\-]|$)", re.I)
_SCRATCH_ROOT_RE = re.compile(
    r"^(test|tests|check|verify|demo|example|fix|simple|quick|reproduce|repro|investigate|"
    r"analyze|analyse|inspect|run_test|manual)([_\-]|$)", re.I)

_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "nul", "npm-debug.log",
               "yarn-error.log", ".coverage", "core"}
_JUNK_EXT = {".log", ".pyc", ".pyo", ".class", ".o"}
_TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "testing", "e2e", "fixtures"}

# Guardrail files: agent edits need confirmation.
GUARD_CONFIG_GLOBS = [
    ".no-git-slop.json", ".claude/settings.json", ".claude/settings.local.json",
    ".pre-commit-config.yaml", ".husky/**", "lefthook.yml", "lefthook.yaml", ".lefthook.yml",
    ".git/hooks/**", ".github/workflows/*slop*", ".claude/hookify.*",
]
GUARD_STATE_GLOBS = [".git/no-git-slop/**", ".git/info/exclude"]


def _matches(rel: str, globs) -> bool:
    name = PurePosixPath(rel).name
    return any(glob_match(rel, g) or (("/" not in g) and glob_match(name, g)) for g in globs)


def _in_tests_dir(parts) -> bool:
    return any(p.lower() in _TEST_DIRS for p in parts[:-1])


def _sibling_exists(root: Path, rel: PurePosixPath, base_stem: str) -> Optional[str]:
    folder = root / rel.parent
    if not base_stem or not folder.is_dir():
        return None
    try:
        for entry in folder.iterdir():
            if entry.name != rel.name and entry.stem.lower() == base_stem.lower():
                return entry.name
    except OSError:
        return None
    return None


def protected_finding(rel: str, config: Config, session=None) -> Optional[Finding]:
    """Writes or edits to guardrail files and no-git-slop state."""
    if _matches(rel, GUARD_STATE_GLOBS):
        if rel == ".git/info/exclude" and intent.user_named(session, rel):
            return None
        return Finding("guard-state", config.mode("guard-state"),
                       f"`{rel}` belongs to no-git-slop's own state. Leave it alone.", rel)
    if _matches(rel, GUARD_CONFIG_GLOBS):
        # "my vscode settings.json" must not unlock .claude/settings.json: require the path.
        if intent.user_named(session, rel, by_name=False):
            return None
        return Finding("guard-config", config.mode("guard-config"),
                       f"`{rel}` controls the repo's guardrails. Ask the user before changing it.",
                       rel)
    return None


def _raw_rule(rel: str, root: Path, config: Config, subagent: bool) -> Optional[Finding]:
    """The first matching rule for a new file, with its rule-level message."""
    pp = PurePosixPath(rel)
    parts = pp.parts
    name = pp.name
    lower = name.lower()
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    scratch = config.scratchDir.strip("/")

    if _matches(rel, config.deny):
        return Finding("deny-pattern", "deny", f"`{rel}` matches a deny pattern in .no-git-slop.json.", rel)

    # 1. OS and tool junk.
    if lower in _JUNK_NAMES or ext in _JUNK_EXT:
        return Finding("junk-file", "deny", f"`{rel}` is a junk or generated file. Don't create "
                       "it; git-ignore it if a tool produces it.", rel)

    # 2. Backup and copy files, including copies parked in folders like NuclearFix/.
    for folder in parts[:-1]:
        is_doc_archive = folder.lower().startswith("archive") and ext in (".md", ".rst", ".txt")
        if _COPY_DIR_RE.search(folder) and folder.lower() not in _TEST_DIRS and not is_doc_archive:
            return Finding("copy-folder", "deny", f"`{rel}` is inside `{folder}/`, which looks "
                           "like a backup or fix-attempt folder. Edit the original file in place.", rel)
    if ext in _BACKUP_EXT or _COPY_OF_RE.search(stem) or _STRONG_COPY_RE.search(stem):
        return Finding("backup-copy", "deny", f"`{rel}` looks like a backup or copy. Edit the "
                       "original file in place; git keeps the history.", rel)
    weak = _WEAK_COPY_RE.search(stem)
    if weak:
        sibling = _sibling_exists(root, pp, stem[: weak.start()])
        if sibling:
            return Finding("version-copy", "deny", f"`{rel}` duplicates `{sibling}`. Edit that "
                           "file instead of creating a new version.", rel,
                           actions=[f"edit {pp.parent / sibling}"])

    # 3. Status-report documents (IMPLEMENTATION_SUMMARY.md, FIX_NOTES.md ...).
    if ext in {".md", ".txt", ".rst", ".markdown", ".adoc"} and lower not in ROOT_DOC_ALLOW:
        shouty = stem.upper() == stem and any(c.isalpha() for c in stem)
        # Real docs that share report words: release notes, a TODO list, mdBook's src/SUMMARY.md.
        legit = stem.lower() in ("release_notes", "release-notes", "todo") or \
            (stem.lower() == "summary" and len(parts) > 1)
        if _REPORT_RE.search(stem) and (shouty or len(parts) == 1) and not legit:
            if subagent:
                reason = (f"`{rel}` is a report file. Do not write report/summary files; "
                          "return the content as text in your final response.")
            else:
                reason = (f"`{rel}` looks like a status report. Put the summary in your reply "
                          "to the user. If it is real documentation, update README.md or a page "
                          "in docs/.")
            return Finding("report-doc", "deny", reason, rel)
        if len(parts) == 1 and stem.lower() not in ("release_notes", "release-notes"):
            return Finding("root-doc", "ask", f"New top-level document `{rel}`. Docs usually "
                           "belong in README.md or docs/.", rel)

    # 4. Throwaway scripts.
    if ext in _SCRIPT_EXT:
        # Bare names like src/debug.ts or src/sandbox.py are real modules; debug_auth.py is not.
        nested_ok = len(parts) > 1 and stem.lower() in ("debug", "sandbox", "playground", "scratch")
        if _SCRATCH_ANYWHERE_RE.match(stem) and not _in_tests_dir(parts) and not nested_ok:
            return Finding("throwaway-script", "deny", f"`{rel}` looks like a throwaway script. "
                           f"Write it to `{scratch}/{name}` (git-ignored) instead, or make it a "
                           "real test.", rel, actions=[f"write {scratch}/{name}"])
        if len(parts) == 1 and _SCRATCH_ROOT_RE.match(stem):
            return Finding("root-script", "deny", f"`{rel}` is a loose script at the repo root. "
                           f"Put tests in the tests folder and one-off checks in `{scratch}/`.",
                           rel, actions=[f"write {scratch}/{name}"])
    return None


def near_duplicate(root: Path, rel: str, content: str, threshold: float):
    """Return (existing_path, ratio) if `content` is nearly identical to a tracked file."""
    if not content or len(content) < 300 or content.count("\n") < 8 or threshold <= 0:
        return None
    ext = os.path.splitext(rel)[1].lower()
    if not ext:
        return None
    size = len(content)
    folder = str(PurePosixPath(rel).parent)
    candidates = [p for p in gitutil.tracked_files(root)
                  if p.lower().endswith(ext) and p != rel]
    candidates.sort(key=lambda p: (str(PurePosixPath(p).parent) != folder, p))
    new_lines = content.splitlines()
    checked = 0
    for cand in candidates:
        try:
            st = (root / cand).stat()
        except OSError:
            continue
        if not (size * 0.5 <= st.st_size <= size * 2) or st.st_size > 300_000:
            continue
        checked += 1
        if checked > 300:
            break
        try:
            old = (root / cand).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        sm = difflib.SequenceMatcher(None, new_lines, old, autojunk=False)
        if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
            continue
        ratio = sm.ratio()
        if ratio >= threshold:
            return cand, ratio
    return None


def judge_new_file(rel: str, root: Path, config: Config, session=None, content: Optional[str] = None,
                   subagent: bool = False, check_ignored: bool = True) -> Finding:
    """Decide whether creating `rel` (repo-relative) is acceptable."""
    rel = intent.norm(rel.replace(os.sep, "/"))
    ok = Finding("ok", "ok", "", rel)
    if not rel:
        return ok
    scratch = config.scratchDir.strip("/")
    if scratch and (rel == scratch or rel.startswith(scratch + "/")):
        return ok
    if is_harness_artifact(root, rel):
        return Finding("ok", "ok", "", rel, note="harness artifact")
    if _matches(rel, config.allow):
        return ok
    preset = plan_preset_for(rel, active_presets(root, config.presets))
    if preset:
        return Finding("ok", "ok", "", rel, note=f"plan file ({preset})")
    if check_ignored and rel in gitutil.ignored(root, [rel]):
        return Finding("ok", "ok", "", rel, note="git-ignored")

    finding = _raw_rule(rel, root, config, subagent)
    if finding is None and content is not None:
        match = near_duplicate(root, rel, content, config.nearDuplicateThreshold)
        if match:
            other, ratio = match
            finding = Finding("near-duplicate", "ask", f"`{rel}` is {ratio:.0%} identical to "
                              f"`{other}`. Edit or reuse that file instead of copying it.", rel,
                              actions=[f"edit {other}"])
    if finding is None:
        return ok
    finding.decision = config.mode(finding.rule)
    if finding.decision == "off":
        return ok
    decision, note = intent.soften(session, finding.rule, rel, finding.decision)
    finding.decision, finding.note = decision, note
    return finding
