# no-git-slop

Stop AI coding agents from cluttering your repo.

Rules in `CLAUDE.md` get ignored. Studies find that agents follow context files closely but still drift as rules pile up, and Anthropic's own docs say anything that must happen every time belongs in a hook. So no-git-slop doesn't ask Claude to be tidy. It blocks the mess at the moment it's created, tells Claude where the file should go instead, and checks again before the turn ends, before each commit and in CI.

## What it blocks

| Slop | Example | What Claude is told to do |
|---|---|---|
| Status-report docs | `IMPLEMENTATION_SUMMARY.md`, `FIX_NOTES.md` | Report in chat; real docs go in README.md or docs/ |
| Copies instead of edits | `parser_fixed.py`, `api_v2.py` next to `api.py`, `*.bak`, `NuclearFix/` folders | Edit the original in place |
| Throwaway scripts | `debug_auth.py`, root-level `test_login.py` | Use `.scratch/` (git-ignored) or write a real test |
| Junk files | `.DS_Store`, `*.log`, `*.pyc` | Don't create; git-ignore it |
| Bulk staging | `git add -A`, `git add .`, `git commit -a` | Stage files by path |
| Skipped checks | `git commit --no-verify` | Fix what the hooks report |
| Vague commits | `updated stuff`, `fix: update` | Conventional Commits: `fix(api): handle empty token` |

New top-level docs that aren't on the standard list (README, CHANGELOG, CONTRIBUTING, SECURITY and so on) prompt you to confirm instead of being blocked. Editing existing files is never blocked.

## How it works

One rules engine, four checkpoints:

1. **Session start.** Records which untracked files already exist, so it only ever flags files Claude creates. It also gives Claude a five-line reminder (re-sent after context compaction).
2. **Before every `Write`.** New files are checked against the rules. Slop is denied with a reason that names the right place.
3. **Before every `git` command.** Blocks bulk adds, `--no-verify`, `commit -a`, staged clutter and bad commit messages.
4. **When Claude tries to finish.** Sweeps `git status` for clutter created this session, including files written through shell commands that skip the Write check, and sends Claude back to clean it up. It only asks once per turn, so it can't loop.

The same engine runs as a pre-commit hook and in CI, so humans and other agents (Cursor, Codex) hit the same rules.

## Install

**Claude Code plugin** (needs Python 3.9+ on your PATH as `python3`):

```
/plugin marketplace add OWNER/no-git-slop
/plugin install no-git-slop@no-git-slop
```

Then, in each repo, ask Claude to run `no-git-slop init` (the plugin puts the command on Claude's PATH). It git-ignores the `.scratch/` folder.

**Command line, pre-commit and CI:**

```
pip install "git+https://github.com/OWNER/no-git-slop"
```

- pre-commit: copy [`templates/.pre-commit-config.yaml`](templates/.pre-commit-config.yaml), then `pre-commit install --hook-type pre-commit --hook-type commit-msg`.
- GitHub Actions: copy [`templates/github-workflow.yml`](templates/github-workflow.yml) to `.github/workflows/no-git-slop.yml`.
- Other agents: copy [`templates/AGENTS.md`](templates/AGENTS.md) into your repo. It holds the same rules as plain text.

Replace `OWNER` with the GitHub account that hosts this repo.

## Commands

```
no-git-slop check                 # untracked files in the working tree
no-git-slop check --staged        # files staged for commit
no-git-slop check --range A..B    # files added between two revisions
no-git-slop commit-msg FILE       # validate one commit message
no-git-slop commits A..B          # validate every commit in a range
no-git-slop init                  # git-ignore the scratch folder
```

Inside Claude Code, `/no-git-slop:tidy` runs a cleanup pass: clutter, `git status`, duplicated functions, docs and commit splitting.

## Configure

Optional `.no-git-slop.json` at the repo root:

```json
{
  "allow": ["docs/adr/*.md"],
  "deny": ["*.ipynb"],
  "scratchDir": ".scratch",
  "blockBulkAdd": true,
  "commitStyle": "conventional",
  "maxSubjectLength": 72
}
```

Set `"commitStyle": "off"` if your project doesn't use Conventional Commits. A broken config falls back to the defaults, and hooks fail open on errors, so no-git-slop can never wedge a session.

## Limits

- It can't make code good. It catches the repo-level mess: stray files, copies, junk, sprawling commits.
- Duplicated *logic* inside files isn't detected. For that, add [jscpd](https://github.com/kucherenko/jscpd) to CI and use the `tidy` skill.
- Name-based rules will occasionally misfire. Add an `allow` glob rather than switching the plugin off.

## Why

The research behind the design (context-file studies, instruction-following limits, AI code-quality data, Claude Code's hook system and prior art) is summarized in [docs/research.md](docs/research.md).

## License

MIT
