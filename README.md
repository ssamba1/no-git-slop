# no-git-slop

Stop AI coding agents from cluttering your repo, without getting in your way.

Writing "don't create summary files" in `CLAUDE.md` doesn't work: users report agents ignoring always-on rules, and Anthropic's own docs say anything that must happen every time belongs in a hook. no-git-slop is a Claude Code plugin that checks every route a file can take onto disk or into git history, *before* it happens. Each block tells Claude where the file should go instead. It says yes to you and no to the agent's leftovers: anything you ask for by name goes through.

## What it catches

| Slop | Example | What Claude is told |
|---|---|---|
| Status-report docs | `IMPLEMENTATION_SUMMARY.md`, `FIX_NOTES.md` | Report in chat; real docs go in README.md or docs/ |
| Copies instead of edits | `parser_fixed.py`, `api_v2.py` next to `api.py`, `*.bak`, `NuclearFix/` | Edit the original in place |
| Renamed copies | `helpers.py` that's 90% identical to `utils.py` | Reuse or edit the existing file |
| Throwaway scripts | `debug_auth.py`, root-level `test_login.py` | Use `.scratch/` (git-ignored) or write a real test |
| Junk | `.DS_Store`, `*.log`, `*.pyc` | Don't create; git-ignore it |
| Shell-written slop | `cat > SUMMARY.md <<EOF`, `cp app.py app_backup.py`, `python -c "open('tmp.py','w')"`, inside `bash -c`, `env -C`, `sudo` | Same rules as the Write tool |
| Bulk staging | `git add -A`, `.`, `:/`, `*`, `git commit -a`, `git add src/` with junk inside | Stage the files you changed, listed for you |
| Bad commits | `updated stuff`, `fix: fix bug`, two changes in one subject, 40-file commits, agent-added `Signed-off-by` | Conventional Commits, one change each |
| Bad pushes and PRs | pushing `wip` commits, force-push, PRs from `main`, "I hope this helps" in PR text, summary files in the PR | Fix before it leaves your machine |
| Turning the guard off | `--no-verify`, `HUSKY=0`, `SKIP=...`, `core.hooksPath`, deleting `.git/hooks`, editing its config | Fix what the hooks report |
| Unfinished work | your `verify` command (tests, lint) failing | Fix it without weakening tests |

Editing existing files is never blocked. Git-ignored paths are never slop.

## How it works

One rules engine, enforced at every checkpoint:

1. **You ask** (UserPromptSubmit). It records files you name and what you ask for. "Save the report to `docs/audit.md`" allows exactly that file.
2. **Claude writes** (PreToolUse on Write/Edit). New files are checked against the rules, including a near-duplicate check against existing files.
3. **Claude runs a command** (PreToolUse on Bash). A static shell parser finds every file the command would create and every git and `gh` call inside it, unwrapping heredocs, pipes, `bash -c`, `env`, `sudo`, `timeout` and `cd`, up to five levels deep. Nothing is executed.
4. **After a commit** (PostToolUse). It re-checks the commit that was actually made, which catches messages from `-F` files and editors.
5. **Claude tries to finish** (Stop). It sweeps `git status` for clutter created this session and runs your `verify` command. It sends Claude back once with one list, and never blames Claude for files Claude Code itself creates (sandbox stubs, `tmpclaude-*`).
6. **Outside Claude.** The same engine runs as pre-commit, commit-msg and pre-push hooks and in CI, so humans and other agents hit the same rules.

Decisions are `deny`, `ask` (you confirm), `warn` (Claude is told, nothing blocked) or `off`, set per rule.

## Saying yes to the user

- **Name a file, get the file.** Paths and file names in your prompt are allowed for the session. Backups still ask for confirmation.
- **Ask for a kind of file, get asked.** "Write a summary doc" turns blocks on report files into a confirmation prompt.
- **Approve one file.** Every block includes a code like `ngs-3fa9c1`. Reply with it and that file is allowed.
- **Session tokens.** Type `>ngs allow notes/*.md` to allow a pattern for the session, or `>ngs off` / `>ngs on` to pause the file and commit checks. Self-protection stays on.

Only text you type counts. Claude's own output can't approve anything.

## Works with planning frameworks

If a framework's folder exists, its plan files are allowed where it expects them: Spec Kit, OpenSpec, Superpowers, planning-with-files, HumanLayer, Task Master, ccpm, Ralph, claude-flow, compound-engineering, Kiro, Agent OS, BMAD and beads. Set `"plans": "ignore"` to keep plan files out of commits. `no-git-slop archive <file>` moves finished plans into a dated `archive/` folder next to them.

## Install

**Claude Code plugin** (needs `python3`, 3.9 or newer):

```
/plugin marketplace add OWNER/no-git-slop
/plugin install no-git-slop@no-git-slop
```

The scratch folder is git-ignored automatically through `.git/info/exclude`, so your `.gitignore` isn't touched.

**Command line, git hooks and CI:**

```
pip install "git+https://github.com/OWNER/no-git-slop"
no-git-slop init --hooks     # git-ignores .scratch/, installs pre-commit, commit-msg and pre-push hooks
```

- pre-commit framework: copy [`templates/.pre-commit-config.yaml`](templates/.pre-commit-config.yaml).
- GitHub Actions: copy [`templates/github-workflow.yml`](templates/github-workflow.yml). It checks new files, commit messages and the PR description.
- Other agents (Cursor, Codex, Gemini): copy [`templates/AGENTS.md`](templates/AGENTS.md), and pipe their patches through `no-git-slop check-patch` (it reads Codex `*** Add File:` and git diff formats).

Replace `OWNER` with the GitHub account that hosts this repo.

## Commands

```
no-git-slop check [--staged | --range A..B] [--deep] [--json]   judge new files
no-git-slop commit-msg FILE | commits A..B | push-check         commit and push checks
no-git-slop pr --title T --body-file F                          self-check a PR before opening it
no-git-slop check-patch < patch.diff                            judge new files in a patch
no-git-slop index [dir]                                         list existing functions to reuse
no-git-slop explain [rule]                                      what a rule does and how to change it
no-git-slop stats                                               what the guard has been catching
no-git-slop archive FILE                                        archive a finished plan file
no-git-slop doctor                                              what it sees in this repo
no-git-slop init [--hooks]                                      set up scratch folder and git hooks
```

`ngs` is a short alias. `--json` output gives each finding a rule, decision, reason and fix, plus `next_steps`, for agents to act on.

## Agents and skills

- `/no-git-slop:tidy`: an end-of-task cleanup pass.
- `/no-git-slop:commit`: split work into small Conventional Commits.
- `/no-git-slop:learn`: tune the config from what the guard has been catching. Proposes changes; you approve.
- `slop-minimizer` agent: strips changes the task doesn't need and keeps tests green. A separate pass works; asking the same agent to be minimal doesn't.
- `slop-reviewer` agent: a fresh, read-only review for correctness, scope and reuse, never style. Every finding must come with evidence.

## Configure

Optional `.no-git-slop.json` at the repo root ([full example](templates/.no-git-slop.json)):

```json
{
  "profile": "default",
  "rules": { "root-doc": "warn", "commit-multi": "off" },
  "allow": ["docs/adr/*.md"],
  "verify": "npm test",
  "attribution": "ignore"
}
```

- `profile`: `relaxed` (most blocks become questions), `default`, or `strict`.
- `rules`: per-rule `deny | ask | warn | off`. Run `no-git-slop explain` for the list. Every rule can be turned off, including the report-file rule.
- `verify`: a command that must pass before Claude finishes a turn that changed files. It gets at most `verifyMaxRetries` retries.
- `attribution`: `ignore`, `forbid` (no AI co-author trailers) or `require` (an `Assisted-by:` trailer).
- `integrations`: if [jscpd](https://github.com/kucherenko/jscpd), vulture, knip or aislop are installed, `check --deep` runs them. Each reports ran, unavailable or skipped, never a silent pass.
- Budgets: `newFileBudget`, `commitFileBudget`, `commitLineBudget`, `diffBudget`, `nearDuplicateThreshold`, `prMaxFiles`.

A broken config falls back to the defaults. Hooks fail open on internal errors, and the Stop sweep, git hooks and CI are the backstops. Claude editing the config asks you first.

## Limits

- It can't make code good. It catches the repo-level mess, and points to the tools and agents for the rest.
- Duplicated *logic* inside files needs jscpd or similar (`check --deep`). The built-in near-duplicate check compares whole files.
- Shell commands built at runtime (variables, `$(...)`, globs) can't be read statically. Those files are caught by the Stop sweep.
- Name-based rules will occasionally misfire. Approve with the code, or add an `allow` glob.
- Nobody has measured stray-file rates for any tool, including this one. `no-git-slop stats` keeps a local record (never sent anywhere) so you can.

## Research

The design comes from three research rounds: [why rules fail and hooks work](docs/research/preventing-ai-git-slop.md), [the best-supported method](docs/research/perfect-method.md), and [a survey of similar tools on GitHub](docs/research/github-survey.md).

## License

MIT
