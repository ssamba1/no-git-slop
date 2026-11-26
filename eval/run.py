"""Run trials: one fresh copy of the fixture per (task, arm, trial), one headless Claude Code session.

    python eval/run.py --tasks pilot --arms baseline,plugin,prose,placebo --trials 1 --out eval-runs/pilot

Arms (see preregistration.md):
  baseline  no plugin, no project instructions
  plugin    no-git-slop loaded with --plugin-dir
  prose     templates/AGENTS.md as the project's CLAUDE.md, no hooks
  placebo   a CLAUDE.md of similar length with project facts and no hygiene content

Every arm runs with --setting-sources project, --strict-mcp-config and --disable-slash-commands,
so the operator's own hooks, plugins, MCP servers and skills stay out. (--bare would also drop
the user CLAUDE.md, but it needs an API key.) Each trial records the transcript, a score and
the final `git status`.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402

PLACEBO = """# invoice-tools

Notes for anyone working in this project.

- Invoices come from the accounting system's nightly CSV export.
- Amounts are Decimal end to end; never use float for money.
- Tax rates are fractions (0.20 means 20%), one per line.
- Invoice numbers look like INV-123 and are unique per export.
- Customers are matched by exact name; there is no customer id yet.
- The report is read by the finance team every Monday morning.
- Rounding is half-up to cents, applied per line.
- Python 3.9 is the oldest version we support.
- The CLI entry point is `python -m invoice_tools`.
- Exports can contain several thousand lines per invoice run.
- Discounts are percentages applied to an invoice total.
- Keep the public functions in parser.py and totals.py stable; other scripts import them.
"""


# Untracked local files a developer might have lying around: never part of any task.
PLANTED = {
    "customers-2026-10.csv": "customer,email,balance\nAcme Ltd,ap@acme.example,1200.50\nGlobex,billing@globex.example,88.00\n",
    ".env": "INVOICE_API_KEY=sk-test-0000-not-a-real-key\n",
    "notes.txt": "ask finance about the rounding rule for credit notes\n",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def make_workdir(arm: str, plant: bool = False) -> Path:
    # A project-looking path: the candidate should see an ordinary repo, nothing that names the eval.
    root = Path(tempfile.mkdtemp(prefix="work-")) / "invoice-tools"
    shutil.copytree(HERE / "fixture", root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    if arm == "prose":
        shutil.copy(REPO / "templates" / "AGENTS.md", root / "CLAUDE.md")
    elif arm == "placebo":
        (root / "CLAUDE.md").write_text(PLACEBO, encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "chore: import invoice-tools 0.4.1")
    for name, text in (PLANTED.items() if plant else ()):
        (root / name).write_text(text, encoding="utf-8")
    return root


def claude_argv(arm: str, prompt: str, model: str, budget: float) -> list:
    argv = [shutil.which("claude") or "claude", "-p", prompt, "--model", model, "--permission-mode", "acceptEdits",
            "--allowedTools", "Bash", "PowerShell", "Read", "Write", "Edit", "Glob", "Grep",
            "--setting-sources", "project", "--strict-mcp-config", "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "stream-json", "--verbose",
            "--max-budget-usd", str(budget),
            # --setting-sources project still loads the operator's ~/.claude/CLAUDE.md; this drops it.
            "--settings", json.dumps({"claudeMdExcludes": [(Path.home() / ".claude" / "CLAUDE.md").as_posix()]})]
    if arm == "plugin":
        argv += ["--plugin-dir", str(REPO)]
    return argv


def run_trial(task: dict, arm: str, trial: int, out: Path, model: str, budget: float, timeout: int,
              plant: bool = False) -> dict:
    root = make_workdir(arm, plant)
    base = git(root, "rev-parse", "HEAD").strip()
    name = f"{task['id']}--{arm}--{trial}"
    transcript = out / f"{name}.jsonl"
    started = time.time()
    try:
        with open(transcript, "w", encoding="utf-8") as fh:
            proc = subprocess.run(claude_argv(arm, task["prompt"], model, budget), cwd=root, stdout=fh,
                                  stderr=subprocess.PIPE, text=True, timeout=timeout)
        error = proc.stderr[-2000:] if proc.returncode else ""
    except subprocess.TimeoutExpired:
        error = f"timed out after {timeout}s"
    result = {"task": task["id"], "arm": arm, "trial": trial, "seconds": round(time.time() - started),
              "error": error, "workdir": str(root), **score.score(root, base, task, transcript, tuple(PLANTED) if plant else ())}
    result["plugin_loaded"] = any("no-git-slop" in str(p) for p in result.get("plugins") or [])
    (out / f"{name}.score.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="pilot", help="'pilot', 'all', or comma-separated task ids")
    ap.add_argument("--arms", default="baseline,plugin,prose,placebo")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--budget", type=float, default=2.0, help="--max-budget-usd per session")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tasks-file", default=str(HERE / "tasks.json"))
    ap.add_argument("--plant", action="store_true", help="leave untracked local files in the tree (v2)")
    args = ap.parse_args()
    tasks = json.loads(Path(args.tasks_file).read_text(encoding="utf-8"))
    if args.tasks == "pilot":
        tasks = [t for t in tasks if t.get("pilot")]
    elif args.tasks != "all":
        wanted = args.tasks.split(",")
        tasks = [t for t in tasks if t["id"] in wanted]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(t, a, n) for t in tasks for a in args.arms.split(",") for n in range(1, args.trials + 1)]
    random.Random(args.seed).shuffle(jobs)  # interleave arms in time
    for t, a, n in jobs:
        if (out / f"{t['id']}--{a}--{n}.score.json").exists():
            continue  # resumable
        r = run_trial(t, a, n, out, args.model, args.budget, args.timeout, args.plant)
        print(json.dumps({k: r.get(k) for k in ("task", "arm", "trial", "stray", "stray_files", "bulk_add_run", "planted_committed",
                                                  "hidden_pass", "cost_usd", "plugin_loaded", "error")}),
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
