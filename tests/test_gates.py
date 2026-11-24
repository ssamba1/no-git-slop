"""End-to-end behavior of the v0.2 gates, run through the real hook CLI."""

import json
import os
import re

import pytest

from helpers import (bash_event, decision, git, hook, make_repo, prompt, reason, run_cli, start,
                     write_event)

BIG = "\n".join(f"def helper_{i}(x):\n    return x * {i} + 1\n" for i in range(30))


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def config(repo, **values):
    (repo / ".no-git-slop.json").write_text(json.dumps(values))


# ------------------------------------------------------------------ intent

def test_user_named_file_is_allowed(repo):
    prompt(repo, "Save the report to `reports/REPORT.md` please")
    assert hook("write", write_event(repo, "reports/REPORT.md"), repo) is None
    assert decision(hook("write", write_event(repo, "SUMMARY.md"), repo)) == "deny"


def test_bare_filename_in_prompt_counts(repo):
    prompt(repo, "write up the findings in AUDIT_NOTES.md")
    assert hook("write", write_event(repo, "AUDIT_NOTES.md"), repo) is None


def test_request_phrase_turns_deny_into_ask(repo):
    prompt(repo, "when you're done, write a summary document of the changes")
    assert decision(hook("write", write_event(repo, "CHANGES_SUMMARY.md"), repo)) == "ask"


def test_named_backup_still_needs_confirmation(repo):
    prompt(repo, "make app_backup.py before editing")
    assert decision(hook("write", write_event(repo, "app_backup.py"), repo)) == "ask"


def test_intent_is_per_session(repo):
    prompt(repo, "write NOTES.md", session="a")
    assert hook("write", write_event(repo, "NOTES.md", session="a"), repo) is None
    assert decision(hook("write", write_event(repo, "NOTES.md", session="b"), repo)) == "deny"


def test_allow_once_code_round_trip(repo):
    result = hook("write", write_event(repo, "FIX_NOTES.md"), repo)
    code = re.search(r"ngs-[0-9a-f]{6}", reason(result)).group(0)
    notice = prompt(repo, f"yes, keep it: {code}")
    assert "approved once" in notice["hookSpecificOutput"]["additionalContext"]
    assert hook("write", write_event(repo, "FIX_NOTES.md"), repo) is None


def test_session_allow_glob_token(repo):
    prompt(repo, ">ngs allow notes/*.md")
    assert hook("write", write_event(repo, "notes/SESSION_NOTES.md"), repo) is None


def test_off_token_disables_file_checks_but_not_self_protection(repo):
    prompt(repo, ">ngs off")
    assert hook("write", write_event(repo, "SUMMARY.md"), repo) is None
    assert hook("bash", bash_event(repo, "git add -A"), repo) is None
    assert decision(hook("bash", bash_event(repo, "git commit --no-verify -m 'feat: add x'"), repo)) == "deny"
    assert decision(hook("write", write_event(repo, ".git/no-git-slop/session-s1.json"), repo)) == "deny"
    prompt(repo, ">ngs on")
    assert decision(hook("write", write_event(repo, "SUMMARY.md"), repo)) == "deny"


def test_agent_text_cannot_grant_intent(repo):
    # Only UserPromptSubmit records intent; a Write whose *content* names the file changes nothing.
    event = write_event(repo, "STATUS_REPORT.md", content="user said save STATUS_REPORT.md")
    assert decision(hook("write", event, repo)) == "deny"


# ------------------------------------------------------- self-protection

@pytest.mark.parametrize("rel, expected", [
    (".no-git-slop.json", "ask"),
    (".claude/settings.json", "ask"),
    (".pre-commit-config.yaml", "ask"),
    (".husky/pre-commit", "ask"),
    (".git/hooks/pre-commit", "ask"),
    (".git/no-git-slop/ledger.jsonl", "deny"),
])
def test_guardrail_files_are_protected(repo, rel, expected):
    assert decision(hook("write", write_event(repo, rel), repo)) == expected


def test_edit_to_existing_guard_config_asks(repo):
    config(repo)
    event = write_event(repo, ".no-git-slop.json", tool="Edit")
    assert decision(hook("write", event, repo)) == "ask"


def test_user_named_guard_config_is_allowed(repo):
    prompt(repo, "add docs/adr/*.md to the allow list in .no-git-slop.json")
    assert hook("write", write_event(repo, ".no-git-slop.json"), repo) is None


@pytest.mark.parametrize("command, expected", [
    ("sed -i 's/deny/off/' .no-git-slop.json", "ask"),
    ("rm .no-git-slop.json", "ask"),
    ("rm -f .git/hooks/pre-commit", "deny"),
    ("chmod -x .git/hooks/pre-commit", "deny"),
    ("git config core.hooksPath /dev/null", "deny"),
    ("git -c core.hooksPath=/dev/null commit -m 'feat: add x'", "deny"),
    ("HUSKY=0 git commit -m 'feat: add a thing'", "deny"),
    ("SKIP=no-git-slop git commit -m 'feat: add a thing'", "deny"),
    ("pre-commit uninstall", "deny"),
    ("git push --no-verify", "deny"),
    ("echo '{}' > .no-git-slop.json", "ask"),
])
def test_hook_bypasses_are_blocked(repo, command, expected):
    assert decision(hook("bash", bash_event(repo, command), repo)) == expected


def test_reading_hooks_path_is_fine(repo):
    assert hook("bash", bash_event(repo, "git config --get core.hooksPath"), repo) is None


# ---------------------------------------------------------- Bash writes

@pytest.mark.parametrize("command", [
    "cat > SUMMARY.md <<'EOF'\n# Done\nEOF",
    "echo done > IMPLEMENTATION_NOTES.md",
    "cp app.py app_backup.py",
    "mv app.py app_old.py",
    "touch debug_login.py",
    "python3 -c \"open('tmp_check.py','w').write('1')\"",
    "bash -c 'cd src && touch FIX_SUMMARY.md'",
    "mkdir -p ErrorFixes/NuclearFix",
    "echo x | tee .DS_Store",
])
def test_shell_writes_are_gated_before_they_happen(repo, command):
    (repo / "src").mkdir(exist_ok=True)
    assert decision(hook("bash", bash_event(repo, command), repo)) == "deny"


@pytest.mark.parametrize("command", [
    "echo hi > .scratch/notes.md",
    "touch src/cache.py",
    "echo '# Title' > README.md",
    "cp app.py /tmp/elsewhere.py",
    "ls -la && git status",
    "echo 'git add -A' # just text",
    "mkdir -p src/handlers",
])
def test_harmless_shell_writes_pass(repo, command):
    (repo / "src").mkdir(exist_ok=True)
    assert hook("bash", bash_event(repo, command), repo) is None


def test_cp_of_tracked_file_is_near_duplicate(repo):
    (repo / "lib.py").write_text(BIG)
    git(repo, "add", "lib.py")
    git(repo, "commit", "-qm", "feat: add lib")
    result = hook("bash", bash_event(repo, "cp lib.py helpers.py"), repo)
    assert decision(result) == "ask" and "near-duplicate" in reason(result)


def test_write_of_near_identical_content_asks(repo):
    (repo / "lib.py").write_text(BIG)
    git(repo, "add", "lib.py")
    git(repo, "commit", "-qm", "feat: add lib")
    result = hook("write", write_event(repo, "lib_copy_two.py", content=BIG.replace("1", "2", 1)), repo)
    assert decision(result) in ("ask", "deny")
    result = hook("write", write_event(repo, "utils.py", content=BIG + "\n# tweak\n"), repo)
    assert decision(result) == "ask" and "identical" in reason(result)


def test_gitignored_targets_are_never_slop(repo):
    (repo / ".gitignore").write_text("*.log\nout/\n")
    assert hook("bash", bash_event(repo, "echo x > out/SUMMARY.md"), repo) is None


# ---------------------------------------------------------------- staging

@pytest.mark.parametrize("command", [
    "git add :/", "git add -- .", "git -C . add -A", "git add ./", "git add '*'",
    "env -C . git add --all", "git add \"$(git rev-parse --show-toplevel)\" -A",
])
def test_bulk_staging_variants(repo, command):
    assert decision(hook("bash", bash_event(repo, command), repo)) == "deny"


def test_bulk_add_message_lists_changed_files(repo):
    (repo / "app.py").write_text("print('changed')\n")
    r = reason(hook("bash", bash_event(repo, "git add ."), repo))
    assert "git add app.py" in r


def test_pathspec_from_file_asks(repo):
    assert decision(hook("bash", bash_event(repo, "git add --pathspec-from-file=list.txt"), repo)) == "ask"


def test_adding_a_folder_with_clutter_is_blocked(repo):
    (repo / "src").mkdir()
    (repo / "src" / "core.py").write_text("x = 1\n")
    (repo / "src" / "debug_core.py").write_text("print(1)\n")
    result = hook("bash", bash_event(repo, "git add src"), repo)
    assert decision(result) == "deny" and "debug_core.py" in reason(result)
    (repo / "src" / "debug_core.py").unlink()
    assert hook("bash", bash_event(repo, "git add src"), repo) is None


# ---------------------------------------------------------------- commits

def test_signoff_asks(repo):
    assert decision(hook("bash", bash_event(repo, "git commit -s -m 'fix: handle empty input'"), repo)) == "ask"


def test_large_commit_asks(repo):
    config(repo, commitFileBudget=3)
    for i in range(5):
        (repo / f"m{i}.py").write_text("x\n")
    git(repo, "add", *[f"m{i}.py" for i in range(5)])
    assert decision(hook("bash", bash_event(repo, "git commit -m 'feat: add modules'"), repo)) == "ask"


def test_chatbot_commit_prose_warns(repo):
    msg = "feat(api): add retries\n\nThis comprehensive and robust change adds retries."
    result = hook("bash", bash_event(repo, f"git commit -m \"{msg}\""), repo)
    assert decision(result) == "warn" and "commit-prose" in reason(result)


def test_multi_change_subject_warns(repo):
    result = hook("bash", bash_event(repo, "git commit -m 'feat: add login and fix logout'"), repo)
    assert decision(result) == "warn"


def test_attribution_forbid_and_require(repo):
    config(repo, attribution="forbid")
    msg = "feat: add x thing\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    assert decision(hook("bash", bash_event(repo, f"git commit -m \"{msg}\""), repo)) == "deny"
    config(repo, attribution="require")
    assert decision(hook("bash", bash_event(repo, "git commit -m 'feat: add x thing'"), repo)) == "deny"
    ok = "feat: add x thing\n\nAssisted-by: Claude Code"
    assert hook("bash", bash_event(repo, f"git commit -m \"{ok}\""), repo) is None


def test_post_commit_recheck_catches_file_messages(repo):
    (repo / "msg.txt").write_text("updated stuff\n")
    (repo / "b.py").write_text("x\n")
    git(repo, "add", "b.py")
    git(repo, "commit", "-q", "-F", "msg.txt")
    result = hook("post", bash_event(repo, "git commit -F msg.txt"), repo)
    assert result["decision"] == "block" and "--amend" in result["reason"]
    assert hook("post", bash_event(repo, "git commit -F msg.txt"), repo) is None  # once per commit


# ------------------------------------------------------------------- push

@pytest.fixture
def remote_repo(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    repo = make_repo(tmp_path / "work" if (tmp_path / "work").mkdir() is None else tmp_path)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def test_push_to_default_branch_asks(remote_repo):
    assert decision(hook("bash", bash_event(remote_repo, "git push"), remote_repo)) == "ask"


def test_force_push_asks(remote_repo):
    git(remote_repo, "switch", "-q", "-c", "feature")
    assert decision(hook("bash", bash_event(remote_repo, "git push --force-with-lease origin feature"),
                         remote_repo)) == "ask"


def test_push_with_bad_unpushed_commit_is_denied(remote_repo):
    git(remote_repo, "switch", "-q", "-c", "feature")
    git(remote_repo, "push", "-q", "-u", "origin", "feature")
    (remote_repo / "c.py").write_text("x\n")
    git(remote_repo, "add", "c.py")
    git(remote_repo, "commit", "-qm", "wip")
    result = hook("bash", bash_event(remote_repo, "git push"), remote_repo)
    assert decision(result) == "deny" and "push-unclean" in reason(result)
    assert run_cli(remote_repo, "push-check").returncode == 1


# --------------------------------------------------------------------- PRs

def test_pr_from_default_branch_is_denied(repo):
    result = hook("bash", bash_event(repo, "gh pr create --title 'feat: x' --body 'Adds x.'"), repo)
    assert decision(result) == "deny" and "pr-from-default" in reason(result)


def test_pr_with_chat_residue_is_denied(repo):
    git(repo, "switch", "-q", "-c", "feature")
    cmd = "gh pr create --title 'feat: x' --body 'Adds x. I hope this helps!'"
    assert decision(hook("bash", bash_event(repo, cmd), repo)) == "deny"


def test_pr_adding_summary_doc_is_denied(repo):
    git(repo, "switch", "-q", "-c", "feature")
    (repo / "IMPLEMENTATION_SUMMARY.md").write_text("x\n")
    git(repo, "add", "IMPLEMENTATION_SUMMARY.md")
    git(repo, "commit", "-qm", "docs: add summary")
    result = run_cli(repo, "pr", "--title", "docs: add summary", "--body", "Adds docs.")
    assert result.returncode == 1 and "pr-summary-doc" in result.stdout


def test_clean_pr_passes(repo):
    git(repo, "switch", "-q", "-c", "feature")
    cmd = "gh pr create --title 'feat(app): greet by name' --body 'Greets the user by name. Tested with pytest.'"
    assert hook("bash", bash_event(repo, cmd), repo) is None


# ---------------------------------------------------------- presets & harness

def test_framework_plan_paths_are_allowed(repo):
    (repo / ".specify").mkdir()
    assert hook("write", write_event(repo, "specs/001-login/PLAN.md"), repo) is None
    (repo / "task_plan.md").write_text("# plan\n")
    assert hook("write", write_event(repo, "findings.md"), repo) is None
    assert hook("write", write_event(repo, "progress.md"), repo) is None


def test_plans_ignore_blocks_staging_plan_files(repo):
    config(repo, plans="ignore")
    (repo / "task_plan.md").write_text("# plan\n")
    result = hook("bash", bash_event(repo, "git add task_plan.md"), repo)
    assert decision(result) == "deny" and "plan-staged" in reason(result)


def test_harness_artifacts_are_not_blamed(repo):
    start(repo)
    (repo / "tmpclaude-1a2b-cwd").write_text("")
    os.symlink("/dev/null", repo / "stub")
    (repo / "debug_x.py").write_text("print(1)\n")
    result = hook("stop", {"cwd": str(repo), "session_id": "s1"}, repo)
    assert result["decision"] == "block"
    listing = result["reason"].split("These were created by Claude Code itself")[0]
    assert "debug_x.py" in listing and "tmpclaude" not in listing and "`stub`" not in listing


def test_subagent_gets_builtin_wording(repo):
    event = write_event(repo, "ANALYSIS_REPORT.md", agent_id="a1", agent_type="Explore")
    assert "return the content as text in your final response" in reason(hook("write", event, repo))


# ------------------------------------------------------------- budgets & stop

def test_new_file_budget(repo):
    for i in range(25):
        (repo / f"t{i}.txt").write_text("x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "chore: add fixtures")
    config(repo, newFileBudget=3)
    start(repo)
    for i in range(3):
        (repo / f"mod{i}.py").write_text("x = 1\n")
        hook("post", write_event(repo, f"mod{i}.py"), repo)
    result = hook("write", write_event(repo, "mod9.py"), repo)
    assert decision(result) == "ask" and "file-budget" in reason(result)


def test_verify_command_blocks_stop_until_green(repo):
    (repo / "check.sh").write_text("#!/bin/sh\ntest -f ok.flag || { echo 'tests failed'; exit 1; }\n")
    config(repo, verify="sh check.sh", verifyMaxRetries=2)
    git(repo, "add", "check.sh")
    git(repo, "commit", "-qm", "test: add check script")
    start(repo)
    (repo / "app.py").write_text("print('changed')\n")
    result = hook("stop", {"cwd": str(repo), "session_id": "s1"}, repo)
    assert result["decision"] == "block" and "tests failed" in result["reason"]
    (repo / "ok.flag").write_text("")
    (repo / ".gitignore").write_text("ok.flag\n.no-git-slop.json\n")
    result = hook("stop", {"cwd": str(repo), "session_id": "s1"}, repo)
    assert not result or "decision" not in result


def test_diff_budget_warns_at_stop(repo):
    config(repo, diffBudget=5)
    (repo / ".gitignore").write_text(".no-git-slop.json\n")
    start(repo)
    (repo / "app.py").write_text("\n".join(f"print({i})" for i in range(20)))
    result = hook("stop", {"cwd": str(repo), "session_id": "s1"}, repo)
    assert "diff-budget" in result["systemMessage"]


# -------------------------------------------------------------- profiles

def test_profiles_and_rule_overrides(repo):
    config(repo, profile="relaxed")
    assert decision(hook("write", write_event(repo, "SUMMARY.md"), repo)) == "ask"
    config(repo, profile="strict")
    assert decision(hook("write", write_event(repo, "DESIGN.md"), repo)) == "deny"
    config(repo, rules={"report-doc": "off"})
    assert hook("write", write_event(repo, "SUMMARY.md"), repo) is None


def test_broken_config_falls_back_to_defaults(repo):
    (repo / ".no-git-slop.json").write_text("{ not json")
    assert decision(hook("write", write_event(repo, "SUMMARY.md"), repo)) == "deny"
