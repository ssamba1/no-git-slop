import pytest

from no_git_slop.commits import check_commit_message
from no_git_slop.config import Config
from no_git_slop.rules import judge_new_file


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parser.py").write_text("")
    (tmp_path / "api.py").write_text("")
    return tmp_path


def decide(path, root):
    return judge_new_file(path, root, Config()).decision


@pytest.mark.parametrize("path", [
    "IMPLEMENTATION_SUMMARY.md",
    "FIX_NOTES.md",
    "docs/REFACTORING_SUMMARY.md",
    "progress_report.md",
    "src/parser_fixed.py",
    "src/parser_backup.py",
    "src/parser.py.bak",
    "src/parser copy.py",
    "src/parser_v2.py",
    "src/parser_new.py",
    "ErrorFixes/NuclearFix/parser.py",
    "DataExplorerBackup/view.swift",
    "debug_auth.py",
    "src/scratch_parse.py",
    "test_login.py",
    ".DS_Store",
    "server.log",
    "src/parser_copy.py",
    "quick_check.py",
    "SUMMARY.md",
])
def test_blocks_slop(repo, path):
    assert decide(path, repo) == "deny"


@pytest.mark.parametrize("path", [
    "README.md",
    "CHANGELOG.md",
    "docs/how-to/install.md",
    "docs/plan.md",
    "src/lexer.py",
    "src/api_v2.py",          # no unsuffixed sibling, so it's a real versioned module
    "tests/test_parser.py",
    "tests/debug_helpers.py",
    "src/new_user.py",
    ".scratch/debug_auth.py",
    ".github/workflows/ci.yml",
    "package.json",
    "src/prefix/tree.py",
    "src/SUMMARY.md",         # mdBook's table of contents
    "RELEASE_NOTES.md",
    "docs/RELEASE_NOTES.md",
    "src/lib/debug.ts",
    "src/sort/quick_sort.py",
    "src/sandbox.py",
    "src/ui/button-copy.tsx",  # no button.tsx sibling
    "docs/archive/2023.md",
])
def test_allows_real_files(repo, path):
    assert decide(path, repo) == "ok"


def test_new_top_level_folder_warns_once(repo):
    assert decide("analysis/run.py", repo) == "warn"
    assert decide("docs/guide.md", repo) == "ok"  # common folder
    (repo / "analysis").mkdir()
    assert decide("analysis/run.py", repo) == "ok"  # folder exists now


def test_misplaced_python_test_warns_only_when_tests_folder_exists(repo):
    assert decide("src/test_parser.py", repo) == "ok"
    (repo / "tests").mkdir()
    assert decide("src/test_parser.py", repo) == "warn"
    assert decide("tests/test_parser.py", repo) == "ok"
    assert decide("src/parser_test.go", repo) == "ok"  # Go keeps tests next to code


def test_new_root_doc_asks(repo):
    assert decide("DESIGN.md", repo) == "ask"
    assert decide("TODO.md", repo) == "ask"


def test_weak_suffix_blocked_when_sibling_exists(repo):
    assert decide("api_v2.py", repo) == "deny"


def test_config_allow_and_deny(repo):
    config = Config(allow=["NOTES.md"], deny=["*.ipynb"])
    assert judge_new_file("NOTES.md", repo, config).decision == "ok"
    assert judge_new_file("analysis.ipynb", repo, config).decision == "deny"


@pytest.mark.parametrize("message", [
    "feat(api): add token refresh",
    "fix: handle empty config file\n\nThe parser crashed on zero bytes.",
    "refactor!: drop Python 3.8 support",
    "Merge branch 'main' into feature",
])
def test_good_commit_messages(message):
    assert check_commit_message(message, Config()) == []


@pytest.mark.parametrize("message", [
    "updated stuff",
    "fix: update",
    "feat: " + "x" * 80,
    "feat: add thing\nno blank line",
    "",
])
def test_bad_commit_messages(message):
    assert check_commit_message(message, Config())


def test_commit_style_off():
    assert check_commit_message("whatever", Config(commitStyle="off")) == []


def _rules(message, config=None):
    return {f.rule for f in check_commit_message(message, config or Config())}


def test_em_dash_in_commit_message_is_blocked():
    em = chr(0x2014)
    assert "commit-em-dash" in _rules(f"fix(api): handle empty token\n\nThe parser crashed {em} now it doesn't.")
    assert "commit-em-dash" not in _rules("fix(api): handle empty token\n\nUse `a" + em + "b` as the separator.")


def test_chatbot_leftovers_are_blocked():
    assert "ai-artifact" in _rules("docs: add usage\n\nSee the guide citeturn0search1.")
    assert "ai-artifact" in _rules("docs: add usage\n\nAs of my last training update this works.")
    assert "ai-artifact" in _rules("docs: add contact\n\nContact [Your Name] for access.")


def test_human_phrases_are_not_chatbot_residue():
    assert "commit-prose" not in _rules("fix: handle empty token\n\nLet me know if this breaks your setup; feel free to revert.")


def test_attribution_needs_an_agent_address_or_model_name():
    forbid = Config(attribution="forbid")
    assert "attribution" not in _rules("fix: handle empty token\n\nCo-authored-by: Claude Paroz <claude@example.com>", forbid)
    assert "attribution" in _rules("fix: handle empty token\n\nCo-authored-by: Claude <noreply@anthropic.com>", forbid)
    assert "attribution" in _rules("fix: handle empty token\n\nCo-authored-by: Claude Opus 5.5 <x@example.com>", forbid)


def test_doc_findings_warn_on_ai_style_only():
    from no_git_slop.commits import doc_findings
    em = chr(0x2014)
    assert doc_findings("## Usage\n\nRun `ngs check` before you push.\n", Config()) == []
    found = doc_findings(f"## \U0001F680 Usage\n\nIt works {em} fast, highlighting its importance.\n", Config())
    assert [f.decision for f in found] == ["warn"]
    assert "em dash" in found[0].reason and "emoji" in found[0].reason and "trailing clause" in found[0].reason
