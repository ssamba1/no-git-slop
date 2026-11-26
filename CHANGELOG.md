# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Hooks now run on Windows: a `bin/run-hook` launcher picks the first working Python 3.9+ (`python3`, `python` or `py`) instead of calling `python3`, which on Windows is often the Microsoft Store stub. It runs Python with `-I -S` and caches the interpreter in the plugin data folder.
- Faster hooks: one `git rev-parse` per hook instead of up to four, hook calls skip loading the CLI, and the launcher no longer forks `dirname`. On Windows a Write hook drops from about 600 ms to about 445 ms.
- The bulk-add message no longer calls every modified file "changed this session".
- Bash parsing on Windows: `/dev/null` and other rooted paths such as `/tmp/x` are no longer judged as files inside the repo, and Git Bash paths like `/c/repo/x` map to `C:/repo/x`.
- Shell parser gaps: git commands inside `if`/`for`/`while`/`{ }`/`!`, after a `<<<` here-string, or before a `#` comment containing an apostrophe are now checked. So are `git add -Av`, `git.exe`, `git push -fu`, `git commit .`, `export HUSKY=0 && ...`, `GIT_CONFIG_KEY_n=core.hooksPath` and `timeout -s KILL`.
- False positives: a commit body line starting with `- ` no longer reads as `-a`/`-n`/`-s`; `git config core.hooksPath` (a read) and `SKIP=check-settings` are allowed; `src/SUMMARY.md`, `RELEASE_NOTES.md`, `src/debug.ts`, `src/sandbox.py`, `quick_sort.py`, `button-copy.tsx` and `docs/archive/*.md` are allowed, and `TODO.md` asks instead of denying.
- Parallel hooks no longer race on one temp file: a per-session lock makes load and save atomic, and a failed save can no longer swallow a deny.
- git output is decoded as UTF-8, so repos under paths like `C:\Users\José` are checked instead of silently skipped.
- no-git-slop's state is guarded in git worktrees too, and naming some other `settings.json` in a prompt no longer unlocks `.claude/settings.json`.
- CLI: `bin/no-git-slop` and `bin/ngs` start on Windows (they used `python3`); output with a ✗ mark no longer crashes on a cp1252 pipe; a bad revision range is an error instead of a silent pass; `init --hooks` works from a pip install or `python -m`; `archive` refuses paths outside the repo; pip installs get the `ngs` alias.
- Line endings are pinned to LF so the shell launchers survive a Windows checkout.
- Commands run through Claude Code's PowerShell tool are now checked like Bash commands; before, `git add -A` or `--no-verify` in PowerShell skipped no-git-slop entirely. `powershell -Command "..."` inside Bash is unwrapped too.
- PowerShell .NET file calls (`[IO.File]::WriteAllText`, `AppendAllLines`, `Copy`, `Move`, `Delete`, `[IO.Directory]::CreateDirectory`, `New-Object IO.StreamWriter`) are checked like the matching cmdlets; before, they wrote report and backup files unchecked.
- `no-git-slop init --hooks` prints an error instead of crashing when git cannot report the repo's `.git` folder.
- A request in the prompt is honored when the prompt also names the file to edit. "Rewrite calc.py ... keep the old version around" used to deny `cp calc.py calc_old.py` because naming `calc.py` counted as naming the only `.py` file wanted; now only named files that don't exist yet count. Found in a live Claude Code session.
- `check --deep`: tools installed in the project (`node_modules/.bin`) run on Windows (the `.cmd` shim is used instead of the sh script); a file list too long for one command line is reported as skipped instead of failing; ANSI color codes are stripped from tool output.
- `check --deep` runs `aislop scan --changes` instead of `--staged`, which reported a clean pass on untracked files with real findings.
- Hook events with Git Bash paths (`/d/repo`) resolve to the right repo, and `sh no-git-slop` / `sh ngs` work when run from inside `bin/`.

## [0.2.0] - 2026-11-24

### Added

- User intent: files named in the prompt are allowed, request phrases turn blocks into questions, one-time approval codes, and `>ngs allow`, `>ngs off` and `>ngs on` session tokens.
- Static shell parser: Bash writes (redirects, heredocs, `tee`, `cp`, `mv`, `touch`, `mkdir`, `python -c`, `node -e` and more) are checked before they run, through `bash -c`, `env`, `sudo` and `cd` up to five levels deep.
- Self-protection: asks before edits to guardrail configs; blocks writes to its own state, `core.hooksPath`, `HUSKY=0`, `SKIP=`, hook deletion and `pre-commit uninstall`.
- Near-duplicate check for new files, a per-session new-file budget, and commit file and line budgets.
- Planning-framework presets for 14 frameworks, the `plans` setting, and an `archive` command.
- Claude Code's own temporary files (sandbox stubs, `tmpclaude-*`) are recognized and never blamed on the agent.
- Staging hardening (`:/`, `:(top)`, `*`, `git -C`, `env -C`, `--pathspec-from-file`) and checks for clutter inside staged folders.
- Commit checks: post-commit re-check, multi-change subjects, `Signed-off-by` added by the agent, attribution policy, chatbot phrasing.
- Push gate (unpushed rule violations, force pushes, pushes to the default branch) and PR self-check for `gh pr create`.
- `verify` command enforced at Stop with a retry cap, diff-size warning, and optional jscpd, vulture, knip and aislop integrations that report ran, unavailable or skipped.
- Per-rule modes (`deny`, `ask`, `warn`, `off`), `relaxed`, `default` and `strict` profiles, and wording that matches Claude Code's built-in subagent report block.
- Commands: `explain`, `stats`, `index`, `pr`, `push-check`, `check-patch`, `archive`, `doctor`, `init --hooks`, and `--json` output.
- `slop-minimizer` and `slop-reviewer` agents; `commit` and `learn` skills; a pre-push hook for pre-commit.
- Local decision ledger, private to the repo's git directory.
- Regression corpus built from reported anthropics/claude-code issues, plus fuzz tests for hostile file names.

### Changed

- The scratch folder is git-ignored through `.git/info/exclude` at session start, leaving `.gitignore` untouched.
- The Stop sweep blocks again only when the findings change, and compares against a per-session baseline.

## [0.1.0] - 2026-11-23

### Added

- Rules engine for new files: status-report docs, backup and copy files, copy folders, throwaway scripts and junk files.
- Claude Code plugin with SessionStart, PreToolUse (Write and Bash) and Stop hooks.
- Git command checks: bulk adds, `--no-verify`, `commit -a`, staged clutter and Conventional Commit messages.
- `no-git-slop` CLI with `check`, `commit-msg`, `commits` and `init`.
- pre-commit hooks, a GitHub Actions template and an AGENTS.md template.
- `tidy` skill for an end-of-task cleanup pass.
