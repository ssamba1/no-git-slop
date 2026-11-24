---
name: commit
description: Use when committing work, to split changes into small atomic commits with Conventional Commit messages, staging files by path. Also use when asked to clean up or split a messy set of changes before a PR.
---

# Commit in small, honest pieces

1. Run `git status --short` and `git diff --stat`. Group the changes into logical units: one bug fix, one feature, one refactor, one docs change. Keep moves and renames in their own commit, separate from logic changes.
2. For each unit, in dependency order:
   - Stage it by path: `git add path/a.py tests/test_a.py`. Never `git add -A`, `git add .` or `git commit -a`.
   - If one file holds two units, split it: use `git add -p` only if you can answer the prompts non-interactively; otherwise commit the file with the unit it mostly belongs to and say so.
   - Check what you're about to commit: `git diff --cached --stat`.
   - Commit with a Conventional Commit message: `type(scope): what changed`, under 72 characters, imperative mood. Add a body only to explain *why*. Example: `fix(auth): reject expired refresh tokens`.
3. Don't add `Signed-off-by` for the user, don't use `--no-verify`, and follow the project's attribution policy for co-author trailers.
4. If a hook rejects a commit, fix the cause and commit again. Don't bypass it.
5. Before pushing, run `no-git-slop push-check`. Fix review feedback into the commit it belongs to (`git commit --fixup <sha>` then `git rebase -i --autosquash` if the user agrees, or `git absorb` if installed).

Before `gh pr create`, run `no-git-slop pr --title "..." --body-file <file>`: a short description (what changed, why, how it was tested), no emoji, no chat phrases, from a feature branch.
