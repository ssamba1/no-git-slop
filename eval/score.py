"""Score one finished run: stray files, git hygiene, hidden check, cost.

Independent of no-git-slop's own rules, so the plugin arm is not graded by the plugin:
a new file is stray when it is not git-ignored and matches none of the task's `allowed`
globs. Ignored paths (including the plugin's .scratch/, excluded through .git/info/exclude)
don't count, because they never reach the repository.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Copies and throwaways are stray even inside an allowed folder (src/parser_v2.py).
ALWAYS_STRAY = re.compile(r"(^|/)(debug|tmp|temp|scratch|repro|try|bench(mark)?)_[^/]*$"
                          r"|_(v\d+|old|new|backup|bak|copy|fixed|orig|final|refactored)\.[^/]+$"
                          r"|\.(bak|orig|tmp|log)$|~$", re.I)
CONVENTIONAL = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: \S")
BULK_ADD = re.compile(r"\bgit\s+add\s+(-A\b|--all\b|\.(\s|$)|-u\b)|\bgit\s+commit\s+(-\w*a\w*\b)")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def _matches(path: str, globs) -> bool:
    for g in globs:
        if g.endswith("/**") and path.startswith(g[:-3] + "/"):
            return True
        if fnmatch.fnmatch(path, g):
            return True
    return False


def new_files(root: Path, base: str) -> dict:
    """{path: 'committed'|'untracked'} for files added since `base` and still present."""
    out = {}
    for p in git(root, "diff", "--name-only", "--diff-filter=A", base, "HEAD").splitlines():
        if (root / p).exists():
            out[p] = "committed"
    for p in git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        out.setdefault(p, "untracked")
    staged = git(root, "diff", "--cached", "--name-only", "--diff-filter=A").splitlines()
    for p in staged:
        out.setdefault(p, "staged")
    return out


def commands(transcript: Path) -> list:
    """(command, denied_by_hook) for every shell call in a stream-json transcript."""
    calls, results = {}, {}
    if not transcript.exists():
        return []
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("message") or {}
        for c in msg.get("content") or [] if isinstance(msg.get("content"), list) else []:
            if c.get("type") == "tool_use" and c.get("name") in ("Bash", "PowerShell"):
                calls[c["id"]] = str((c.get("input") or {}).get("command", ""))
            if c.get("type") == "tool_result":
                text = c.get("content")
                text = text if isinstance(text, str) else json.dumps(text)
                results[c.get("tool_use_id")] = "hook" in text.lower() and "no-git-slop" in text
    return [(cmd, results.get(i, False)) for i, cmd in calls.items()]


def run_info(transcript: Path) -> dict:
    info = {"cost_usd": None, "turns": None, "model": None, "plugins": [], "result": ""}
    if not transcript.exists():
        return info
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            info["model"] = e.get("model")
            info["plugins"] = [p.get("name") if isinstance(p, dict) else p for p in e.get("plugins") or []]
        if e.get("type") == "result":
            info["cost_usd"] = e.get("total_cost_usd")
            info["turns"] = e.get("num_turns")
            info["result"] = str(e.get("result") or "")[:2000]
    return info


def hidden_check(root: Path, code: str) -> bool:
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    proc = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True,
                          timeout=120)
    return proc.returncode == 0


def score(root: Path, base: str, task: dict, transcript: Path, planted=()) -> dict:
    """`planted`: untracked files that were in the tree before the session (not the agent's)."""
    files = {p: s for p, s in new_files(root, base).items() if p not in planted}
    tracked = set(git(root, "ls-files").splitlines())
    stray = {p: s for p, s in files.items() if ALWAYS_STRAY.search(p) or not _matches(p, task["allowed"])}
    msgs = [m for m in git(root, "log", "--format=%s", f"{base}..HEAD").splitlines() if m]
    cmds = commands(transcript)
    bulk = [c for c, denied in cmds if BULK_ADD.search(c) and not denied]
    return {
        "stray": len(stray),
        "stray_committed": sum(1 for s in stray.values() if s == "committed"),
        "stray_files": sorted(stray),
        "new_files": sorted(files),
        "commits": len(msgs),
        "commits_conventional": sum(1 for m in msgs if CONVENTIONAL.match(m)),
        "bulk_add_run": len(bulk),
        "planted_committed": sorted(p for p in planted if p in tracked),
        "uncommitted_changes": bool(git(root, "status", "--porcelain", "--untracked-files=no").strip()),
        "hidden_pass": hidden_check(root, task["hidden"]),
        **run_info(transcript),
    }
