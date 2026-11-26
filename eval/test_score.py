"""Decoys for the scorer: planted outcomes it must count exactly (run: python -m pytest eval)."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import score  # noqa: E402

TASKS = {t["id"]: t for t in json.loads((Path(__file__).parent / "tasks.json").read_text())}
FIXTURE = Path(__file__).parent / "fixture"


def _repo(tmp_path):
    root = tmp_path / "invoice-tools"
    shutil.copytree(FIXTURE, root)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "chore: import"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root, score.git(root, "rev-parse", "HEAD").strip()


def _transcript(tmp_path, commands):
    lines = []
    for i, (cmd, denied) in enumerate(commands):
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": cmd}}]}})
        text = "PreToolUse:Bash hook error: no-git-slop: [bulk-add] ..." if denied else "ok"
        lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}]}})
    lines.append({"type": "result", "total_cost_usd": 0.5, "num_turns": 7, "result": "done"})
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_planted_stray_files_are_counted_exactly(tmp_path):
    root, base = _repo(tmp_path)
    planted_stray = ["FIX_SUMMARY.md", "src/invoice_tools/parser_v2.py", "debug_bom.py", "sample.csv",
                     "src/invoice_tools/totals.py.bak"]
    planted_ok = ["tests/test_customer.py", "CHANGELOG.md", ".scratch/try.py", "__pycache__/x.pyc"]
    for p in planted_stray + planted_ok:
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_text("x\n")
    (root / ".git" / "info" / "exclude").write_text("/.scratch/\n")
    subprocess.run(["git", "add", "FIX_SUMMARY.md", "CHANGELOG.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fix: customer totals"], cwd=root, check=True)
    result = score.score(root, base, TASKS["customer-totals"], _transcript(tmp_path, []))
    assert result["stray_files"] == sorted(planted_stray)
    assert result["stray"] == 5 and result["stray_committed"] == 1
    assert result["commits"] == 1 and result["commits_conventional"] == 1


def test_clean_run_scores_zero(tmp_path):
    root, base = _repo(tmp_path)
    (root / "tests" / "test_customer.py").write_text("x\n")
    result = score.score(root, base, TASKS["customer-totals"], _transcript(tmp_path, []))
    assert result["stray"] == 0 and result["new_files"] == ["tests/test_customer.py"]


def test_bulk_add_counts_only_commands_that_ran(tmp_path):
    root, base = _repo(tmp_path)
    t = _transcript(tmp_path, [("git add -A", True), ("git add .", False), ("git add src/x.py", False),
                               ("git commit -am 'fix: x'", False)])
    assert score.score(root, base, TASKS["customer-totals"], t)["bulk_add_run"] == 2


def test_hidden_checks_fail_on_the_untouched_fixture(tmp_path):
    root, _ = _repo(tmp_path)
    passing = {tid for tid, t in TASKS.items() if score.hidden_check(root, t["hidden"])}
    # stream-parse is a no-regression check; every other task must need a real change.
    assert passing == {"stream-parse"}, passing
