# Repo hygiene

- Edit existing files in place. Never create `*_v2`, `*_new`, `*_fixed`, `*.bak` or other copies.
- Don't write summary, notes or report files. Report what you did in chat.
- Throwaway scripts and notes go in `.scratch/` (git-ignored). Delete them when done.
- Before writing a new function, search for an existing one and reuse it.
- Docs live in README.md or docs/. Don't add new top-level .md files.
- Stage files by path (never `git add -A` or `git add .`). One logical change per commit.
- Commit messages follow Conventional Commits: `type(scope): what changed`.
