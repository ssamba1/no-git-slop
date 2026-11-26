"""Replay the regression corpus and fuzz the hooks with hostile input."""

import json
import random
import string
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import CLI, bash_event, decision, hook, make_repo, write_event

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "corpus.json").read_text())


@pytest.fixture(scope="module")
def corpus_repo(tmp_path_factory):
    return make_repo(tmp_path_factory.mktemp("corpus"))


@pytest.mark.parametrize("case", CORPUS, ids=[c["name"] for c in CORPUS])
def test_corpus(corpus_repo, case):
    if case["hook"] == "write":
        result = hook("write", write_event(corpus_repo, case["path"], session="corpus"), corpus_repo)
    else:
        result = hook("bash", bash_event(corpus_repo, case["command"], session="corpus"), corpus_repo)
    got = decision(result) or "none"
    assert got == case["expect"], f"{case['name']}: expected {case['expect']}, got {got} ({result})"


def _random_name(rng):
    alphabet = string.ascii_letters + string.digits + " ._-$`'\"()[]{}*?;&|<>\\~!#%^=+,:é漢🙂\t"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))


def test_fuzzed_paths_and_commands_never_crash_or_execute(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "fuzz" if (tmp_path / "fuzz").mkdir() is None else tmp_path)
    rng = random.Random(1234)
    canary = tmp_path / "PWNED"
    payloads = [f"$(touch {canary})", f"`touch {canary}`", f"x; touch {canary}",
                f"x && touch {canary}", f"a\ntouch {canary}"]
    for i in range(150):
        name = payloads[i % len(payloads)] if i < 20 else _random_name(rng)
        for event, kind in ((write_event(repo, name, session="fz"), "write"),
                            (bash_event(repo, f"touch {name!s}", session="fz"), "bash"),
                            (bash_event(repo, f"git commit -m {name!r}", session="fz"), "bash"),
                            (bash_event(repo, name, session="fz"), "bash")):
            # In-process: 600 hook calls as subprocesses took 13 minutes. An injected command would
            # still create the canary, so the check is the same.
            code, out, err = _run_hook_in_process(kind, json.dumps(event), repo, monkeypatch)
            assert code == 0
            assert "skipped after an error" not in err, (name, err)
            if out.strip():
                json.loads(out)
    assert not canary.exists(), "a hook executed part of its input"
    # One real subprocess per payload keeps the launcher itself covered.
    for name in payloads:
        proc = subprocess.run([sys.executable, str(CLI), "hook", "bash"],
                              input=json.dumps(bash_event(repo, name, session="fz")),
                              capture_output=True, text=True, cwd=repo, timeout=60)
        assert proc.returncode == 0 and "skipped after an error" not in proc.stderr
    assert not canary.exists(), "a hook executed part of its input"


def _run_hook_in_process(kind, stdin_text, cwd, monkeypatch):
    import io
    sys.path.insert(0, str(CLI.parents[1] / "src"))
    from no_git_slop import hooks
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    try:
        code = hooks.run(kind)
    finally:
        monkeypatch.undo()
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("payload", ["", "null", "[]", "42", "{\"tool_input\": null}",
                                     "{\"tool_input\": {\"file_path\": 7}}", "not json"])
def test_malformed_events_fail_open(tmp_path, payload):
    repo = make_repo(tmp_path)
    for kind in ("write", "bash", "post", "stop", "prompt", "session-start"):
        proc = subprocess.run([sys.executable, str(CLI), "hook", kind], input=payload,
                              capture_output=True, text=True, cwd=repo, timeout=30)
        assert proc.returncode == 0


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no POSIX permission bits")
def test_state_files_are_private(tmp_path):
    repo = make_repo(tmp_path)
    hook("session-start", {"cwd": str(repo), "session_id": "p"}, repo)
    hook("write", write_event(repo, "SUMMARY.md", session="p"), repo)
    state = repo / ".git" / "no-git-slop"
    for f in state.iterdir():
        assert f.stat().st_mode & 0o077 == 0, f
