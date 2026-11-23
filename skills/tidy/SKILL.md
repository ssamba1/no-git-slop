---
name: tidy
description: Use when finishing a task, before committing, or when the user asks to tidy, clean up, or audit a repository for clutter such as stray files, duplicate code, backup copies or status-report docs.
---

# Tidy the repo

Work through these steps in order. Report what you changed in chat; do not write a summary file.

1. **Find new clutter.** Run `no-git-slop check` (or `python3 "${CLAUDE_PLUGIN_ROOT}/bin/no-git-slop" check`). For each flagged file: delete it, merge its useful content into an existing file, or move throwaway work into `.scratch/`.
2. **Review `git status`.** Every untracked or modified file must belong to the task. Revert edits you didn't need.
3. **Look for duplication you introduced.** For each new function, search the repo for an existing one that does the same job (`grep -rn "def name\|function name"`). Reuse it and delete the copy.
4. **Check docs.** User-facing changes update README.md or the right page in docs/. Don't add new top-level .md files.
5. **Commit in small pieces.** Stage files by path. One logical change per commit, with a Conventional Commit message: `type(scope): what changed`. Split unrelated changes into separate commits.

If a check blocks something that is genuinely needed, tell the user and suggest an `allow` glob in `.no-git-slop.json` rather than working around the check.
