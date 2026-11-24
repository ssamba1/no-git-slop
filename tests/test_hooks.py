"""Run the real CLI the way Claude Code does: JSON on stdin, JSON on stdout."""

import json
import subprocess
import sys
from pathlib import Path

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


def hook(name, event, cwd):
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
