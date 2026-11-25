import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CLI = Path(__file__).resolve().parents[1] / "bin" / "no-git-slop"


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def make_repo(path: Path) -> Path:
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "config", "commit.gpgsign", "false")
    (path / "app.py").write_text("print('hi')\n")
    git(path, "add", "app.py")
    git(path, "commit", "-qm", "feat: initial commit")
    return path


def run_cli(root, *args, stdin=None):
    return subprocess.run([sys.executable, str(CLI), *args], input=stdin, capture_output=True,
                          text=True, cwd=root)


def hook(name, event, cwd) -> Any:
    out = subprocess.run([sys.executable, str(CLI), "hook", name], input=json.dumps(event),
                         capture_output=True, text=True, cwd=cwd)
    assert out.returncode == 0, out.stderr
    assert "skipped after an error" not in out.stderr, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else None


def decision(result):
    if not result:
        return None
    spec = result.get("hookSpecificOutput", {})
    if "permissionDecision" in spec:
        return spec["permissionDecision"]
    if "additionalContext" in spec:
        return "warn"
    return None


def reason(result):
    spec = (result or {}).get("hookSpecificOutput", {})
    return spec.get("permissionDecisionReason") or spec.get("additionalContext") or ""


def write_event(repo, rel, content="x", session="s1", tool="Write", **extra):
    return {"cwd": str(repo), "session_id": session, "tool_name": tool,
            "tool_input": {"file_path": str(repo / rel), "content": content}, **extra}


def bash_event(repo, command, session="s1", cwd=None):
    return {"cwd": str(cwd or repo), "session_id": session, "tool_name": "Bash",
            "tool_input": {"command": command}}


def prompt(repo, text, session="s1"):
    return hook("prompt", {"cwd": str(repo), "session_id": session, "prompt": text}, repo)


def start(repo, session="s1"):
    return hook("session-start", {"cwd": str(repo), "session_id": session, "source": "startup"}, repo)
