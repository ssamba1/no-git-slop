"""Optional external checkers (duplication, dead code, code-content slop).

no-git-slop doesn't reimplement these. If a tool is installed it runs; if
not, it reports `unavailable` instead of pretending the check passed.
Command templates are configurable in .no-git-slop.json under
"integrations"; set a tool to "off" to skip it.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List

from . import gitutil

# Defaults are conservative starting points; adjust them to your project.
DEFAULTS: Dict[str, str] = {
    "jscpd": "jscpd --silent --min-lines 8 --threshold 0 {files}",
    "vulture": "vulture --min-confidence 80 {py_files}",
    "knip": "knip --no-progress",
    "aislop": "aislop scan --staged",
}
_CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".rb", ".java",
             ".kt", ".swift", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".scala"}


def _changed_files(root: Path) -> List[str]:
    out = gitutil._split0(gitutil._git(["diff", "--name-only", "-z", "HEAD"], root))
    out += gitutil.untracked_files(root)
    seen, files = set(), []
    for p in out:
        if p not in seen and (root / p).is_file():
            seen.add(p)
            files.append(p)
    return files


def _executable(root: Path, name: str) -> str:
    local = root / "node_modules" / ".bin" / name
    if local.exists():
        return str(local)
    return shutil.which(name) or ""


def run_all(root: Path, configured: Dict[str, str], timeout: int = 300) -> List[dict]:
    """Run each integration. Returns [{name, status, output}] with status ran|unavailable|failed|skipped."""
    tools = dict(DEFAULTS)
    tools.update({k: v for k, v in configured.items() if isinstance(v, str)})
    files = [f for f in _changed_files(root) if Path(f).suffix.lower() in _CODE_EXT]
    py_files = [f for f in files if f.endswith(".py")]
    results = []
    for name, template in tools.items():
        if template == "off":
            continue
        try:
            argv_template = shlex.split(template)
        except ValueError:
            results.append({"name": name, "status": "failed", "output": "bad command template"})
            continue
        if not argv_template:
            continue
        exe = _executable(root, argv_template[0])
        if not exe:
            results.append({"name": name, "status": "unavailable", "output": ""})
            continue
        argv = [exe]
        empty = False
        for tok in argv_template[1:]:
            if tok == "{files}":
                argv += files
                empty = empty or not files
            elif tok == "{py_files}":
                argv += py_files
                empty = empty or not py_files
            else:
                argv.append(tok)
        if empty:
            results.append({"name": name, "status": "skipped", "output": "no changed files to check"})
            continue
        try:
            proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            results.append({"name": name, "status": "failed", "output": str(exc)})
            continue
        output = (proc.stdout + proc.stderr).strip()
        results.append({"name": name, "status": "ran", "exit": proc.returncode,
                        "output": "\n".join(output.splitlines()[-25:])})
    return results


def run_verify(root: Path, command: str, timeout: int) -> dict:
    """Run the project's verify command (tests/lint). No shell: use `bash -c '...'` if needed."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return {"status": "failed", "exit": -1, "output": "verify command could not be parsed"}
    if not argv:
        return {"status": "skipped", "exit": 0, "output": ""}
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "exit": -1, "output": f"timed out after {timeout}s"}
    except OSError as exc:
        return {"status": "unavailable", "exit": -1, "output": str(exc)}
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return {"status": "ran", "exit": proc.returncode, "output": "\n".join(output.splitlines()[-30:])}
