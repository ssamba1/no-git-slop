"""Run the Claude Code hook handlers for other agents: Codex CLI, Gemini CLI and Cursor.

Each agent's event is translated into the Claude-shaped event the handlers read, the
same handler runs, and its decision is translated back. Where an agent has no "ask"
(Codex and Gemini hooks, Cursor's preToolUse), "ask" becomes "deny" and the reason tells
the agent to ask the user, who can approve with the code in the message.

Hook formats, as documented:
- Codex: https://learn.chatgpt.com/docs/hooks
- Gemini CLI: https://geminicli.com/docs/hooks/reference/
- Cursor: https://cursor.com/docs/hooks
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, Dict, List, Optional, Tuple

from . import hooks, shell

AGENTS = ("codex", "gemini", "cursor")


def _capture(handler: Callable[[dict], None], event: dict) -> List[dict]:
    out: List[dict] = []
    saved = hooks._emit
    hooks._emit = out.append  # type: ignore[assignment]
    try:
        handler(event)
    finally:
        hooks._emit = saved  # type: ignore[assignment]
    return out


def _pre_decision(results: List[dict]) -> Tuple[Optional[str], str]:
    """(allow|ask|deny|warn|None, reason) from captured PreToolUse output, worst first."""
    level, reasons = None, []
    rank = {None: 0, "warn": 1, "ask": 2, "deny": 3}
    for r in results:
        spec = r.get("hookSpecificOutput", {})
        d = spec.get("permissionDecision") or ("warn" if spec.get("additionalContext") else None)
        text = spec.get("permissionDecisionReason") or spec.get("additionalContext") or ""
        if rank[d] > rank[level]:
            level = d
        if text:
            reasons.append(text)
    return level, " ".join(reasons)


_ASK_NOTE = " (This needs the user's OK: ask them before retrying.)"


# ------------------------------------------------------------------ events in

def _claude_tool_events(agent: str, event: dict) -> List[Tuple[str, dict]]:
    """Claude-shaped (handler name, event) pairs for one pre-tool call."""
    base = {k: event[k] for k in ("cwd", "session_id", "agent_id", "agent_type") if k in event}
    name = str(event.get("tool_name") or "")
    tin = event.get("tool_input") or {}
    if isinstance(tin, str):  # Cursor sends some tool inputs as a JSON string
        try:
            tin = json.loads(tin)
        except ValueError:
            tin = {}
    if agent == "cursor":
        base.setdefault("session_id", event.get("conversation_id"))
        roots = event.get("workspace_roots") or []
        base.setdefault("cwd", event.get("cwd") or (roots[0] if roots else os.environ.get("CURSOR_PROJECT_DIR")))
        if "command" in event and not name:  # beforeShellExecution
            return [("bash", {**base, "tool_name": "Bash", "tool_input": {"command": event["command"]}})]
    if name in ("Bash", "Shell", "run_shell_command"):
        cwd = tin.get("dir_path") or tin.get("working_directory") or base.get("cwd")
        return [("bash", {**base, "cwd": cwd, "tool_name": "Bash",
                          "tool_input": {"command": tin.get("command") or ""}})]
    if name == "apply_patch":  # Codex: the patch text is tool_input.command
        files = shell.patch_new_files(str(tin.get("command") or tin.get("patch") or ""))
        return [("write", {**base, "tool_name": "Write",
                           "tool_input": {"file_path": path, "content": content}})
                for path, content in files.items()]
    if name in ("Write", "write_file", "Edit", "replace", "StrReplace", "MultiEdit"):
        # Cursor's docs don't list Write's argument names, so accept the common spellings.
        path = tin.get("file_path") or tin.get("path") or tin.get("target_file")
        content = tin.get("content", tin.get("contents", tin.get("file_text")))
        tool = "Write" if name in ("Write", "write_file") else "Edit"
        return [("write", {**base, "tool_name": tool,
                           "tool_input": {"file_path": path, "content": content}})] if path else []
    return []


def _session_event(agent: str, event: dict) -> dict:
    e = dict(event)
    if agent == "cursor":
        e.setdefault("session_id", event.get("conversation_id"))
        roots = event.get("workspace_roots") or []
        e.setdefault("cwd", roots[0] if roots else os.environ.get("CURSOR_PROJECT_DIR"))
        if "loop_count" in event:
            e["stop_hook_active"] = bool(event["loop_count"])
    return e


# ----------------------------------------------------------------- events out

def _pre_tool(agent: str, event: dict) -> Optional[dict]:
    results: List[dict] = []
    for handler, claude_event in _claude_tool_events(agent, event):
        results += _capture(hooks.HANDLERS[handler], claude_event)
    level, reason = _pre_decision(results)
    shell_hook = agent == "cursor" and "command" in event and not event.get("tool_name")
    if level == "ask" and not shell_hook:
        level, reason = "deny", reason + _ASK_NOTE  # these hooks have no "ask"
    if agent == "codex":
        if level in ("deny", "ask"):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": level,
                                           "permissionDecisionReason": reason}}
        return {"systemMessage": reason} if level == "warn" else None
    if agent == "gemini":
        if level == "deny":
            return {"decision": "deny", "reason": reason}
        return {"systemMessage": reason} if level == "warn" else None
    # Cursor permission hooks must always answer with valid JSON.
    if level in ("deny", "ask"):
        return {"permission": level, "user_message": reason, "agent_message": reason}
    out: Dict[str, str] = {"permission": "allow"}
    if level == "warn":
        out["agent_message"] = reason
    return out


def _context(results: List[dict]) -> str:
    return "\n".join(r.get("hookSpecificOutput", {}).get("additionalContext", "") for r in results).strip()


def _stop_reason(results: List[dict]) -> Tuple[Optional[str], str]:
    for r in results:
        if r.get("decision") == "block":
            return "block", r.get("reason", "")
    msgs = [r["systemMessage"] for r in results if r.get("systemMessage")]
    return None, " ".join(msgs)


def handle(agent: str, name: str, event: dict) -> Optional[dict]:
    """Translate one agent hook call. `name` is the agent's own event name."""
    if name in ("PreToolUse", "BeforeTool", "preToolUse", "beforeShellExecution"):
        return _pre_tool(agent, event)
    e = _session_event(agent, event)
    if name in ("UserPromptSubmit", "BeforeAgent", "beforeSubmitPrompt"):
        ctx = _context(_capture(hooks.on_prompt, e))
        if agent == "cursor":
            return {"continue": True}  # beforeSubmitPrompt can't add context
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": ctx}} if ctx else None
    if name in ("SessionStart", "sessionStart"):
        ctx = _context(_capture(hooks.on_session_start, e))
        if agent == "cursor":
            return {"additional_context": ctx}
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
    if name in ("Stop", "AfterAgent", "stop"):
        decision, reason = _stop_reason(_capture(hooks.on_stop, e))
        if agent == "cursor":
            return {"followup_message": reason} if decision else {}
        if agent == "gemini":
            if decision:
                return {"decision": "deny", "reason": reason}  # AfterAgent: deny forces a retry
            return {"systemMessage": reason} if reason else None
        if decision:
            return {"decision": "block", "reason": reason}
        return {"systemMessage": reason} if reason else {}  # Codex Stop wants JSON on exit 0
    if name in ("PostToolUse", "AfterTool", "postToolUse", "afterShellExecution", "afterFileEdit"):
        pairs = _claude_tool_events(agent, e) if name != "afterShellExecution" else \
            [("bash", {**e, "tool_name": "Bash", "tool_input": {"command": event.get("command", "")}})]
        if name == "afterFileEdit":
            pairs = [("write", {**e, "tool_name": "Edit", "tool_input": {"file_path": event.get("file_path")}})]
        feedback = ""
        for _, claude_event in pairs:
            for r in _capture(hooks.on_post, claude_event):
                feedback = r.get("reason") or feedback
        if not feedback:
            return None
        if agent == "codex":
            return {"decision": "block", "reason": feedback}
        if agent == "gemini":
            return {"hookSpecificOutput": {"hookEventName": "AfterTool", "additionalContext": feedback}}
        return {"additional_context": feedback} if name == "postToolUse" else None
    return None


def run(agent: str, name: str) -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            return 0
        out = handle(agent, name, event)
        if out is not None:
            sys.stdout.write(json.dumps(out))
    except Exception as exc:  # noqa: BLE001 - fail open, but say why.
        sys.stderr.write(f"no-git-slop: {agent} hook '{name}' skipped after an error: {exc}\n")
        if agent == "cursor" and name in ("preToolUse", "beforeShellExecution"):
            sys.stdout.write(json.dumps({"permission": "allow"}))  # invalid JSON would block
    return 0


# --------------------------------------------------------------- config files

def command_line(argv: List[str], agent: str) -> str:
    """A hook command line every agent's shell runs. Codex on Windows uses PowerShell, where a
    quoted program path is a string, not a call: unquoted paths work in PowerShell, cmd and sh,
    and a path with spaces needs PowerShell's `&` there (checked against Codex 0.155)."""
    if not any(c.isspace() for a in argv for c in a):
        return " ".join(argv)
    quoted = " ".join(f'"{a}"' for a in argv)
    return f"& {quoted}" if agent == "codex" and os.name == "nt" else quoted


def hook_config(agent: str, argv: List[str]) -> Tuple[str, dict]:
    """(path relative to the repo, hook config) that runs no-git-slop for this agent."""
    run_cmd = command_line(argv, agent)

    def cmd(event: str) -> str:
        return f"{run_cmd} hook-agent {agent} {event}"

    if agent == "codex":
        def entry(event, matcher=None):
            e = {"hooks": [{"type": "command", "command": cmd(event), "timeout": 30}]}
            return {**({"matcher": matcher} if matcher else {}), **e}
        return ".codex/hooks.json", {"hooks": {
            "SessionStart": [entry("SessionStart")],
            "UserPromptSubmit": [entry("UserPromptSubmit")],
            "PreToolUse": [entry("PreToolUse", "Bash|apply_patch")],
            "PostToolUse": [entry("PostToolUse", "Bash|apply_patch")],
            "Stop": [entry("Stop")]}}
    if agent == "gemini":
        def g_entry(event, matcher="*"):
            return {"matcher": matcher, "hooks": [{"name": f"no-git-slop-{event}", "type": "command",
                                                   "command": cmd(event), "timeout": 30000}]}
        tools = "run_shell_command|write_file|replace"
        return ".gemini/settings.json", {"hooks": {
            "SessionStart": [g_entry("SessionStart", "startup|resume|clear")],
            "BeforeAgent": [g_entry("BeforeAgent")],
            "BeforeTool": [g_entry("BeforeTool", tools)],
            "AfterTool": [g_entry("AfterTool", tools)],
            "AfterAgent": [g_entry("AfterAgent")]}}
    if agent == "cursor":
        def c_entry(event, matcher=None):
            return {"command": cmd(event), "timeout": 30, **({"matcher": matcher} if matcher else {})}
        return ".cursor/hooks.json", {"version": 1, "hooks": {
            "sessionStart": [c_entry("sessionStart")],
            "beforeSubmitPrompt": [c_entry("beforeSubmitPrompt")],
            "beforeShellExecution": [c_entry("beforeShellExecution")],
            "preToolUse": [c_entry("preToolUse", "Write")],
            "afterShellExecution": [c_entry("afterShellExecution")],
            "afterFileEdit": [c_entry("afterFileEdit")],
            "stop": [c_entry("stop")]}}
    raise ValueError(agent)


def merge_config(existing: dict, ours: dict) -> dict:
    """Add our hook entries to an existing config, replacing any earlier no-git-slop entries."""
    merged = dict(existing)
    merged_hooks = dict(existing.get("hooks") or {})
    for event, entries in ours["hooks"].items():
        kept = [e for e in merged_hooks.get(event, []) if "hook-agent" not in json.dumps(e)]
        merged_hooks[event] = kept + entries
    merged["hooks"] = merged_hooks
    for k, v in ours.items():
        if k != "hooks":
            merged.setdefault(k, v)
    return merged
