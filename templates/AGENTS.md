# Repo hygiene

- Every changed line should trace to the request. Mention unrelated problems; don't fix them unasked.
- Edit existing files in place. Never create `*_v2`, `*_new`, `*_fixed`, `*.bak` or other copies.
- Before writing a new function, look for an existing one (`no-git-slop index <dir>` or grep) and reuse it.
- Don't write summary, notes or report files. Report what you did in chat.
- Throwaway scripts and notes go in `.scratch/` (git-ignored). Delete them when done.
- Docs live in README.md or docs/. Don't add new top-level .md files.
- Done means the project's tests and linters pass, and you've shown the output.
- Stage files by path (never `git add -A` or `git add .`). One logical change per commit.
- Commit messages follow Conventional Commits: `type(scope): what changed`.
- Never use `--no-verify` or otherwise disable hooks. Fix what they report.
