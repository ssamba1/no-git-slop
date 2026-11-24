"""Planning-framework presets and Claude Code harness artifacts.

Frameworks like Spec Kit, OpenSpec and Superpowers keep plan files on
purpose. When a framework's marker exists, its conventional paths are
allowed at write time; whether they may be committed is the `plans` setting.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path
from typing import Dict, List, Tuple

# name -> (marker paths, allowed globs)
PRESETS: Dict[str, Tuple[List[str], List[str]]] = {
    "spec-kit": ([".specify"], [".specify/**", "specs/**"]),
    "openspec": (["openspec"], ["openspec/**"]),
    "superpowers": (["docs/superpowers"], ["docs/superpowers/**"]),
    "planning-with-files": ([".planning", "task_plan.md"],
                            [".planning/**", "task_plan.md", "findings.md", "progress.md"]),
    "humanlayer": (["thoughts"], ["thoughts/**"]),
    "taskmaster": ([".taskmaster"], [".taskmaster/**"]),
    "ccpm": ([".claude/epics", ".claude/prds"], [".claude/epics/**", ".claude/prds/**"]),
    "ralph": ([".ralph", "IMPLEMENTATION_PLAN.md", "fix_plan.md"],
              [".ralph/**", "IMPLEMENTATION_PLAN.md", "fix_plan.md", "PROMPT.md", "specs/**"]),
    "claude-flow": ([".claude-flow"], [".claude-flow/**"]),
    "compound-engineering": (["docs/plans", "docs/solutions"], ["docs/plans/**", "docs/solutions/**"]),
    "kiro": ([".kiro"], [".kiro/**"]),
    "agent-os": ([".agent-os"], [".agent-os/**"]),
    "bmad": ([".bmad-core", "_bmad", "bmad"], [".bmad-core/**", "_bmad/**", "docs/stories/**",
                                               "docs/prd.md", "docs/architecture.md"]),
    "beads": ([".beads"], [".beads/**"]),
}


def glob_match(rel: str, pattern: str) -> bool:
    """fnmatch with `**` meaning any depth (including zero directories)."""
    if pattern.endswith("/**"):
        base = pattern[:-3]
        return rel == base or rel.startswith(base + "/")
    if "**/" in pattern:
        return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.replace("**/", ""))
    return fnmatch.fnmatch(rel, pattern)


def active_presets(root: Path, setting) -> List[str]:
    if setting == "off":
        return []
    if isinstance(setting, list):
        return [p for p in setting if p in PRESETS]
    found = []
    for name, (markers, _) in PRESETS.items():
        if any((root / m).exists() for m in markers):
            found.append(name)
    return found


def plan_preset_for(rel: str, presets: List[str]) -> str:
    for name in presets:
        if any(glob_match(rel, g) for g in PRESETS[name][1]):
            return name
    return ""


# ------------------------------------------------------------- harness files

_HARNESS_GLOBS = [
    "tmpclaude-*", "**/tmpclaude-*", ".claude.json.backup*", ".claude/worktrees/**",
    ".claude/settings.local.json.bak*", ".claude/todos/**", ".claude/plans/**",
]


def is_harness_artifact(root: Path, rel: str) -> bool:
    """Files Claude Code itself creates (sandbox stubs, temp cwd files...)."""
    if any(glob_match(rel, g) for g in _HARNESS_GLOBS):
        return True
    try:
        st = os.lstat(root / rel)
    except OSError:
        return False
    # Sandbox stubs are character devices or /dev/null symlinks (anthropics/claude-code#78419).
    if stat.S_ISCHR(st.st_mode):
        return True
    if stat.S_ISLNK(st.st_mode):
        try:
            return os.readlink(root / rel) == "/dev/null"
        except OSError:
            return False
    return False
