import json
import subprocess

import pytest

from helpers import git, hook, make_repo, run_cli, write_event


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def test_check_json_contract(repo):
    (repo / "NOTES_v2.md").write_text("x\n")
    (repo / "src").mkdir()
    (repo / "src" / "ok.py").write_text("x = 1\n")
    out = run_cli(repo, "check", "--json")
    data = json.loads(out.stdout)
    assert out.returncode == 1 and data["verdict"] == "fail"
    [finding] = data["findings"]
    assert finding["path"] == "NOTES_v2.md" and finding["rule"] == "report-doc"
    assert finding["fix"]


def test_check_deep_reports_tool_status(repo):
    out = run_cli(repo, "check", "--deep", "--json")
    statuses = {r["name"]: r["status"] for r in json.loads(out.stdout)["integrations"]}
    assert set(statuses) >= {"jscpd", "vulture", "knip", "aislop"}
    assert all(s in ("ran", "unavailable", "skipped", "failed") for s in statuses.values())


def test_check_deep_runs_project_local_tool(repo):
    # A fake jscpd in node_modules/.bin, written as the .cmd shim on Windows and a script elsewhere.
    import sys
    bin_dir = repo / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (repo / ".gitignore").write_text("node_modules/\n")
    (repo / "new.py").write_text("x = 1\n")
    if sys.platform == "win32":
        (bin_dir / "jscpd").write_text("#!/bin/sh\necho sh-shim\n")  # what npm also writes; not runnable here
        (bin_dir / "jscpd.cmd").write_text("@echo \x1b[1mfake jscpd\x1b[22m %*\r\n")
    else:
        shim = bin_dir / "jscpd"
        shim.write_text("#!/bin/sh\nprintf '\\033[1mfake jscpd\\033[22m %s\\n' \"$*\"\n")
        shim.chmod(0o755)
    out = run_cli(repo, "check", "--deep", "--json")
    [jscpd] = [r for r in json.loads(out.stdout)["integrations"] if r["name"] == "jscpd"]
    assert jscpd["status"] == "ran", jscpd
    assert "fake jscpd" in jscpd["output"] and "new.py" in jscpd["output"]
    assert "\x1b" not in jscpd["output"]


def test_explain(repo):
    out = run_cli(repo, "explain", "bulk-add")
    assert out.returncode == 0 and "why:" in out.stdout and "fix:" in out.stdout
    listing = run_cli(repo, "explain").stdout
    assert "report-doc" in listing and "push-unclean" in listing
    assert run_cli(repo, "explain", "nope").returncode == 1


def test_stats_after_decisions(repo):
    hook("write", write_event(repo, "SUMMARY.md"), repo)
    hook("write", write_event(repo, "debug_x.py"), repo)
    out = run_cli(repo, "stats")
    assert "report-doc" in out.stdout and "throwaway-script" in out.stdout
    data = json.loads(run_cli(repo, "stats", "--json").stdout)
    assert len(data) == 2


def test_index_lists_signatures(repo):
    (repo / "pkg").mkdir()
    (repo / "pkg" / "util.py").write_text("def parse_date(s, fmt):\n    pass\n\nclass Cache:\n"
                                          "    def get(self, key):\n        pass\n")
    (repo / "web.ts").write_text("export function slugify(text: string) {}\n"
                                 "export const clamp = (n: number, lo: number) => n\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: add utils")
    out = run_cli(repo, "index").stdout
    assert "parse_date(s, fmt)" in out and "Cache.get(self, key)" in out
    assert "slugify(text: string)" in out and "clamp" in out
    assert "parse_date" in run_cli(repo, "index", "pkg").stdout
    assert "slugify" not in run_cli(repo, "index", "pkg").stdout


def test_check_patch_codex_and_git_formats(repo):
    codex = "*** Begin Patch\n*** Add File: FIX_SUMMARY.md\n+done\n*** Update File: app.py\n@@\n-a\n+b\n*** End Patch\n"
    out = run_cli(repo, "check-patch", stdin=codex)
    assert out.returncode == 1 and "FIX_SUMMARY.md" in out.stdout
    diff = ("diff --git a/src/cache.py b/src/cache.py\nnew file mode 100644\n--- /dev/null\n"
            "+++ b/src/cache.py\n@@ -0,0 +1 @@\n+x = 1\n")
    assert run_cli(repo, "check-patch", stdin=diff).returncode == 0


def test_commits_flags_churn(repo):
    for i in range(2):
        (repo / f"f{i}.py").write_text("x\n")
        git(repo, "add", f"f{i}.py")
        git(repo, "commit", "-qm", "fix(api): handle timeouts")
    out = run_cli(repo, "commits", "HEAD~2..HEAD")
    assert "repeats" in out.stdout


def test_pr_command_on_feature_branch(repo):
    git(repo, "switch", "-q", "-c", "feature")
    ok = run_cli(repo, "pr", "--title", "feat: add x", "--body", "Adds x. Tested locally.")
    assert ok.returncode == 0
    bad = run_cli(repo, "pr", "--title", "feat: add x", "--body", "Certainly! Here's a summary 🚀✨🔥")
    assert bad.returncode == 1 and "pr-residue" in bad.stdout and "pr-emoji" in bad.stdout


def test_archive_moves_plan_with_date(repo):
    plans = repo / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "login.md").write_text("# plan\n")
    out = run_cli(repo, "archive", "docs/plans/login.md")
    assert out.returncode == 0
    archived = list((plans / "archive").iterdir())
    assert len(archived) == 1 and archived[0].name.endswith("-login.md")


def test_init_hooks_enforce_at_git_level(repo):
    out = run_cli(repo, "init", "--hooks")
    assert "Installed git pre-commit hook" in out.stdout
    assert ".scratch/" in (repo / ".gitignore").read_text()
    (repo / "DEBUG_NOTES.md").write_text("x\n")
    git(repo, "add", "DEBUG_NOTES.md")
    blocked = subprocess.run(["git", "commit", "-m", "docs: add notes"], cwd=repo,
                             capture_output=True, text=True)
    assert blocked.returncode != 0 and "DEBUG_NOTES.md" in blocked.stdout + blocked.stderr
    git(repo, "rm", "-q", "--cached", "DEBUG_NOTES.md")
    (repo / "DEBUG_NOTES.md").unlink()
    git(repo, "add", ".gitignore")
    bad_msg = subprocess.run(["git", "commit", "-m", "stuff"], cwd=repo, capture_output=True, text=True)
    assert bad_msg.returncode != 0
    good = subprocess.run(["git", "commit", "-m", "chore: ignore scratch folder"], cwd=repo,
                          capture_output=True, text=True)
    assert good.returncode == 0, good.stdout + good.stderr


def test_init_respects_existing_hooks(repo):
    hook_file = repo / ".git" / "hooks" / "pre-commit"
    hook_file.write_text("#!/bin/sh\necho mine\n")
    out = run_cli(repo, "init", "--hooks")
    assert "Skipped pre-commit" in out.stdout
    assert "mine" in hook_file.read_text()


def test_doctor(repo):
    (repo / ".specify").mkdir()
    out = run_cli(repo, "doctor").stdout
    assert "spec-kit" in out and "scratch:" in out and "tool:" in out


def test_session_start_excludes_scratch_locally(repo):
    hook("session-start", {"cwd": str(repo), "session_id": "x"}, repo)
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert "/.scratch/" in exclude
    (repo / ".scratch").mkdir()
    (repo / ".scratch" / "t.py").write_text("x\n")
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("args", [("commits", "nosuch..HEAD"), ("check", "--range", "nosuch..HEAD"),
                                  ("push-check", "--range", "nosuch..HEAD")])
def test_bad_range_is_an_error(repo, args):
    out = run_cli(repo, *args)
    assert out.returncode != 0 and "nosuch" in out.stderr


def test_deny_output_survives_a_cp1252_pipe(repo):
    import os
    import sys
    from helpers import CLI
    (repo / "FIX_NOTES.md").write_text("x\n")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    out = subprocess.run([sys.executable, str(CLI), "check"], cwd=repo, capture_output=True, env=env)
    assert out.returncode == 1 and b"FIX_NOTES.md" in out.stdout and b"Traceback" not in out.stderr


def test_archive_rejects_paths_outside_repo(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("o") / "a.txt"
    outside.write_text("x")
    out = run_cli(repo, "archive", str(outside))
    assert out.returncode == 1 and not (outside.parent / "archive").exists()


def test_init_hooks_from_python_m_work(repo):
    import os
    import sys
    from helpers import CLI
    env = {**os.environ, "PYTHONPATH": str(CLI.parents[1] / "src")}
    subprocess.run([sys.executable, "-m", "no_git_slop", "init", "--hooks"], cwd=repo, env=env,
                   check=True, capture_output=True)
    (repo / "b.py").write_text("x = 1\n")
    git(repo, "add", "b.py")
    out = subprocess.run(["git", "commit", "-m", "did stuff"], cwd=repo, env=env,
                         capture_output=True, text=True)
    assert out.returncode != 0 and "ImportError" not in out.stderr and "commit-format" in out.stderr
