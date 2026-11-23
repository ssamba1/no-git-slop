"""Claude Code hook handlers.

Each handler reads the hook's JSON from stdin and prints a JSON decision.
Handlers fail open: if anything unexpected happens they allow the action,
so a bug here can never wedge a Claude session.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from . import gitutil
from .rules import Config, check_commit_message, judge_new_file, load_config

SESSION_REMINDER = (
    "no-git-slop is active in this repo. Keep it tidy:\n"
    "- Edit existing files; never create *_v2, *_new, *_fixed or backup copies.\n"
    "- Report summaries in chat, not in new .md files.\n"
    "- Throwaway scripts go in {scratch}/ (git-ignored); delete them when done.\n"
    "- Stage files by path, never `git add -A` or `git add .`.\n"
    "- Commit messages: Conventional Commits, e.g. `fix(api): handle empty token`."
)


def _emit(obj) -> None:
    sys.stdout.write(json.dumps(obj))


def _pre_tool(decision: str, reason: str) -> None:
    _emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": "no-git-slop: " + reason,
    }})


def _root_for(event: dict, start: Path = None):
    candidates = [start] if start else []
    candidates += [Path(event.get("cwd") or "."), Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))]
    for c in candidates:
        if c and c.exists():
            root = gitutil.repo_root(c)
            if root:
                return root
    return None


# ------------------------------------------------------------------ Write

def on_write(event: dict) -> None:
    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return
    target = Path(file_path)
    if not target.is_absolute():
        target = Path(event.get("cwd") or ".") / target
    if target.exists():
        return  # Overwriting or editing an existing file is never slop.
    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    root = _root_for(event, parent)
    if not root:
        return
    try:
        rel = target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return  # Outside the repo: not our business.
    verdict = judge_new_file(rel, root, load_config(root))
    if verdict.decision in ("deny", "ask"):
        _pre_tool(verdict.decision, verdict.reason)


# ------------------------------------------------------------------- Bash

_SEPARATORS = {"&&", "||", ";", "|", "&"}
_HEREDOC_RE = re.compile(
    r"^\$\(\s*cat\s+<<-?\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\s*\1\s*\)\s*$", re.DOTALL
)


def _segments(command: str):
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return []
    seg = []
    for tok in tokens:
        if tok in _SEPARATORS or set(tok) <= set(";&|"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def _git_subcommand(seg):
    """Return (subcommand, args) for a `git ...` segment, else (None, [])."""
    if not seg or os.path.basename(seg[0]) != "git":
        return None, []
    i = 1
    while i < len(seg) and seg[i].startswith("-"):
        i += 2 if seg[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i >= len(seg):
        return None, []
    return seg[i], seg[i + 1:]


def _commit_message(args):
    """Extract the message from `git commit` args, or None if it can't be known."""
    parts, i = [], 0
    while i < len(args):
        a = args[i]
        if a in ("-m", "--message") and i + 1 < len(args):
            parts.append(args[i + 1])
            i += 2
            continue
        if a.startswith("--message="):
            parts.append(a.split("=", 1)[1])
        elif a.startswith("-m") and len(a) > 2 and not a.startswith("--"):
            parts.append(a[2:])
        elif a in ("-F", "--file", "-C", "-c", "--reuse-message", "--reedit-message") \
                or a.startswith(("--file=", "--fixup", "--squash")):
            return None
        i += 1
    if not parts:
        return None
    resolved = []
    for p in parts:
        heredoc = _HEREDOC_RE.match(p.strip())
        if heredoc:
            p = heredoc.group(2)
        elif "$(" in p or "`" in p:
            return None
        resolved.append(p)
    return "\n\n".join(resolved)


def on_bash(event: dict) -> None:
    command = (event.get("tool_input") or {}).get("command") or ""
    if "git" not in command:
        return
    root = _root_for(event)
    config = load_config(root) if root else Config()
    for seg in _segments(command):
        sub, args = _git_subcommand(seg)
        if sub == "add" and config.blockBulkAdd:
            if any(a in ("-A", "--all", ".", "*", ":/", "--no-ignore-removal") for a in args):
                _pre_tool("deny", "Stage files explicitly by path (`git add src/x.py tests/test_x.py`) "
                                  "so stray files don't get committed. Run `git status` first.")
                return
        if sub == "commit":
            if any(a in ("-n", "--no-verify") for a in args):
                _pre_tool("deny", "Don't bypass git hooks with --no-verify. Fix what they report.")
                return
            if any(a in ("-a", "--all") or (a.startswith("-") and not a.startswith("--")
                   and "a" in a[1:] and "m" in a[1:]) for a in args):
                _pre_tool("deny", "`git commit -a` sweeps in every modified file. Stage the "
                                  "intended files by path, then commit.")
                return
            if root:
                staged = [judge_new_file(p, root, config) for p in gitutil.staged_new_files(root)]
                blocked = [v for v in staged if v.blocked]
                if blocked:
                    _pre_tool("deny", "Unstage these files (`git restore --staged <file>`) and "
                                      "clean them up first: " + " ".join(v.reason for v in blocked))
                    return
            message = _commit_message(args)
            if message is not None:
                problems = check_commit_message(message, config)
                if problems:
                    _pre_tool("deny", " ".join(problems))
                    return


# ---------------------------------------------------------- Session/Stop

def _baseline_path(root: Path):
    gd = gitutil.git_dir(root)
    return gd / "no-git-slop" / "baseline.json" if gd else None


def on_session_start(event: dict) -> None:
    root = _root_for(event)
    if not root:
        return
    path = _baseline_path(root)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(gitutil.untracked_files(root))), encoding="utf-8")
    config = load_config(root)
    _emit({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": SESSION_REMINDER.format(scratch=config.scratchDir),
    }})


def find_new_slop(root: Path):
    config = load_config(root)
    baseline = set()
    path = _baseline_path(root)
    if path and path.is_file():
        try:
            baseline = set(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            pass
    flagged = []
    for rel in gitutil.untracked_files(root):
        if rel in baseline:
            continue
        verdict = judge_new_file(rel, root, config)
        if verdict.blocked:
            flagged.append(verdict)
    return flagged


def on_stop(event: dict) -> None:
    root = _root_for(event)
    if not root:
        return
    flagged = find_new_slop(root)
    if not flagged:
        return
    listing = "\n".join(f"- {v.reason}" for v in flagged)
    if event.get("stop_hook_active"):
        _emit({"systemMessage": "no-git-slop: these new files still look like clutter:\n" + listing})
        return
    _emit({"decision": "block", "reason": (
        "no-git-slop: before finishing, clean up the files you created this session. "
        "Delete them, fold their content into existing files, or move throwaway work "
        f"into {load_config(root).scratchDir}/.\n" + listing)})


HANDLERS = {
    "write": on_write,
    "bash": on_bash,
    "session-start": on_session_start,
    "stop": on_stop,
}


def run(name: str) -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        HANDLERS[name](event)
    except Exception as exc:  # noqa: BLE001 - fail open, but say why.
        sys.stderr.write(f"no-git-slop: hook '{name}' skipped after an error: {exc}\n")
    return 0
