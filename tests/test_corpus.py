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


def test_fuzzed_paths_and_commands_never_crash_or_execute(tmp_path):
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
            proc = subprocess.run([sys.executable, str(CLI), "hook", kind], input=json.dumps(event),
                                  capture_output=True, text=True, cwd=repo, timeout=30)
            assert proc.returncode == 0
            assert "skipped after an error" not in proc.stderr, (name, proc.stderr)
            if proc.stdout.strip():
                json.loads(proc.stdout)
    assert not canary.exists(), "a hook executed part of its input"


@pytest.mark.parametrize("payload", ["", "null", "[]", "42", "{\"tool_input\": null}",
                                     "{\"tool_input\": {\"file_path\": 7}}", "not json"])
def test_malformed_events_fail_open(tmp_path, payload):
    repo = make_repo(tmp_path)
    for kind in ("write", "bash", "post", "stop", "prompt", "session-start"):
        proc = subprocess.run([sys.executable, str(CLI), "hook", kind], input=payload,
                              capture_output=True, text=True, cwd=repo, timeout=30)
        assert proc.returncode == 0


def test_state_files_are_private(tmp_path):
    repo = make_repo(tmp_path)
    hook("session-start", {"cwd": str(repo), "session_id": "p"}, repo)
    hook("write", write_event(repo, "SUMMARY.md", session="p"), repo)
    state = repo / ".git" / "no-git-slop"
    for f in state.iterdir():
        assert f.stat().st_mode & 0o077 == 0, f
