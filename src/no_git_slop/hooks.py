"""Claude Code hook handlers.

Each handler reads the hook's JSON from stdin and prints a JSON decision.
Handlers fail open on internal errors (the Stop sweep, pre-commit and CI
are the backstops), so a bug here can never wedge a Claude session.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from . import gitutil, intent, shell, state
from .commits import check_commit_message
from .config import Config, load_config
from .findings import Finding, worst
from .gates import evaluate_bash, evaluate_write, rel_to, state_path_finding
from .presets import active_presets, is_harness_artifact
from .rules import judge_new_file

REMINDER = (
    "no-git-slop is active. Keep the repo tidy:\n"
    "- Edit existing files; never create *_v2, *_new, *_fixed or backup copies.\n"
    "- Report summaries in chat, not in new .md files.\n"
    "- Throwaway scripts go in {scratch}/ (git-ignored); delete them when done.\n"
    "- Before adding a function, check what exists: `no-git-slop index <dir>`.\n"
    "- Stage files by path, never `git add -A` or `git add .`; one change per commit.\n"
    "- Commit messages: Conventional Commits, e.g. `fix(api): handle empty token`."
)
_FILE_RULES = {"junk-file", "backup-copy", "copy-folder", "version-copy", "near-duplicate",
               "report-doc", "root-doc", "throwaway-script", "root-script", "file-budget",
               "new-top-dir", "misplaced-test"}


def _emit(obj) -> None:
    sys.stdout.write(json.dumps(obj))


def _path(raw: Optional[str]) -> Path:
    """An event path as a local Path; maps Git Bash /c/... to C:/... on Windows."""
    return (shell._resolve(Path("."), raw) or Path(raw)) if raw else Path(".")


def _root_for(event: dict, start: Optional[Path] = None) -> Optional[Path]:
    candidates = [start] if start else []
    candidates += [_path(event.get("cwd")), _path(os.environ.get("CLAUDE_PROJECT_DIR"))]
    for c in candidates:
        if c and c.exists():
            root = gitutil.repo_root(c)
            if root:
                return root
    return None


def _subagent(event: dict) -> bool:
    return bool(event.get("agent_id") or event.get("agent_type"))


def _record(root: Path, session, event: str, findings: List[Finding]) -> None:
    for f in findings:
        if f.strength > 0 or f.note:
            state.ledger(root, {"session": session.key if session else "", "event": event,
                                "rule": f.rule, "decision": f.decision, "path": f.path[:200],
                                "note": f.note})


def _decide_pre_tool(root: Path, session, findings: List[Finding], event_name: str) -> None:
    _record(root, session, event_name, findings)
    top = worst(findings)
    if top is None:
        return
    level = top.decision
    chosen = [f for f in findings if f.decision == level]
    lines = []
    for f in chosen[:4]:
        line = f.line()
        if level == "deny" and session is not None and f.path and f.rule in _FILE_RULES:
            code = intent.allow_code(session, f.path)
            line += (f" If the user explicitly wants this file, ask them; they can approve it by "
                     f"replying `{code}`.")
        lines.append(line)
    if len(chosen) > 4:
        lines.append(f"(+{len(chosen) - 4} more)")
    lines.append(f"Details: `no-git-slop explain {top.rule}`.")
    reason = "no-git-slop: " + " ".join(lines)
    if session is not None:
        session.save()
    if level == "warn":
        _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": reason}})
    else:
        _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "permissionDecision": level,
                                      "permissionDecisionReason": reason}})


# ------------------------------------------------------------ UserPrompt

def on_prompt(event: dict) -> None:
    root = _root_for(event)
    if not root:
        return
    session = state.Session(root, state.session_key(event))
    notices = intent.record_prompt(session, str(event.get("prompt") or ""))
    session.save()
    if notices:
        _emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                      "additionalContext": "\n".join(notices)}})


# ---------------------------------------------------------------- Write

def on_write(event: dict) -> None:
    tool = event.get("tool_name") or "Write"
    tool_input = event.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return
    target = _path(file_path)
    if not target.is_absolute():
        target = _path(event.get("cwd")) / target
    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    root = _root_for(event, parent)
    if not root:
        return
    rel = rel_to(root, target)
    if rel is None:
        _decide_pre_tool(root, None, state_path_finding(root, load_config(root), target), f"pre-{tool}")
        return
    if not rel:
        return
    session = state.Session(root, state.session_key(event))
    config = load_config(root)
    is_new = tool == "Write" and not target.exists()
    content = tool_input.get("content") if isinstance(tool_input.get("content"), str) else None
    findings = evaluate_write(root, config, session, rel, is_new, content, _subagent(event))
    _decide_pre_tool(root, session, findings, f"pre-{tool}")


# ----------------------------------------------------------------- Bash

def _command(event: dict) -> str:
    """The shell command of a Bash or PowerShell call, in the POSIX shape the parser reads."""
    command = (event.get("tool_input") or {}).get("command") or ""
    return shell.from_powershell(command) if event.get("tool_name") == "PowerShell" else command


def on_bash(event: dict) -> None:
    command = _command(event)
    if not command.strip():
        return
    cwd = _path(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR"))
    root = _root_for(event)
    if not root:
        return
    session = state.Session(root, state.session_key(event))
    config = load_config(root)
    findings = evaluate_bash(root, config, session, command, cwd, _subagent(event))
    _decide_pre_tool(root, session, findings, f"pre-{event.get('tool_name') or 'Bash'}")


# ------------------------------------------------------------ PostToolUse

def on_post(event: dict) -> None:
    root = _root_for(event)
    if not root:
        return
    session = state.Session(root, state.session_key(event))
    baseline = session.baseline() or set()
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    paths: List[Path] = []
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        if fp:
            paths.append(_path(fp))
    command = _command(event) if tool in ("Bash", "PowerShell") else ""
    cwd = _path(event.get("cwd"))
    if command:
        for cmd in shell.parse(command, cwd):
            paths += [t.path for t in shell.write_targets(cmd) if t.kind not in ("delete", "chmod")]
    tracked = None
    for p in paths:
        rel = rel_to(root, p)
        if not rel or rel.startswith(".git/") or not (root / rel).exists():
            continue
        session.add_unique("touched", rel)
        if rel not in baseline:
            if tracked is None:
                tracked = set(gitutil.tracked_files(root))
            if rel not in tracked:
                session.add_unique("created", rel)
    feedback = None
    if command and "commit" in command:
        head = gitutil.head_sha(root)
        if head and head != session.data.get("last_head_checked"):
            session.data["last_head_checked"] = head
            problems = [f for f in check_commit_message(gitutil.head_message(root), load_config(root),
                                                        by_agent=True) if f.decision == "deny"]
            if problems:
                feedback = ("no-git-slop: the commit just made has problems: "
                            + " ".join(f.line() for f in problems[:3])
                            + " Fix it with `git commit --amend -m \"type(scope): ...\"` before "
                            "continuing.")
                _record(root, session, "post-commit", problems)
    session.save()
    if feedback:
        _emit({"decision": "block", "reason": feedback})
        return
    block, note = _written_text_review(root, tool, tool_input)
    if block:
        _emit({"decision": "block", "reason": block})
    elif note:
        _emit({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}})


_DOC_EXT = (".md", ".mdx", ".markdown", ".rst", ".txt")


def _written_text_review(root: Path, tool: str, tool_input: dict):
    """(block reason, note) for AI-style writing or code in the text just written.
    Only the new text is judged, never what was already in the file."""
    fp = str(tool_input.get("file_path") or "")
    if tool not in ("Write", "Edit", "MultiEdit") or not fp:
        return None, None
    if tool == "Write":
        text = tool_input.get("content") or ""
    elif tool == "Edit":
        text = tool_input.get("new_string") or ""
    else:
        text = "\n".join(e.get("new_string") or "" for e in tool_input.get("edits") or [])
    text = str(text)
    config = load_config(root)
    if fp.lower().endswith(_DOC_EXT):
        from .commits import doc_findings

        found = doc_findings(text, config)
        return None, (f"no-git-slop: {found[0].reason}" if found else None)
    from .codestyle import code_findings

    hard, soft = code_findings(fp.replace("\\", "/"), text)
    block = note = None
    if hard and config.mode("elided-code") in ("deny", "ask"):
        block = "no-git-slop: " + "; ".join(hard) + "."
    elif hard and config.mode("elided-code") == "warn":
        soft = hard + soft
    if soft and config.mode("code-slop") != "off":
        note = "no-git-slop: in the code just written, " + "; ".join(soft[:3]) + "."
    return block, note


# ---------------------------------------------------------- SessionStart

# Instruction files agents load into every session.
CONTEXT_FILES = ("CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md")


def oversized_context_files(root: Path, max_lines: int) -> list:
    """(path, lines) for always-loaded agent instruction files longer than max_lines (0 disables)."""
    if max_lines <= 0:
        return []
    out = []
    for rel in CONTEXT_FILES:
        try:
            n = (root / rel).read_text(encoding="utf-8", errors="replace").count("\n")
        except OSError:
            continue
        if n > max_lines:
            out.append((rel, n))
    return out


_CONTEXT_PATH_RE = re.compile(r"`((?:\.?[\w.-]+/)+[\w.-]+\.\w+)`")


def stale_context_paths(root: Path, limit: int = 3) -> list:
    """Backticked file paths in agent instruction files that no longer exist."""
    out = []
    for rel in CONTEXT_FILES:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for p in _CONTEXT_PATH_RE.findall(text):
            if not p.startswith("~") and not (root / p).exists() and p not in out:
                out.append(p)
    return out[:limit]


def on_session_start(event: dict) -> None:
    root = _root_for(event)
    if not root:
        return
    state.prune(root)
    session = state.Session(root, state.session_key(event))
    source = event.get("source") or "startup"
    if source in ("startup", "clear") or session.baseline() is None:
        session.write_baseline(gitutil.untracked_files(root))
    config = load_config(root)
    scratch = config.scratchDir.strip("/")
    extra = []
    if scratch and f"{scratch}/x" not in gitutil.ignored(root, [f"{scratch}/x"]):
        gitutil.add_local_exclude(root, f"/{scratch}/")
    presets = active_presets(root, config.presets)
    if presets:
        extra.append(f"- Planning files for {', '.join(presets)} are allowed in their usual folders.")
    if config.verify:
        extra.append(f"- Before finishing, `{config.verify}` must pass.")
    big = oversized_context_files(root, config.contextFileMaxLines)
    if big:
        extra.append("- " + ", ".join(f"`{p}` is {n} lines" for p, n in big) +
                     f" (limit {config.contextFileMaxLines}) and loads into every session. "
                     "Don't add to it; move detail into docs/ and link it.")
    stale = stale_context_paths(root)
    if stale:
        extra.append("- Agent instructions mention files that don't exist: "
                     + ", ".join(f"`{p}`" for p in stale) + ". Fix or remove those references.")
    session.save()
    text = REMINDER.format(scratch=scratch) + ("\n" + "\n".join(extra) if extra else "")
    _emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}})


# ------------------------------------------------------------------ Stop

def find_new_slop(root: Path, config: Config, session=None):
    """(blocking findings, softer findings, harness artifacts) for files new this session."""
    baseline = (session.baseline() if session else None) or set()
    hard, soft, harness = [], [], []
    for rel in gitutil.untracked_files(root):
        if rel in baseline:
            continue
        if is_harness_artifact(root, rel):
            harness.append(rel)
            continue
        f = judge_new_file(rel, root, config, session, check_ignored=False)
        if f.decision == "deny":
            hard.append(f)
        elif f.strength > 0:
            soft.append(f)
    return hard, soft, harness


def _changed_this_session(root: Path, session) -> bool:
    if session.data.get("touched") or session.data.get("created"):
        return True
    return gitutil.worktree_line_count(root) > 0


def on_stop(event: dict) -> None:
    root = _root_for(event)
    if not root:
        return
    session = state.Session(root, state.session_key(event))
    config = load_config(root)
    if session.data.get("off"):
        return
    hard, soft, harness = find_new_slop(root, config, session)
    notes: List[str] = []

    if config.verify and _changed_this_session(root, session):
        from .integrations import run_verify

        result = run_verify(root, config.verify, config.verifyTimeout)
        if result["status"] == "ran" and result["exit"] != 0:
            session.data["verify_failures"] = session.data.get("verify_failures", 0) + 1
            if session.data["verify_failures"] <= config.verifyMaxRetries and \
                    config.mode("verify-failed") == "deny":
                hard.append(Finding("verify-failed", "deny", f"`{config.verify}` failed (exit "
                                    f"{result['exit']}). Fix the failures without weakening the "
                                    f"tests. Last output:\n{result['output']}"))
            else:
                notes.append(f"`{config.verify}` is still failing; stopping after "
                             f"{config.verifyMaxRetries} retries.")
        elif result["status"] == "ran":
            session.data["verify_failures"] = 0
        elif result["status"] in ("unavailable", "failed"):
            notes.append(f"verify command could not run: {result['output'][:200]}")

    lines = gitutil.worktree_line_count(root)
    if lines > config.diffBudget and config.mode("diff-budget") != "off":
        soft.append(Finding("diff-budget", "warn", f"Uncommitted changes total {lines} lines "
                            f"(budget {config.diffBudget}). Remove anything the task doesn't need "
                            "(the slop-minimizer agent can help) or split the work."))

    if config.integrationsAtStop:
        from .integrations import run_all

        for r in run_all(root, config.integrations):
            if r["status"] == "ran" and r.get("exit"):
                soft.append(Finding(f"tool:{r['name']}", "warn", f"{r['name']} reported "
                                    f"problems:\n{r['output']}"))

    _record(root, session, "stop", hard + soft)
    if not hard:
        session.data["stop_hash"] = ""
        session.save()
        messages = [f.line() for f in soft] + notes
        if messages:
            _emit({"systemMessage": "no-git-slop: " + " ".join(messages)})
        return

    listing = "\n".join(f"- {f.line()}" for f in hard)
    digest = hashlib.sha1(listing.encode()).hexdigest()
    repeat = event.get("stop_hook_active") and digest == session.data.get("stop_hash")
    session.data["stop_hash"] = digest
    session.save()
    if repeat:
        _emit({"systemMessage": "no-git-slop: these are still unresolved:\n" + listing})
        return
    tail = ""
    if harness:
        tail = ("\nThese were created by Claude Code itself, leave them: "
                + ", ".join(f"`{h}`" for h in harness[:5]))
    if soft:
        tail += "\nAlso consider: " + " ".join(f.line() for f in soft[:3])
    _emit({"decision": "block", "reason": (
        "no-git-slop: before finishing, clean up. Delete these files, fold their content into "
        f"existing files, or move throwaway work into {config.scratchDir}/:\n" + listing + tail)})


HANDLERS = {
    "prompt": on_prompt,
    "write": on_write,
    "bash": on_bash,
    "post": on_post,
    "session-start": on_session_start,
    "stop": on_stop,
}


def run(name: str) -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            return 0
        HANDLERS[name](event)
    except Exception as exc:  # noqa: BLE001 - fail open, but say why.
        sys.stderr.write(f"no-git-slop: hook '{name}' skipped after an error: {exc}\n")
    return 0
