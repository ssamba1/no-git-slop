---
name: tidy
description: Use when finishing a task, before committing, or when the user asks to tidy, clean up, or audit a repository for clutter such as stray files, duplicate code, backup copies, status-report docs or oversized diffs.
---

# Tidy the repo

Work through these steps in order. Report what you changed in chat; don't write a summary file.

1. **Find clutter.** Run `no-git-slop check --json`. For each finding: delete the file, fold its useful content into an existing file, or move throwaway work into `.scratch/`. Files listed as harness artifacts were made by Claude Code; leave them.
2. **Check `git status`.** Every untracked or modified file must belong to the task. Revert edits the task didn't need.
3. **Shrink the diff.** If the diff is large, use the `slop-minimizer` agent with the task description and base branch. It reverts unneeded hunks and keeps tests green.
4. **Remove duplication you introduced.** For each new function, run `no-git-slop index <dir>` (or grep) for an existing one that does the same job. Reuse it and delete the copy.
5. **Verify.** Run the project's tests and linters, and show the output. Don't claim success without it.
6. **Review with fresh eyes.** For anything non-trivial, use the `slop-reviewer` agent. Fix only findings it verified.
7. **Docs.** User-facing changes update README.md or the right page in docs/. No new top-level .md files.
8. **Plans.** If a planning framework left finished plan files, run `no-git-slop archive <path>` rather than deleting them.
9. **Commit** with the `commit` skill: small commits, one logical change each.

If a check blocks something genuinely needed, tell the user. They can approve it with the code in the block message, or add an `allow` glob to `.no-git-slop.json`. Don't work around the check.
