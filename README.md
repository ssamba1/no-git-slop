# no-git-slop

Keeps AI coding agents from making a mess of your repo: stray files, copies, report dumps, odd folders, bloated agent instructions, AI-style commit messages and code habits. It stays quiet unless something is wrong, and anything you ask for by name goes through.

Rules in `CLAUDE.md` get ignored; Anthropic's docs say anything that must happen every time belongs in a hook. no-git-slop is a Claude Code plugin that checks every file write, shell command and commit *before* it lands, and tells the agent where things go instead.

## What it catches

| Problem | Example | Default |
|---|---|---|
| Status-report docs | `IMPLEMENTATION_SUMMARY.md`, `FIX_NOTES.md` | block |
| Copies instead of edits | `parser_fixed.py`, `api_v2.py` next to `api.py`, `*.bak`, `NuclearFix/`, a 90% copy of `utils.py` | block |
| Throwaway scripts and junk | `debug_auth.py`, root `test_login.py`, `.DS_Store`, `*.log` | block |
| Odd layout | a new top-level `analysis/` folder, `src/test_parser.py` when `tests/` exists | warn |
| Agent leftovers in git | aider chat history, SpecStory transcripts, `CLAUDE.local.md`, `.claude/settings.local.json` | block on commit |
| Bloated or stale agent instructions | `CLAUDE.md`/`AGENTS.md` over 200 lines, paths in them that no longer exist | told at session start |
| Bulk staging | `git add -A`, `.`, `git commit -a` | block |
| Bad commits | `updated stuff`, two changes in one subject, 40-file commits | block |
| AI-style commit messages | em dashes, `citeturn` and `utm_source=chatgpt.com` leftovers, "I hope this helps" | block |
| AI-style docs | spaced em dashes, chat phrases, hype words, ", highlighting its importance" | warn |
| AI-style code | `// ... rest of the code unchanged` (block); comments about the chat, swallowed exceptions, `as any`, `# type: ignore`, skipped tests, debug leftovers (warn) | block / warn |
| Turning the guard off | `--no-verify`, `HUSKY=0`, `core.hooksPath` | block |

Editing existing files is never blocked, git-ignored paths are never slop, and writing and code checks only look at the text just written, never at what was already there.

## Light by design

- A new session gets six short rules (about 550 characters). Tests fail if that or the plugin's skill descriptions grow.
- The 200-line limit is Anthropic's own guidance: "target under 200 lines per CLAUDE.md file" ([Claude Code docs](https://code.claude.com/docs/en/memory)).
- Hooks import only what they need; the slow modules load only when a check needs them.
- Warnings never stop the agent. Only clear junk is blocked, and every rule can be turned off.

## Saying yes to the user

- **Name a file, get the file.** Paths in your prompt are allowed for the session.
- **Approve one file.** Every block includes a code like `ngs-3fa9c1`; reply with it.
- **Session tokens.** `>ngs allow notes/*.md` allows a pattern; `>ngs off` / `>ngs on` pauses the checks.

Only text you type counts. The agent's own output can't approve anything.

## Install

**Claude Code** (needs Python 3.9+; Windows works through Git Bash):

```
/plugin marketplace add ssamba1/no-git-slop
/plugin install no-git-slop@no-git-slop
```

**Git hooks and CI**, so people and other tools hit the same rules:

```
pip install "git+https://github.com/ssamba1/no-git-slop"
no-git-slop init --hooks     # pre-commit, commit-msg and pre-push hooks
```

Or copy [`templates/.pre-commit-config.yaml`](templates/.pre-commit-config.yaml) or [`templates/github-workflow.yml`](templates/github-workflow.yml).

**Codex CLI, Gemini CLI and Cursor:**

```
no-git-slop init --agent codex      # writes .codex/hooks.json
no-git-slop init --agent gemini     # adds hooks to .gemini/settings.json
no-git-slop init --agent cursor     # writes .cursor/hooks.json
```

Codex runs project hooks only after you trust the project and approve each hook in `/hooks` ([Codex hooks](https://learn.chatgpt.com/docs/hooks)). The Codex adapter was tested live; Gemini and Cursor only against their documented formats.

## Commands

```
no-git-slop check [--staged | --range A..B] [--deep] [--json]   judge new files
no-git-slop commit-msg FILE | commits A..B | push-check         commit and push checks
no-git-slop pr --title T --body-file F                          self-check a PR before opening it
no-git-slop index [dir]                                         list existing functions to reuse
no-git-slop explain [rule]                                      what a rule does and how to change it
no-git-slop doctor                                              what it sees in this repo
```

`ngs` is a short alias. Skills: `/no-git-slop:tidy` (end-of-task cleanup), `/no-git-slop:commit` (split work into small commits), `/no-git-slop:learn` (tune the config). Agents: `slop-minimizer` (strip what the task didn't need) and `slop-reviewer` (read-only review).

## Configure

Optional `.no-git-slop.json` at the repo root ([full example](templates/.no-git-slop.json)):

```json
{
  "profile": "default",
  "rules": { "new-top-dir": "off", "commit-em-dash": "warn" },
  "allow": ["docs/adr/*.md"],
  "verify": "npm test",
  "contextFileMaxLines": 200
}
```

- `profile`: `relaxed`, `default` or `strict`. `rules`: per-rule `deny | ask | warn | off` (`no-git-slop explain` lists them).
- `verify`: a command that must pass before the agent finishes a turn that changed files.
- `plans`: framework plan files (Spec Kit, Superpowers, Task Master and others) stay out of commits by default; set `"commit"` to keep them.
- `attribution`: `ignore`, `forbid` (no AI co-author trailers) or `require`.

A broken config falls back to the defaults, and hooks fail open on internal errors.

## Limits

- It catches repo-level mess and the AI code habits above. It can't make code good; `check --deep` runs jscpd, vulture or knip if installed.
- Shell commands built at runtime can't be read statically; the end-of-turn sweep catches those files.
- The writing and code checks are patterns, so humans trip some warnings too. On 738 Python standard library files, scanned whole, none was blocked and 25 got a warning.
- On short lab tasks, current Claude and Codex models rarely made these mistakes, so the measured benefit there is zero. It is a backstop for long sessions, weaker models, other agents and people. The evaluations and research are on the [`research` branch](https://github.com/ssamba1/no-git-slop/tree/research).

## License

MIT
