"""Codex, Gemini CLI and Cursor hooks, fed the event shapes their docs describe."""

import json
import subprocess
import sys
from typing import Any

import pytest

from helpers import CLI, make_repo, run_cli


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def agent_hook(repo, agent, event_name, event) -> Any:
    out = subprocess.run([sys.executable, str(CLI), "hook-agent", agent, event_name],
                         input=json.dumps(event), capture_output=True, text=True, cwd=repo)
    assert out.returncode == 0, out.stderr
    assert "skipped after an error" not in out.stderr, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else None


def codex_tool(repo, tool, command):
    return {"session_id": "c1", "cwd": str(repo), "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": {"command": command}}


# ------------------------------------------------------------------ Codex

def test_codex_bash_bulk_add_is_denied(repo):
    out = agent_hook(repo, "codex", "PreToolUse", codex_tool(repo, "Bash", "git add -A"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "no-git-slop" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_codex_apply_patch_report_file_is_denied(repo):
    patch = "*** Begin Patch\n*** Add File: IMPLEMENTATION_SUMMARY.md\n+# Done\n*** End Patch\n"
    out = agent_hook(repo, "codex", "PreToolUse", codex_tool(repo, "apply_patch", patch))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_apply_patch_editing_existing_file_passes(repo):
    patch = "*** Begin Patch\n*** Update File: app.py\n@@\n-print('hi')\n+print('hello')\n*** End Patch\n"
    assert agent_hook(repo, "codex", "PreToolUse", codex_tool(repo, "apply_patch", patch)) is None


def test_codex_has_no_ask_so_ask_becomes_deny(repo):
    out = agent_hook(repo, "codex", "PreToolUse", codex_tool(repo, "Bash", "git push -f origin main"))
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny" and "user's OK" in spec["permissionDecisionReason"]


def test_codex_stop_sends_agent_back_once(repo):
    start = {"session_id": "c1", "cwd": str(repo), "source": "startup"}
    assert "no-git-slop" in agent_hook(repo, "codex", "SessionStart", start)["hookSpecificOutput"]["additionalContext"]
    (repo / "SUMMARY.md").write_text("x\n")
    stop = {"session_id": "c1", "cwd": str(repo), "stop_hook_active": False}
    out = agent_hook(repo, "codex", "Stop", stop)
    assert out["decision"] == "block" and "SUMMARY.md" in out["reason"]
    again = agent_hook(repo, "codex", "Stop", {**stop, "stop_hook_active": True})
    assert "decision" not in again


# ----------------------------------------------------------------- Gemini

def gemini_tool(repo, tool, tool_input):
    return {"session_id": "g1", "cwd": str(repo), "hook_event_name": "BeforeTool",
            "tool_name": tool, "tool_input": tool_input}


def test_gemini_write_file_report_is_denied(repo):
    event = gemini_tool(repo, "write_file", {"file_path": str(repo / "FIX_NOTES.md"), "content": "x"})
    out = agent_hook(repo, "gemini", "BeforeTool", event)
    assert out["decision"] == "deny" and "FIX_NOTES.md" in out["reason"]


def test_gemini_shell_uses_dir_path(repo):
    (repo / "sub").mkdir()
    event = gemini_tool(repo, "run_shell_command", {"command": "cp ../app.py ../app_backup.py",
                                                    "dir_path": str(repo / "sub")})
    assert agent_hook(repo, "gemini", "BeforeTool", event)["decision"] == "deny"


def test_gemini_prompt_naming_a_file_allows_it(repo):
    prompt = {"session_id": "g1", "cwd": str(repo), "prompt": "Save the notes to `notes/REPORT.md`"}
    agent_hook(repo, "gemini", "BeforeAgent", prompt)
    event = gemini_tool(repo, "write_file", {"file_path": str(repo / "notes/REPORT.md"), "content": "x"})
    assert agent_hook(repo, "gemini", "BeforeTool", event) is None


def test_gemini_after_agent_retries_with_cleanup_list(repo):
    agent_hook(repo, "gemini", "SessionStart", {"session_id": "g1", "cwd": str(repo), "source": "startup"})
    (repo / "debug_login.py").write_text("print(1)\n")
    out = agent_hook(repo, "gemini", "AfterAgent", {"session_id": "g1", "cwd": str(repo),
                                                    "stop_hook_active": False})
    assert out["decision"] == "deny" and "debug_login.py" in out["reason"]


# ----------------------------------------------------------------- Cursor

def cursor_base(repo):
    return {"conversation_id": "k1", "workspace_roots": [str(repo)], "hook_event_name": "x"}


def test_cursor_shell_allows_clean_and_denies_bulk_add(repo):
    ok = agent_hook(repo, "cursor", "beforeShellExecution", {**cursor_base(repo), "command": "git status",
                                                              "cwd": str(repo)})
    assert ok == {"permission": "allow"}
    bad = agent_hook(repo, "cursor", "beforeShellExecution", {**cursor_base(repo), "command": "git add .",
                                                               "cwd": str(repo)})
    assert bad["permission"] == "deny" and bad["agent_message"]


def test_cursor_shell_keeps_ask(repo):
    out = agent_hook(repo, "cursor", "beforeShellExecution",
                     {**cursor_base(repo), "command": "git push -f origin main", "cwd": str(repo)})
    assert out["permission"] == "ask"


def test_cursor_pre_tool_write(repo):
    event = {**cursor_base(repo), "tool_name": "Write", "cwd": str(repo),
             "tool_input": {"file_path": str(repo / "app_v2.py"), "contents": "print(2)\n"}}
    assert agent_hook(repo, "cursor", "preToolUse", event)["permission"] == "deny"
    event["tool_input"] = {"path": str(repo / "src" / "feature.py"), "contents": "x = 1\n"}
    assert agent_hook(repo, "cursor", "preToolUse", event) == {"permission": "allow"}


def test_cursor_stop_follow_up(repo):
    agent_hook(repo, "cursor", "sessionStart", {**cursor_base(repo), "session_id": "k1"})
    (repo / "CHANGES_SUMMARY.md").write_text("x\n")
    out = agent_hook(repo, "cursor", "stop", {**cursor_base(repo), "status": "completed", "loop_count": 0})
    assert "CHANGES_SUMMARY.md" in out["followup_message"]


def test_cursor_bad_input_still_answers(repo):
    out = subprocess.run([sys.executable, str(CLI), "hook-agent", "cursor", "preToolUse"],
                         input="not json", capture_output=True, text=True, cwd=repo)
    assert json.loads(out.stdout) == {"permission": "allow"}


# ------------------------------------------------------------------- init

def test_init_agent_writes_and_merges_configs(repo):
    (repo / ".gemini").mkdir()
    (repo / ".gemini" / "settings.json").write_text(json.dumps(
        {"ui": {"theme": "GitHub"}, "hooks": {"BeforeTool": [{"matcher": "x", "hooks": []}]}}))
    for _ in range(2):  # running twice must not duplicate entries
        out = run_cli(repo, "init", "--agent", "codex", "--agent", "gemini", "--agent", "cursor")
        assert out.returncode == 0, out.stderr
    gemini = json.loads((repo / ".gemini" / "settings.json").read_text())
    assert gemini["ui"] == {"theme": "GitHub"}
    assert len(gemini["hooks"]["BeforeTool"]) == 2 and gemini["hooks"]["BeforeTool"][0]["matcher"] == "x"
    codex = json.loads((repo / ".codex" / "hooks.json").read_text())
    assert "hook-agent codex PreToolUse" in codex["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    cursor = json.loads((repo / ".cursor" / "hooks.json").read_text())
    assert cursor["version"] == 1 and len(cursor["hooks"]["stop"]) == 1


def test_installed_cursor_command_runs(repo):
    run_cli(repo, "init", "--agent", "cursor")
    command = json.loads((repo / ".cursor" / "hooks.json").read_text())["hooks"]["beforeShellExecution"][0]["command"]
    out = subprocess.run(command, shell=True, input=json.dumps({**cursor_base(repo), "command": "git add -A",
                                                                "cwd": str(repo)}),
                         capture_output=True, text=True, cwd=repo)
    assert json.loads(out.stdout)["permission"] == "deny", out.stderr
