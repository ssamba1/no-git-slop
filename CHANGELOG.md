# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
