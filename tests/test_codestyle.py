import pytest

from no_git_slop.codestyle import code_findings


@pytest.mark.parametrize("path, text", [
    ("src/app.py", "def run():\n    # ... rest of the code remains the same\n"),
    ("src/app.ts", "function run() {\n  // existing code stays unchanged\n}\n"),
])
def test_elided_code_blocks(path, text):
    hard, _ = code_findings(path, text)
    assert hard


@pytest.mark.parametrize("path, text, word", [
    ("src/app.py", "# Updated to use the new API\nclient.call()\n", "edit or the chat"),
    ("src/app.py", "try:\n    send()\nexcept Exception:\n    pass\n", "silently dropped"),
    ("src/app.js", "try { send() } catch (e) {}\n", "silently dropped"),
    ("src/app.ts", "const x = data as any;\n", "as any"),
    ("src/app.py", "value = compute()  # type: ignore\n", "type: ignore"),
    ("tests/test_app.py", "@pytest.mark.skip\ndef test_total():\n    ...\n", "skips or fakes"),
    ("src/app.py", "breakpoint()\n", "debug leftover"),
    ("src/app.py", "print(f\"{total=}\")\n", "debug leftover"),
    ("src/app.py", "# Retry once" + chr(0x2014) + "the API drops the first call\n", "em dash"),
])
def test_ai_habits_warn(path, text, word):
    hard, soft = code_findings(path, text)
    assert not hard and any(word in s for s in soft), soft


@pytest.mark.parametrize("path, text", [
    ("src/app.py", "# Retry once because the API drops the first call.\nclient.call()\n"),
    ("src/app.py", "try:\n    import ujson as json\nexcept ImportError:\n    import json\n"),
    ("src/app.py", "value = compute()  # type: ignore[arg-type]\n"),
    ("src/app.py", "x = 1  # noqa: E741\n"),
    ("src/app.ts", "// eslint-disable-next-line no-console\nconsole.log(msg);\n"),
    ("src/cli.py", "print(f\"wrote {count} files\")\n"),
    ("tests/test_app.py", "breakpoint()\n"),
    ("src/app.py", "url = 'http://example.com/rest of the code'\n"),
    ("README.md", "# ... rest of the code remains the same\n"),
    # Human comments from the Python standard library that earlier patterns flagged.
    ("src/ntpath.py", "# Previously, this function also truncated pathnames to 8+3 format\n"),
    ("src/subprocess.py", "# which is calling CloseHandle as requested in [1]\n"),
    ("src/typing.py", "#     def stub(self): ...  # implementation goes here\n"),
    ("src/_common.py", "# The remainder of the file consists of leap seconds\n"),
    ("src/compiler.py", "# keep it as any other library\n"),
    ("src/pdb.py", "    debugger\n"),
])
def test_ordinary_code_passes(path, text):
    assert code_findings(path, text) == ([], [])
