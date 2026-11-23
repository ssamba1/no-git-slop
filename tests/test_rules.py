import pytest

from no_git_slop.rules import Config, check_commit_message, judge_new_file


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
])
def test_allows_real_files(repo, path):
    assert decide(path, repo) == "ok"


def test_new_root_doc_asks(repo):
    assert decide("DESIGN.md", repo) == "ask"


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
