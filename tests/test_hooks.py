"""Run the real CLI the way Claude Code does: JSON on stdin, JSON on stdout."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

CLI = Path(__file__).resolve().parents[1] / "bin" / "no-git-slop"


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "app.py").write_text("print('hi')\n")
    git(tmp_path, "add", "app.py")
    git(tmp_path, "commit", "-qm", "feat: initial commit")
    return tmp_path


def hook(name, event, cwd) -> Any:
    out = subprocess.run([sys.executable, str(CLI), "hook", name], input=json.dumps(event),
                         capture_output=True, text=True, cwd=cwd)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else None


def decision(result):
    return result and result["hookSpecificOutput"]["permissionDecision"]


def write_event(repo, rel):
    return {"cwd": str(repo), "tool_name": "Write",
            "tool_input": {"file_path": str(repo / rel), "content": "x"}}


def bash_event(repo, command):
    return {"cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": command}}


def test_write_blocks_summary_doc(repo):
    assert decision(hook("write", write_event(repo, "CHANGES_SUMMARY.md"), repo)) == "deny"


def test_write_blocks_copy_of_existing_file(repo):
    assert decision(hook("write", write_event(repo, "app_fixed.py"), repo)) == "deny"


def test_write_allows_overwrite_of_existing(repo):
    assert hook("write", write_event(repo, "app.py"), repo) is None


def test_write_allows_new_module(repo):
    assert hook("write", write_event(repo, "src/cache.py"), repo) is None


def test_write_ignores_files_outside_repo(repo, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere")
    event = {"cwd": str(repo), "tool_input": {"file_path": str(other / "NOTES.md")}}
    assert hook("write", event, repo) is None


@pytest.mark.parametrize("command", [
    "git add -A",
    "git add . && git commit -m 'feat: x'",
    "git commit --no-verify -m 'feat: add x'",
    "git commit -am 'feat: add x'",
    "git commit -m 'updated files'",
    "git commit -m \"$(cat <<'EOF'\nfixed stuff\n\nbody\nEOF\n)\"",
])
def test_bash_blocks_sloppy_git(repo, command):
    assert decision(hook("bash", bash_event(repo, command), repo)) == "deny"


@pytest.mark.parametrize("command", [
    "ls -la",
    "git status",
    "git add app.py",
    "git commit -m 'feat(app): greet the user by name'",
    "git commit -m \"$(cat <<'EOF'\nfix(app): avoid crash on empty input\n\nDetails.\nEOF\n)\"",
    "git commit -F msg.txt",
    "echo 'git add -A is bad'",
])
def test_bash_allows_clean_git(repo, command):
    assert hook("bash", bash_event(repo, command), repo) is None


def test_commit_blocked_when_slop_is_staged(repo):
    (repo / "FINAL_REPORT.md").write_text("done\n")
    git(repo, "add", "FINAL_REPORT.md")
    result = hook("bash", bash_event(repo, "git commit -m 'docs: add report'"), repo)
    assert decision(result) == "deny"


def test_commit_blocked_for_agent_local_files(repo):
    (repo / "CLAUDE.local.md").write_text("my notes\n")
    assert decision(hook("write", write_event(repo, "CLAUDE.local.md"), repo)) is None  # creating it is fine
    git(repo, "add", "CLAUDE.local.md")
    assert decision(hook("bash", bash_event(repo, "git commit -m 'docs: add notes'"), repo)) == "deny"


def test_session_start_flags_oversized_context_file(repo):
    (repo / "AGENTS.md").write_text("rule\n" * 250)
    text = hook("session-start", {"cwd": str(repo)}, repo)["hookSpecificOutput"]["additionalContext"]
    assert "`AGENTS.md` is 250 lines (limit 200)" in text
    (repo / "AGENTS.md").write_text("rule\n" * 20)
    text = hook("session-start", {"cwd": str(repo)}, repo)["hookSpecificOutput"]["additionalContext"]
    assert "AGENTS.md" not in text


def test_session_start_text_stays_small(repo):
    # The reminder loads into every new chat; keep it from growing unnoticed.
    text = hook("session-start", {"cwd": str(repo)}, repo)["hookSpecificOutput"]["additionalContext"]
    assert len(text) <= 700, len(text)


def test_skill_and_agent_descriptions_stay_small():
    # Their descriptions load into every session that has the plugin.
    root = CLI.parents[1]
    files = list(root.glob("skills/*/SKILL.md")) + list(root.glob("agents/*.md"))
    total = sum(len(line) for f in files for line in f.read_text(encoding="utf-8").splitlines()
                if line.startswith("description:"))
    assert files and total <= 1200, total


def test_stop_only_flags_files_created_this_session(repo):
    (repo / "old_debug.log").write_text("from before\n")
    start = hook("session-start", {"cwd": str(repo)}, repo)
    assert "no-git-slop" in start["hookSpecificOutput"]["additionalContext"]

    assert hook("stop", {"cwd": str(repo)}, repo) is None

    (repo / "debug_login.py").write_text("print(1)\n")
    (repo / "src").mkdir()
    (repo / "src" / "login.py").write_text("print(2)\n")
    result = hook("stop", {"cwd": str(repo)}, repo)
    assert result["decision"] == "block"
    assert "debug_login.py" in result["reason"]
    assert "old_debug.log" not in result["reason"]
    assert "login.py`" not in result["reason"].replace("debug_login.py`", "")


def test_stop_does_not_loop(repo):
    hook("session-start", {"cwd": str(repo)}, repo)
    (repo / "SUMMARY.md").write_text("x\n")
    first = hook("stop", {"cwd": str(repo)}, repo)
    assert first["decision"] == "block"
    again = hook("stop", {"cwd": str(repo), "stop_hook_active": True}, repo)
    assert "decision" not in again
    assert "SUMMARY.md" in again["systemMessage"]


def test_hooks_fail_open_on_garbage(repo):
    out = subprocess.run([sys.executable, str(CLI), "hook", "write"], input="not json",
                         capture_output=True, text=True, cwd=repo)
    assert out.returncode == 0
    assert out.stdout == ""


def test_cli_check_and_init(repo):
    (repo / "NOTES_v2.md").write_text("x\n")
    out = subprocess.run([sys.executable, str(CLI), "check"], capture_output=True,
                         text=True, cwd=repo)
    assert out.returncode == 1 and "NOTES_v2.md" in out.stdout

    subprocess.run([sys.executable, str(CLI), "init"], check=True, cwd=repo, capture_output=True)
    assert ".scratch/" in (repo / ".gitignore").read_text()


def test_cli_commits_range(repo):
    (repo / "b.py").write_text("")
    git(repo, "add", "b.py")
    git(repo, "commit", "-qm", "wip")
    out = subprocess.run([sys.executable, str(CLI), "commits", "HEAD~1..HEAD"],
                         capture_output=True, text=True, cwd=repo)
    assert out.returncode == 1 and "wip" in out.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash paths exist only on Windows")
def test_git_bash_style_event_paths(repo):
    bash_path = "/" + str(repo)[0].lower() + str(repo)[2:].replace("\\", "/")
    event = {"cwd": bash_path, "tool_name": "Write",
             "tool_input": {"file_path": bash_path + "/CHANGES_SUMMARY.md", "content": "x"}}
    assert decision(hook("write", event, repo.parent)) == "deny"


@pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")
@pytest.mark.parametrize("name", ["no-git-slop", "ngs"])
def test_launcher_runs_from_its_own_folder(name):
    out = subprocess.run(["sh", name, "--version"], cwd=CLI.parent, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip(), out.stderr


def ps_event(repo, command):
    return {"cwd": str(repo), "tool_name": "PowerShell", "tool_input": {"command": command}}


@pytest.mark.parametrize("command,expected", [
    ("git status; git add -A", "deny"),
    ("if ($?) { git commit --no-verify -m 'feat: x' }", "deny"),
    ("if (Test-Path x) { git status } else { git push -f origin main }", "ask"),
    ("$env:HUSKY='0'; git commit -m 'feat: add parser'", "deny"),
    ("Set-Content -Path IMPLEMENTATION_SUMMARY.md -Value 'x'", "deny"),
    (r"'x' | Out-File .\notes_backup.md", "deny"),
    ("Copy-Item app.py app_copy.py", "deny"),
    (r"Remove-Item -Recurse -Force .git\hooks", "deny"),
    ("git commit -m @'\nfeat: add parser\n\n- a body line\n'@", None),
    ("[IO.File]::WriteAllText('IMPLEMENTATION_SUMMARY.md', 'done')", "deny"),
    (r'[System.IO.File]::AppendAllLines("notes_backup.md", $lines)', "deny"),
    ("[IO.File]::Copy('app.py', 'app_copy.py', $true)", "deny"),
    (r"[IO.File]::Delete('.git\hooks\pre-commit')", "deny"),
    ("$w = New-Object System.IO.StreamWriter('COMMIT_REPORT.md'); $w.Close()", "deny"),
    ("[IO.File]::ReadAllText('README.md')", None),
    ("git status; git diff --stat", None),
    ("Get-ChildItem -Recurse | Select-Object -First 5", None),
])
def test_powershell_commands(repo, command, expected):
    assert decision(hook("bash", ps_event(repo, command), repo)) == expected


def test_bash_into_powershell(repo):
    assert decision(hook("bash", bash_event(repo, "powershell -Command \"git add -A\""), repo)) == "deny"
