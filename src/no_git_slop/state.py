"""Per-repo, per-session state kept inside the git directory.

Layout (never tracked, never pushed):
    .git/no-git-slop/baseline-<session>.json   untracked files at session start
    .git/no-git-slop/session-<session>.json    intent, approvals, counters
    .git/no-git-slop/ledger.jsonl              one line per gate decision

Files are created with owner-only permissions. Only the hooks write here;
agent writes to this folder are blocked by the guard-state rule.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from . import gitutil

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")
STATE_DIRNAME = "no-git-slop"
_MAX_AGE = 14 * 24 * 3600


def state_dir(root: Path) -> Optional[Path]:
    gd = gitutil.git_dir(root)
    if not gd:
        return None
    path = gd / STATE_DIRNAME
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def session_key(event: dict) -> str:
    sid = str(event.get("session_id") or "default")
    return _SAFE_ID.sub("_", sid)[:80] or "default"


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")  # one per writer: hooks run in parallel
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: a reader has the file open for a moment
            if attempt == 19:
                os.unlink(tmp)
                raise
            time.sleep(0.02)


_HELD: set = set()


def _lock(path: Path) -> None:
    """Per-session lock so parallel hooks don't lose each other's updates. Never blocks for long."""
    key = str(path)
    if key in _HELD:
        return
    deadline = time.time() + 5
    while True:
        try:
            os.close(os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            _HELD.add(key)
            return
        except FileExistsError:
            try:
                if time.time() - os.stat(key).st_mtime > 30:  # left behind by a killed hook
                    os.unlink(key)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                return  # proceed unlocked rather than stall the user's session
            time.sleep(0.02)
        except OSError:
            return


def _unlock(path: Path) -> None:
    key = str(path)
    if key in _HELD:
        _HELD.discard(key)
        try:
            os.unlink(key)
        except OSError:
            pass


@atexit.register
def _unlock_all() -> None:
    for key in list(_HELD):
        _unlock(Path(key))


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Session:
    """Mutable per-session record. Call save() after changing it."""

    def __init__(self, root: Path, key: str):
        self.root = root
        self.key = key
        self.dir = state_dir(root)
        self.data = {
            "intent": {"paths": [], "basenames": [], "phrases": [], "allow_globs": []},
            "off": False,
            "pending_codes": {},
            "created": [],
            "touched": [],
            "stop_hash": "",
            "verify_failures": 0,
            "last_head_checked": "",
        }
        if self.dir:
            _lock(self._lockfile())  # held until save(), so load-modify-save is atomic
            stored = _read_json(self.dir / f"session-{key}.json", {})
            if isinstance(stored, dict):
                self.data.update(stored)

    # -- intent -------------------------------------------------------------
    @property
    def intent(self) -> dict:
        return self.data["intent"]

    def add_unique(self, key: str, value: str) -> None:
        bucket = self.data[key] if key in self.data else self.intent[key]
        if value not in bucket:
            bucket.append(value)

    # -- persistence ---------------------------------------------------------
    def _lockfile(self) -> Path:
        return self.dir / f"session-{self.key}.lock"

    def save(self) -> None:
        if not self.dir:
            return
        lock = self._lockfile()
        _lock(lock)
        try:
            _write_json(self.dir / f"session-{self.key}.json", self.data)
        except OSError:
            pass  # losing a counter update is fine; losing the hook's decision is not
        finally:
            _unlock(lock)

    # -- baseline --------------------------------------------------------------
    def write_baseline(self, files) -> None:
        if self.dir:
            _write_json(self.dir / f"baseline-{self.key}.json", sorted(files))
            _write_json(self.dir / "baseline-latest.json", sorted(files))

    def baseline(self):
        if not self.dir:
            return None
        for name in (f"baseline-{self.key}.json", "baseline-latest.json"):
            data = _read_json(self.dir / name, None)
            if isinstance(data, list):
                return set(data)
        return None


def ledger(root: Path, entry: dict) -> None:
    d = state_dir(root)
    if not d:
        return
    entry = {"ts": int(time.time()), **entry}
    path = d / "ledger.jsonl"
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def read_ledger(root: Path):
    d = state_dir(root)
    if not d:
        return []
    out = []
    try:
        with open(d / "ledger.jsonl", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def prune(root: Path) -> None:
    """Remove session files older than two weeks."""
    d = state_dir(root)
    if not d:
        return
    cutoff = time.time() - _MAX_AGE
    for entry in d.glob("*-*.json"):
        if entry.name == "baseline-latest.json":
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            pass
