"""Command line: `no-git-slop <command>` (alias `ngs`)."""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from . import __version__, gitutil, hooks, index, state
from .commits import check_commit_message, churn_findings
from .config import CONFIG_FILE, load_config
from .findings import ORDER, RULES
from .gates import pr_checks, push_violations
from .integrations import DEFAULTS, _executable, run_all
from .presets import PRESETS, active_presets
from .rules import judge_new_file


def _root() -> Path:
    root = gitutil.repo_root(Path.cwd())
    if not root:
        sys.exit("no-git-slop: not inside a git repository")
    return root


def _print_findings(findings, as_json: bool, extra=None) -> int:
    worst = max((ORDER.get(f.decision, 0) for f in findings), default=0)
    if as_json:
        verdict = "fail" if worst >= ORDER["deny"] else ("warn" if worst > 0 else "pass")
        out = {"verdict": verdict, "findings": [f.to_dict() for f in findings if f.strength > 0]}
        if extra is not None:
            out["integrations"] = extra
        out["next_steps"] = sorted({a for f in findings for a in f.actions})
        print(json.dumps(out, indent=2))
    else:
        marks = {"deny": "✗", "ask": "?", "warn": "!"}
        for f in findings:
            if f.strength > 0:
                print(f"{marks.get(f.decision, '-')} {f.line()}")
        for r in extra or []:
            print(f"  [{r['name']}] {r['status']}" + (f" (exit {r.get('exit')})" if r["status"] == "ran" else ""))
            if r["status"] == "ran" and r.get("exit") and r.get("output"):
                print("    " + r["output"].replace("\n", "\n    "))
    return 1 if worst >= ORDER["deny"] else 0


# ------------------------------------------------------------------ check

def cmd_check(args) -> int:
    root = _root()
    config = load_config(root)
    if args.paths:
        paths = [os.path.relpath(os.path.abspath(p), root).replace(os.sep, "/") for p in args.paths]
        check_ignored = True
    elif args.range:
        paths, check_ignored = gitutil.files_added_in_range(root, args.range), False
    elif args.staged:
        paths, check_ignored = gitutil.staged_new_files(root), False
    else:
        paths, check_ignored = gitutil.untracked_files(root), False
    findings = [judge_new_file(p, root, config, None, check_ignored=check_ignored) for p in paths]
    extra = run_all(root, config.integrations) if args.deep else None
    code = _print_findings(findings, args.json, extra)
    if extra and any(r["status"] == "ran" and r.get("exit") for r in extra):
        code = code or (1 if args.strict else 0)
    if code and not args.json:
        print("\nno-git-slop: allow a real exception with an \"allow\" glob in .no-git-slop.json, "
              "or see `no-git-slop explain <rule>`.")
    return code


def cmd_commit_msg(args) -> int:
    root = gitutil.repo_root(Path.cwd()) or Path.cwd()
    message = Path(args.file).read_text(encoding="utf-8", errors="replace")
    findings = check_commit_message(message, load_config(root))
    return _print_findings(findings, args.json)


def cmd_commits(args) -> int:
    root = _root()
    config = load_config(root)
    messages = gitutil.commit_messages(root, args.range)
    findings = []
    for sha, body in messages:
        for f in check_commit_message(body, config):
            f.reason = f"{sha}: {f.reason}"
            findings.append(f)
    findings += churn_findings(messages, config)
    return _print_findings(findings, args.json)


def cmd_push_check(args) -> int:
    root = _root()
    rng = args.range or gitutil.unpushed_range(root)
    if not rng:
        return 0
    return _print_findings(push_violations(root, load_config(root), rng), args.json)


def cmd_pr(args) -> int:
    root = _root()
    body = args.body or ""
    if args.body_file:
        body = sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text(encoding="utf-8")
    findings = pr_checks(root, load_config(root), args.title or "", body, args.base or "")
    return _print_findings(findings, args.json)


_ADD_FILE = re.compile(r"^\*\*\* Add File: (.+)$")
_DIFF_NEW = re.compile(r"^\+\+\+ b/(.+)$")


def cmd_check_patch(args) -> int:
    """Judge new files in a patch read from stdin (Codex apply_patch or git diff format)."""
    root = _root()
    config = load_config(root)
    text = sys.stdin.read()
    new_files, current, buf, new_mode = {}, None, [], False
    for line in text.splitlines():
        m = _ADD_FILE.match(line)
        if m:
            if current:
                new_files[current] = "\n".join(buf)
            current, buf = m.group(1).strip(), []
            continue
        if line.startswith("*** ") and current:
            new_files[current] = "\n".join(buf)
            current, buf = None, []
            continue
        if line.startswith("diff --git"):
            if current:
                new_files[current] = "\n".join(buf)
            current, buf, new_mode = None, [], False
            continue
        if line.startswith("new file mode"):
            new_mode = True
            continue
        m = _DIFF_NEW.match(line)
        if m and new_mode:
            current, buf = m.group(1).strip(), []
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            buf.append(line[1:])
    if current:
        new_files[current] = "\n".join(buf)
    findings = [judge_new_file(p, root, config, None, content=c) for p, c in new_files.items()
                if not (root / p).exists()]
    return _print_findings(findings, args.json)


# --------------------------------------------------------------- explain

def cmd_explain(args) -> int:
    root = gitutil.repo_root(Path.cwd())
    config = load_config(root) if root else None
    ids = [args.rule] if args.rule else list(RULES)
    for rid in ids:
        rule = RULES.get(rid)
        if not rule:
            print(f"Unknown rule `{rid}`. Run `no-git-slop explain` for the list.")
            return 1
        mode = config.mode(rid) if config else rule.default
        if args.rule:
            print(f"{rule.id}: {rule.title}\n  mode: {mode} (default {rule.default})\n"
                  f"  why:  {rule.why}\n  fix:  {rule.fix}\n"
                  f"  change it: \"rules\": {{\"{rule.id}\": \"deny|ask|warn|off\"}} in {CONFIG_FILE}")
        else:
            print(f"{rule.id:<20} {mode:<5} {rule.title}")
    return 0


# ----------------------------------------------------------------- stats

def cmd_stats(args) -> int:
    root = _root()
    entries = state.read_ledger(root)
    if args.since_days:
        cutoff = datetime.datetime.now().timestamp() - args.since_days * 86400
        entries = [e for e in entries if e.get("ts", 0) >= cutoff]
    if args.json:
        print(json.dumps(entries[-args.limit:] if args.limit else entries, indent=2))
        return 0
    if not entries:
        print("No gate decisions recorded yet.")
        return 0
    by_rule = collections.Counter((e.get("rule"), e.get("decision")) for e in entries)
    overrides = [e for e in entries if "user" in (e.get("note") or "")]
    sessions = {e.get("session") for e in entries}
    print(f"{len(entries)} decisions across {len(sessions)} session(s)\n")
    print(f"{'rule':<20} {'decision':<8} count")
    for (rule, decision), n in by_rule.most_common():
        print(f"{rule or '?':<20} {decision or '?':<8} {n}")
    if overrides:
        print(f"\n{len(overrides)} decision(s) softened because the user asked for the file.")
    paths = collections.Counter(e.get("path") for e in entries if e.get("path"))
    if paths:
        print("\nMost-flagged paths:")
        for p, n in paths.most_common(8):
            print(f"  {n:>3}  {p}")
    return 0


# ----------------------------------------------------------------- index

def cmd_index(args) -> int:
    root = _root()
    rels = [os.path.relpath(os.path.abspath(p), root).replace(os.sep, "/") for p in args.paths]
    rels = [r for r in rels if r != "."]
    lines = index.build(root, rels, args.limit)
    print("\n".join(lines) if lines else "No definitions found.")
    return 0


# --------------------------------------------------------------- archive

def cmd_archive(args) -> int:
    root = _root()
    today = datetime.date.today().isoformat()
    tracked = set(gitutil.tracked_files(root))
    for raw in args.paths:
        src = Path(raw).resolve()
        if not src.exists():
            print(f"✗ {raw}: not found")
            return 1
        dest = src.parent / "archive" / f"{today}-{src.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        rel = src.relative_to(root).as_posix()
        if rel in tracked or any(t.startswith(rel + "/") for t in tracked):
            subprocess.run(["git", "mv", str(src), str(dest)], cwd=str(root), check=True)
        else:
            shutil.move(str(src), str(dest))
        print(f"archived {rel} -> {dest.relative_to(root).as_posix()}")
    return 0


# ------------------------------------------------------------------ init

_HOOK_SCRIPTS = {
    "pre-commit": "check --staged",
    "commit-msg": 'commit-msg "$1"',
    "pre-push": "push-check",
}
_MARKER = "# installed by no-git-slop"


def cmd_init(args) -> int:
    root = _root()
    config = load_config(root)
    scratch = config.scratchDir.strip("/") + "/"
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if scratch not in existing.split() and "/" + scratch not in existing.split():
        sep = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(f"{existing}{sep}\n# Throwaway work (no-git-slop)\n{scratch}\n",
                             encoding="utf-8")
        print(f"Added {scratch} to .gitignore")
    else:
        print(f"{scratch} is already git-ignored")
    if args.hooks:
        if gitutil._git(["config", "--get", "core.hooksPath"], root):
            print("core.hooksPath is set (husky/lefthook?). Add `no-git-slop check --staged`, "
                  "`no-git-slop commit-msg` and `no-git-slop push-check` to that setup instead.")
            return 0
        gd = gitutil.git_dir(root)
        exe = str(Path(sys.argv[0]).resolve()) if Path(sys.argv[0]).exists() else "no-git-slop"
        for name, sub in _HOOK_SCRIPTS.items():
            hook = gd / "hooks" / name
            if hook.exists() and _MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
                print(f"Skipped {name}: an existing hook is there. Chain `{exe} {sub}` into it.")
                continue
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(f"#!/bin/sh\n{_MARKER}\nexec \"{sys.executable}\" \"{exe}\" {sub}\n",
                            encoding="utf-8")
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            print(f"Installed git {name} hook")
    return 0


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    root = _root()
    config = load_config(root)
    print(f"no-git-slop {__version__}\nrepo:      {root}")
    cfg = root / CONFIG_FILE
    print(f"config:    {CONFIG_FILE if cfg.exists() else 'defaults'} (profile {config.profile})")
    scratch = config.scratchDir.strip("/")
    ignored = f"{scratch}/x" in gitutil.ignored(root, [f"{scratch}/x"])
    print(f"scratch:   {scratch}/ ({'ignored' if ignored else 'NOT ignored: run no-git-slop init'})")
    presets = active_presets(root, config.presets)
    print(f"presets:   {', '.join(presets) if presets else 'none detected'} "
          f"(known: {', '.join(PRESETS)})")
    gd = gitutil.git_dir(root)
    hooks_installed = [n for n in _HOOK_SCRIPTS if gd and (gd / "hooks" / n).exists()]
    print(f"git hooks: {', '.join(hooks_installed) or 'none'}"
          f"{' (core.hooksPath set)' if gitutil._git(['config', '--get', 'core.hooksPath'], root) else ''}")
    print(f"pre-commit config: {'yes' if (root / '.pre-commit-config.yaml').exists() else 'no'}")
    print(f"verify:    {config.verify or 'not set'}")
    tools = dict(DEFAULTS)
    tools.update(config.integrations)
    for name, template in tools.items():
        if template == "off":
            status = "off"
        else:
            first = template.split()[0] if template.split() else ""
            status = "available" if first and _executable(root, first) else "not installed"
        print(f"tool:      {name} ({status})")
    print(f"ledger:    {len(state.read_ledger(root))} decision(s) recorded")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="no-git-slop",
                                     description="Keep AI agents from cluttering git repos.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        return p

    p = add("check", cmd_check, "judge new files (untracked by default)")
    p.add_argument("paths", nargs="*")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="files staged for commit")
    g.add_argument("--range", help="files added in a revision range, e.g. origin/main..HEAD")
    p.add_argument("--deep", action="store_true", help="also run installed integrations")
    p.add_argument("--strict", action="store_true", help="fail when integrations report problems")
    p.add_argument("--json", action="store_true")

    p = add("commit-msg", cmd_commit_msg, "validate a commit message file")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")

    p = add("commits", cmd_commits, "validate commit messages in a range")
    p.add_argument("range")
    p.add_argument("--json", action="store_true")

    p = add("push-check", cmd_push_check, "check unpushed commits (for a pre-push hook)")
    p.add_argument("--range")
    p.add_argument("--json", action="store_true")

    p = add("pr", cmd_pr, "self-check a pull request before opening it")
    p.add_argument("--title")
    p.add_argument("--body")
    p.add_argument("--body-file")
    p.add_argument("--base")
    p.add_argument("--json", action="store_true")

    p = add("check-patch", cmd_check_patch, "judge new files in a patch on stdin")
    p.add_argument("--json", action="store_true")

    p = add("explain", cmd_explain, "list rules or explain one")
    p.add_argument("rule", nargs="?")

    p = add("stats", cmd_stats, "summarize recorded gate decisions")
    p.add_argument("--json", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--since-days", type=int, default=0)

    p = add("index", cmd_index, "list existing function/class signatures")
    p.add_argument("paths", nargs="*")
    p.add_argument("--limit", type=int, default=400)

    p = add("archive", cmd_archive, "move finished plan files into a dated archive folder")
    p.add_argument("paths", nargs="+")

    p = add("init", cmd_init, "git-ignore the scratch folder; --hooks installs git hooks")
    p.add_argument("--hooks", action="store_true")

    add("doctor", cmd_doctor, "show what no-git-slop sees in this repo")

    p = sub.add_parser("hook", help="Claude Code hook entry point")
    p.add_argument("name", choices=sorted(hooks.HANDLERS))
    p.set_defaults(func=lambda a: hooks.run(a.name))

    args = parser.parse_args(argv)
    return args.func(args)
