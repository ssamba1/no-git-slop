"""Command line: `no-git-slop <command>`.

  check [--staged | --range A..B] [PATH ...]   judge new files (exit 1 on slop)
  commit-msg FILE                              validate a commit message file
  commits RANGE                                validate every commit in a range
  hook {write,bash,session-start,stop}         Claude Code hook entry points
  init                                         add the scratch dir to .gitignore
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, gitutil, hooks
from .rules import check_commit_message, judge_new_file, load_config


def _root() -> Path:
    root = gitutil.repo_root(Path.cwd())
    if not root:
        sys.exit("no-git-slop: not inside a git repository")
    return root


def cmd_check(args) -> int:
    root = _root()
    config = load_config(root)
    if args.paths:
        paths = args.paths
    elif args.range:
        paths = gitutil.files_added_in_range(root, args.range)
    elif args.staged:
        paths = gitutil.staged_new_files(root)
    else:
        paths = gitutil.untracked_files(root)
    failures = 0
    for rel in paths:
        verdict = judge_new_file(rel, root, config)
        if verdict.decision == "deny":
            failures += 1
            print(f"✗ {verdict.reason}")
        elif verdict.decision == "ask":
            print(f"? {verdict.reason}")
    if failures:
        print(f"\nno-git-slop: {failures} file(s) look like clutter. Allow a real exception "
              f"with an \"allow\" glob in .no-git-slop.json.")
    return 1 if failures else 0


def cmd_commit_msg(args) -> int:
    root = gitutil.repo_root(Path.cwd()) or Path.cwd()
    message = Path(args.file).read_text(encoding="utf-8", errors="replace")
    problems = check_commit_message(message, load_config(root))
    for p in problems:
        print(f"✗ {p}")
    return 1 if problems else 0


def cmd_commits(args) -> int:
    root = _root()
    config = load_config(root)
    bad = 0
    for sha, body in gitutil.commit_messages(root, args.range):
        for p in check_commit_message(body, config):
            bad += 1
            print(f"✗ {sha}: {p}")
    return 1 if bad else 0


def cmd_init(args) -> int:
    root = _root()
    scratch = load_config(root).scratchDir.strip("/") + "/"
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if scratch not in existing.split():
        sep = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(f"{existing}{sep}\n# Throwaway work (no-git-slop)\n{scratch}\n",
                             encoding="utf-8")
        print(f"Added {scratch} to .gitignore")
    else:
        print(f"{scratch} is already git-ignored")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="no-git-slop",
                                     description="Keep AI agents from cluttering git repos.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="judge new files")
    p.add_argument("paths", nargs="*")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="files staged for commit")
    group.add_argument("--range", help="files added in a revision range, e.g. origin/main..HEAD")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("commit-msg", help="validate a commit message file")
    p.add_argument("file")
    p.set_defaults(func=cmd_commit_msg)

    p = sub.add_parser("commits", help="validate commit messages in a range")
    p.add_argument("range")
    p.set_defaults(func=cmd_commits)

    p = sub.add_parser("hook", help="Claude Code hook entry point")
    p.add_argument("name", choices=sorted(hooks.HANDLERS))
    p.set_defaults(func=lambda a: hooks.run(a.name))

    p = sub.add_parser("init", help="git-ignore the scratch folder")
    p.set_defaults(func=cmd_init)

    args = parser.parse_args(argv)
    return args.func(args)
