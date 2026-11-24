from pathlib import Path

import pytest

from no_git_slop import shell

CWD = Path("/work/repo")


def targets(command):
    out = []
    for cmd in shell.parse(command, CWD):
        for t in shell.write_targets(cmd):
            out.append((t.path.relative_to(CWD).as_posix() if str(t.path).startswith(str(CWD)) else t.path.as_posix(),
                        t.kind))
    return out


@pytest.mark.parametrize("command, expected", [
    ("cat > SUMMARY.md <<'EOF'\nhello\nEOF", [("SUMMARY.md", "redirect")]),
    ("echo hi >> notes.md", [("notes.md", "redirect")]),
    ("echo hi 2>err.log", [("err.log", "redirect")]),
    ("cmd &> out.log", [("out.log", "redirect")]),
    ("echo hi | tee -a a.md b.md", [("a.md", "tee"), ("b.md", "tee")]),
    ("cp app.py app_backup.py", [("app_backup.py", "cp")]),
    ("cp -r a.py b.py lib/", [("lib/a.py", "cp"), ("lib/b.py", "cp")]),
    ("cp -t dest a.py", [("dest/a.py", "cp")]),
    ("mv x.py x_old.py", [("x_old.py", "mv")]),
    ("install -m 644 a.conf etc/a.conf", [("etc/a.conf", "cp")]),
    ("ln -s target link_v2", [("link_v2", "ln")]),
    ("touch a b", [("a", "touch"), ("b", "touch")]),
    ("mkdir -p NuclearFix/x", [("NuclearFix/x", "mkdir")]),
    ("dd if=/dev/zero of=blob.bin bs=1", [("blob.bin", "dd")]),
    ("curl -sSL -o page.html https://x", [("page.html", "download")]),
    ("wget -O data.json https://x", [("data.json", "download")]),
    ("python3 -c \"open('debug_x.py', 'w').write('1')\"", [("debug_x.py", "script")]),
    ("python -c \"from pathlib import Path; Path('NOTES.md').write_text('x')\"", [("NOTES.md", "script")]),
    ("node -e \"require('fs').writeFileSync('REPORT.md', 'x')\"", [("REPORT.md", "script")]),
    ("sed -i 's/a/b/' config.json", [("config.json", "inplace")]),
    ("perl -pi -e 's/a/b/' x.txt", [("x.txt", "inplace")]),
    ("rm -f .git/hooks/pre-commit", [(".git/hooks/pre-commit", "delete")]),
    ("chmod -x .git/hooks/pre-commit", [(".git/hooks/pre-commit", "chmod")]),
])
def test_write_targets(command, expected):
    assert targets(command) == expected


@pytest.mark.parametrize("command, expected", [
    ("bash -c 'touch FIX_NOTES.md'", [("FIX_NOTES.md", "touch")]),
    ("sh -lc \"echo x > tmp.sh\"", [("tmp.sh", "redirect")]),
    ("cd sub && touch a.md", [("sub/a.md", "touch")]),
    ("env -C sub touch a.md", [("sub/a.md", "touch")]),
    ("env FOO=1 sudo -u me timeout 5 nice -n 5 touch a.md", [("a.md", "touch")]),
    ("bash -c \"bash -c 'bash -c \\\"touch deep.md\\\"'\"", [("deep.md", "touch")]),
    ("(cd sub; touch x.md)", [("sub/x.md", "touch")]),
])
def test_wrappers_are_unwrapped(command, expected):
    assert targets(command) == expected


@pytest.mark.parametrize("command", [
    "echo $HOME > \"$HOME/x.md\"",
    "cp a.py \"$(mktemp)\"",
    "touch *.md",
    "ls > /dev/null 2>&1",
    "cmd 2>&1",
    "echo 'cat > fake.md' ",
])
def test_dynamic_or_harmless_targets_are_skipped(command):
    assert targets(command) == []


def test_heredoc_content_is_captured():
    [cmd] = shell.parse("cat > a.md <<'EOF'\nline one\nline two\nEOF", CWD)
    [t] = shell.write_targets(cmd)
    assert t.content == "line one\nline two"


def test_heredoc_body_is_not_parsed_as_commands():
    cmds = shell.parse("cat > a.md <<EOF\nrm -rf /\ngit add -A\nEOF\necho done", CWD)
    assert [c.argv[0] for c in cmds] == ["cat", "echo"]


def test_quoted_heredoc_commit_message_survives():
    command = "git commit -m \"$(cat <<'EOF'\nfeat(x): add y\n\nBody.\nEOF\n)\""
    [cmd] = shell.parse(command, CWD)
    call = shell.git_call(cmd)
    assert shell.commit_message(call.args, CWD) == "feat(x): add y\n\nBody."


def test_git_call_parses_global_options():
    [cmd] = shell.parse("git -C sub -c core.hooksPath=/dev/null --no-pager commit -m x", CWD)
    call = shell.git_call(cmd)
    assert call.sub == "commit"
    assert call.cwd == CWD / "sub"
    assert call.configs == {"core.hookspath": "/dev/null"}


def test_env_assignments_are_kept():
    [cmd] = shell.parse("HUSKY=0 SKIP=no-git-slop git commit -m x", CWD)
    assert cmd.env == {"HUSKY": "0", "SKIP": "no-git-slop"}


@pytest.mark.parametrize("args, message", [
    (["-m", "feat: a"], "feat: a"),
    (["-am", "feat: a"], "feat: a"),
    (["-mfeat: a"], "feat: a"),
    (["--message=feat: a"], "feat: a"),
    (["-m", "feat: a", "-m", "body"], "feat: a\n\nbody"),
    (["-m", "$MSG"], None),
    (["--fixup", "abc"], None),
    (["-C", "HEAD"], None),
])
def test_commit_message_extraction(args, message):
    assert shell.commit_message(args, CWD) == message


def test_gh_pr_create_parsing():
    [cmd] = shell.parse("gh pr create --title 'feat: x' --body 'I hope this helps' --base main", CWD)
    pr = shell.gh_pr_call(cmd)
    assert (pr.title, pr.body, pr.base) == ("feat: x", "I hope this helps", "main")


def test_unparseable_command_is_ignored():
    assert shell.parse("echo 'unterminated", CWD) == []
