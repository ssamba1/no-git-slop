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
