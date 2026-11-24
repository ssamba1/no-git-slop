"""Turn a Write call or a Bash command into findings.

This is where the file rules, the shell parser and the commit rules meet.
Hooks, the CLI and tests all go through these two functions.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import List, Optional

from . import gitutil, shell, state
from .commits import check_commit_message, check_pr
from .config import Config
from .findings import Finding
from .presets import active_presets, plan_preset_for
from .rules import judge_new_file, protected_finding

_BULK_SPECS = {".", "-A", "--all", "*", ":/", ":(top)", ":/*", "./", "./*", ":(top)*",
               "--no-ignore-removal", ":(glob)**", ":(top,glob)**"}
_HOOK_ENV = {"HUSKY": "0", "HUSKY_SKIP_HOOKS": None, "LEFTHOOK": "0", "LEFTHOOK_EXCLUDE": None,
             "GIT_HOOKS_SKIP": None, "PRE_COMMIT_ALLOW_NO_CONFIG": None}


def rel_to(root: Path, path: Path) -> Optional[str]:
    try:
        rp = Path(os.path.realpath(path)) if path.exists() else Path(os.path.normpath(path))
        rr = Path(os.path.realpath(root))
        return rp.relative_to(rr).as_posix()
    except ValueError:
        try:
            return Path(os.path.normpath(path)).relative_to(root).as_posix()
        except ValueError:
            return None


def state_path_finding(root: Path, config: Config, path: Path) -> List[Finding]:
    """Guard state that lives outside the work tree, e.g. .git/worktrees/<name>/no-git-slop."""
    gd = gitutil.git_dir(root)
    if gd is None:
        return []
    p = Path(os.path.realpath(path))
    for guarded in (gd / state.STATE_DIRNAME, gd / "info" / "exclude"):
        g = Path(os.path.realpath(guarded))
        if p == g or g in p.parents:
            return _finding(config, "guard-state", f"`{path}` belongs to no-git-slop's own state. "
                            "Leave it alone.", p.as_posix())
    return []


def _mode(config: Config, rule: str) -> str:
    return config.mode(rule)


def _finding(config: Config, rule: str, reason: str, path: str = "", actions=None) -> List[Finding]:
    mode = _mode(config, rule)
    return [] if mode == "off" else [Finding(rule, mode, reason, path, actions or [])]


# -------------------------------------------------------------------- Write

def evaluate_write(root: Path, config: Config, session, rel: str, is_new: bool,
                   content: Optional[str], subagent: bool = False) -> List[Finding]:
    prot = protected_finding(rel, config, session)
    if prot and prot.decision != "off":
        return [prot]
    if rel == ".git" or rel.startswith(".git/"):
        return []
    if session is not None and session.data.get("off"):
        return []
    if not is_new:
        return []
    f = judge_new_file(rel, root, config, session, content=content, subagent=subagent)
    if f.strength > 0:
        return [f]
    return _budget(root, config, session, rel) if f.note == "" else []


def _budget(root: Path, config: Config, session, rel: str) -> List[Finding]:
    if session is None or config.newFileBudget <= 0:
        return []
    created = [p for p in session.data.get("created", []) if p != rel]
    if len(created) < config.newFileBudget:
        return []
    if len(gitutil.tracked_files(root)) < config.budgetMinTrackedFiles:
        return []
    return _finding(config, "file-budget", f"This would be new file #{len(created) + 1} this "
                    f"session (budget {config.newFileBudget}). Fold content into existing files "
                    "where you can.", rel)


# --------------------------------------------------------------------- Bash

def evaluate_bash(root: Path, config: Config, session, command: str, cwd: Path,
                  subagent: bool = False) -> List[Finding]:
    findings: List[Finding] = []
    off = bool(session is not None and session.data.get("off"))
    for cmd in shell.parse(command, cwd):
        findings += _bypass_checks(cmd, config)
        for target in shell.write_targets(cmd):
            findings += _target_checks(root, config, session, target, off, subagent)
        call = shell.git_call(cmd)
        if call:
            findings += _git_checks(root, config, session, call, off)
        pr = shell.gh_pr_call(cmd)
        if pr and not off:
            findings += pr_checks(root, config, pr.title, pr.body, pr.base, pr.head)
    unique = {}
    for f in findings:  # `export HUSKY=0 && git commit` reports the same thing twice otherwise
        unique.setdefault((f.rule, f.reason, f.path), f)
    return list(unique.values())


def _bypass_checks(cmd: shell.Command, config: Config) -> List[Finding]:
    out: List[Finding] = []
    env = cmd.env
    for key, bad in _HOOK_ENV.items():
        if key in env and (bad is None or env[key] == bad):
            out += _finding(config, "hooks-bypass", f"`{key}={env[key]}` disables git hooks. Fix "
                            "what the hooks report instead.")
    for k, v in env.items():
        if k.startswith("GIT_CONFIG_KEY_") and v.lower() == "core.hookspath":
            out += _finding(config, "hooks-bypass", "Setting core.hooksPath through GIT_CONFIG_* swaps "
                            "out the repo's git hooks.")
    skip = env.get("SKIP", "")
    skipped = {s.strip() for s in skip.split(",")}  # pre-commit's SKIP is a comma-separated list of hook ids
    if any(s.startswith("no-git-slop") or s in ("ngs", "commitlint", "gitleaks") for s in skipped):
        out += _finding(config, "hooks-bypass", f"`SKIP={skip}` skips the repo's checks.")
    if cmd.name in ("pre-commit", "lefthook", "husky") and cmd.argv[1:2] == ["uninstall"]:
        out += _finding(config, "hooks-bypass", f"`{cmd.name} uninstall` removes the repo's git "
                        "hooks. Ask the user if this is really wanted.")
    return out


def _target_checks(root: Path, config: Config, session, t: shell.WriteTarget, off: bool,
                   subagent: bool) -> List[Finding]:
    rel = rel_to(root, t.path)
    if rel is None:
        return state_path_finding(root, config, t.path)
    if rel == "":
        return []
    if t.kind in ("delete", "chmod"):
        if rel.startswith(".git/hooks/") or rel == ".git/hooks":
            return _finding(config, "hooks-bypass", f"Changing `{rel}` disables git hooks.", rel)
        prot = protected_finding(rel, config, session)
        return [prot] if prot and prot.decision != "off" else []
    prot = protected_finding(rel, config, session)
    if prot and prot.decision != "off":
        return [prot]
    if rel.startswith(".git/") or t.kind == "inplace" or off:
        return []
    if t.path.exists() and not t.path.is_dir():
        return []
    if t.kind == "mkdir":
        f = judge_new_file(rel + "/__dir__", root, config, session, subagent=subagent)
        if f.rule != "copy-folder":
            return []
    elif t.kind in ("cp", "mv", "ln") and t.path.is_dir():
        return []
    else:
        content = t.content if t.kind in ("cp", "redirect", "tee") else None
        f = judge_new_file(rel, root, config, session, content=content, subagent=subagent)
    if f.strength <= 0:
        return []
    if t.kind in ("cp", "mv") and t.source is not None:
        src = rel_to(root, t.source) or t.source.name
        f.reason += f" (via `{t.kind} {src} {rel}`)"
    return [f]


def _staging_suggestion(root: Path, session) -> str:
    touched = list(session.data.get("touched", [])) if session else []
    modified = gitutil._split0(gitutil._git(["diff", "--name-only", "-z"], root))
    paths = []
    for p in modified + touched:
        if p not in paths and (root / p).exists():
            paths.append(p)
    if not paths:
        return "Run `git status` and stage the files you changed by path."
    shown = " ".join(shlex.quote(p) for p in paths[:15])
    more = f" (+{len(paths) - 15} more)" if len(paths) > 15 else ""
    return f"Stage your changed files by path: `git add {shown}`{more}"


def _git_checks(root: Path, config: Config, session, call: shell.GitCall, off: bool) -> List[Finding]:
    out: List[Finding] = []
    sub, args = call.sub, call.args
    flags = shell.short_flags(args)
    # Hook bypasses always apply, even with checks turned off.
    if "core.hookspath" in call.configs:
        out += _finding(config, "hooks-bypass", "`git -c core.hooksPath=...` swaps out the repo's "
                        "git hooks. Fix what they report instead.")
    if sub == "config" and _config_sets_hooks_path(args):
        out += _finding(config, "hooks-bypass", "Changing core.hooksPath disables the repo's git "
                        "hooks. Ask the user first.")
    if sub in ("commit", "push", "merge", "am", "rebase", "cherry-pick") and \
            ("--no-verify" in args or (sub == "commit" and "n" in flags)):
        out += _finding(config, "no-verify", "Don't bypass git hooks with --no-verify. Fix what "
                        "they report.")
    if off:
        return out
    groot = gitutil.repo_root(call.cwd) or root
    if sub == "add":
        out += _add_checks(groot, config, session, call)
    elif sub == "commit":
        out += _commit_checks(groot, config, session, call, flags)
    elif sub == "push":
        out += _push_checks(groot, config, call)
    return out


def _config_sets_hooks_path(args: List[str]) -> bool:
    """`git config core.hooksPath x` or `git config set ...` changes it; reads and unsets don't."""
    keys = [i for i, a in enumerate(args) if a.lower() == "core.hookspath"]
    if not keys:
        return False
    if any(a in ("--unset", "--unset-all", "unset", "--get", "--get-all", "--get-regexp", "get",
                 "--list", "-l", "list") for a in args[:keys[0]]):
        return False
    after = [a for a in args[keys[0] + 1:] if not a.startswith("-")]
    return bool(after) or any(a in ("set", "--add", "--replace-all") for a in args[:keys[0]])


def _add_checks(root: Path, config: Config, session, call: shell.GitCall) -> List[Finding]:
    args = call.args
    if any(a == "--pathspec-from-file" or a.startswith("--pathspec-from-file=") for a in args):
        return _finding(config, "pathspec-file", "Staging from a pathspec file hides what gets "
                        "staged. Stage files by path.")
    plain = [a for a in args if not a.startswith("-") or a in _BULK_SPECS]
    bulk = [a for a in plain if a in _BULK_SPECS]
    for a in plain:
        p = shell._resolve(call.cwd, a)
        if p is not None and rel_to(root, p) == "" and a not in bulk:
            bulk.append(a)
    if any(a in ("--all", "--no-ignore-removal") for a in args) or "A" in shell.short_flags(args, ""):
        bulk.append("-A")
    if bulk and config.mode("bulk-add") != "off":
        return _finding(config, "bulk-add", f"`git add {bulk[0]}` sweeps in every stray file "
                        "(and fails under Claude Code's sandbox). " + _staging_suggestion(root, session))
    if any(a in ("-f", "--force") for a in args):
        return []
    specs = []
    for a in plain:
        p = shell._resolve(call.cwd, a)
        rel = rel_to(root, p) if p is not None else None
        if rel:
            specs.append(rel)
    if not specs:
        return []
    return _clutter(root, config, session, gitutil.untracked_files(root, specs), "would stage")


def _clutter(root: Path, config: Config, session, paths, verb: str) -> List[Finding]:
    out: List[Finding] = []
    presets = active_presets(root, config.presets)
    for rel in paths:
        if config.plans == "ignore" and plan_preset_for(rel, presets):
            out += _finding(config, "plan-staged", f"`{rel}` is a planning file and this project "
                            "keeps plans out of history (plans: ignore).", rel)
            continue
        f = judge_new_file(rel, root, config, session, check_ignored=False)
        if f.decision == "deny":
            mode = config.mode("staged-clutter")
            if mode != "off":
                out.append(Finding("staged-clutter", mode, f"This {verb} `{rel}`: {f.reason}", rel,
                                   [f"git restore --staged {shlex.quote(rel)}"]))
    return out


def _commit_checks(root: Path, config: Config, session, call: shell.GitCall, flags) -> List[Finding]:
    args = call.args
    out: List[Finding] = []
    # `git commit .` commits every modified tracked file, like -a; `git commit src/x.py` is fine.
    whole_repo = False
    for p in shell.split_args(args)[1]:
        resolved = shell._resolve(call.cwd, p)
        whole_repo |= p in _BULK_SPECS or (resolved is not None and rel_to(root, resolved) == "")
    if "--all" in args or "a" in flags or whole_repo:
        out += _finding(config, "commit-all", "`git commit -a` sweeps in every modified file. "
                        "Stage the intended files by path, then commit.")
    if "--signoff" in args or "s" in flags:
        out += _finding(config, "signoff", "Don't add Signed-off-by for the user; a sign-off is "
                        "the human's own certification.")
    out += _clutter(root, config, session, gitutil.staged_new_files(root), "commit includes")
    staged = gitutil.staged_files(root)
    if len(staged) > config.commitFileBudget:
        out += _finding(config, "commit-size", f"This commit stages {len(staged)} files (budget "
                        f"{config.commitFileBudget}). Split it into smaller commits.")
    elif staged:
        lines = gitutil.staged_line_count(root)
        if lines > config.commitLineBudget:
            out += _finding(config, "commit-size", f"This commit changes {lines} lines (budget "
                            f"{config.commitLineBudget}). Split it into smaller commits.")
    message = shell.commit_message(args, call.cwd)
    if message is not None:
        out += check_commit_message(message, config, by_agent=True)
    return out


def _push_checks(root: Path, config: Config, call: shell.GitCall) -> List[Finding]:
    args = call.args
    out: List[Finding] = []
    flags, plain = shell.split_args(args, "o")
    if "f" in flags or any(a == "--force" or a.startswith(("--force-with-lease", "--force-if-includes"))
                           for a in args) or any(a.startswith("+") for a in plain[1:]):
        out += _finding(config, "push-force", "Force-pushing rewrites shared history. Confirm "
                        "with the user first.")
    default = gitutil.default_branch(root)
    branch = gitutil.current_branch(root)
    refspecs = plain[1:]
    targets = [r.split(":", 1)[-1].lstrip("+") for r in refspecs] or [branch]
    targets = [branch if t in ("HEAD", "@") else t for t in targets]
    if any(t in (default, f"refs/heads/{default}") for t in targets) and \
            not any(a in ("--delete", "-d", "--tags", "--dry-run", "-n") for a in args):
        out += _finding(config, "push-default-branch", f"This pushes straight to `{default}`. "
                        "Push a feature branch and open a PR, or confirm with the user.")
    rng = gitutil.unpushed_range(root)
    if rng:
        out += push_violations(root, config, rng)
    return out


def push_violations(root: Path, config: Config, rng: str) -> List[Finding]:
    problems = []
    for sha, body in gitutil.commit_messages(root, rng):
        for f in check_commit_message(body, config):
            if f.decision == "deny":
                problems.append(f"{sha}: {f.reason}")
    for f in _clutter(root, config, None, gitutil.files_added_in_range(root, rng), "push adds"):
        if f.decision == "deny":
            problems.append(f.reason)
    if not problems:
        return []
    listing = "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")
    return _finding(config, "push-unclean", f"Unpushed commits break the rules: {listing}. Fix them "
                    "(amend, or `git rebase -i` to reword) before pushing.")


def pr_checks(root: Path, config: Config, title: str, body: Optional[str], base: str = "",
              head: str = "") -> List[Finding]:
    default = base or gitutil.default_branch(root)
    head = head or gitutil.current_branch(root)
    changed, reports = [], []
    for ref in (f"origin/{default}", default):
        if gitutil._git(["rev-parse", "--verify", "--quiet", ref], root) is not None:
            rng = f"{ref}...HEAD"
            changed = gitutil.files_changed_in_range(root, rng)
            for rel in gitutil.files_added_in_range(root, rng):
                f = judge_new_file(rel, root, config, None, check_ignored=False)
                if f.rule == "report-doc" and f.decision == "deny":
                    reports.append(rel)
            break
    return check_pr(title or "", body or "", config, head=head, default_branch=default,
                    changed_files=changed, report_files=reports)
