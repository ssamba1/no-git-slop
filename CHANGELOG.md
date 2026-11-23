# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-23

### Added

- Rules engine for new files: status-report docs, backup and copy files, copy folders, throwaway scripts and junk files.
- Claude Code plugin with SessionStart, PreToolUse (Write and Bash) and Stop hooks.
- Git command checks: bulk adds, `--no-verify`, `commit -a`, staged clutter and Conventional Commit messages.
- `no-git-slop` CLI with `check`, `commit-msg`, `commits` and `init`.
- pre-commit hooks, a GitHub Actions template and an AGENTS.md template.
- `tidy` skill for an end-of-task cleanup pass.
