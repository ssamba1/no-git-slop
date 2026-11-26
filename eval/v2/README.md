# Evaluation v2: planted local files

Design and bars: [preregistration.md](preregistration.md), frozen before the first priced v2 session. Raw scores in [results/](results/). All sessions ran on Claude Code 2.1.303 on Windows, with the operator's `~/.claude/CLAUDE.md` excluded.

```
python eval/run.py --tasks-file eval/v2/tasks.json --tasks pilot --plant --out eval-runs/v2-pilot
python eval/summarize.py eval-runs/v2-pilot
```

## Probes, baseline only, clean tree

12 sessions on the 6 vaguer tasks in [probe-tasks.json](probe-tasks.json) ("profile it", "try a couple of approaches", "investigate"), no plugin, no planted files.

| model | stray files | sessions with a bulk add | bulk adds | cost |
|---|---|---|---|---|
| `claude-sonnet-5-5` | 0 | 6 of 6 | 11 | $0.76 |
| `claude-haiku-5-5` | 0 | 0 of 6 | 0 | $0.10 |

Even tasks that invite benchmark scripts and copies left nothing behind: the scratch work went through `python -c` or outside the repo. An earlier round of the same 12 sessions was discarded because it loaded the operator's own `CLAUDE.md`. It gave the same picture: 0 stray files, and Sonnet bulk-added in 5 of 6.

## Pilot, planted files

12 sessions: 3 tasks × 4 arms × 1 trial, Sonnet, with `customers-2026-10.csv`, `.env` and `notes.txt` left untracked in the repo root. Cost $1.61.

| arm | planted file committed or staged | bulk adds that ran | stray files | hidden checks | cost |
|---|---|---|---|---|---|
| baseline | 0 of 3 | 0 | 0 | 3 of 3 | $0.43 |
| plugin | 0 of 3 | 0 | 0 | 3 of 3 | $0.45 |
| prose | 0 of 3 | 0 | 0 | 3 of 3 | $0.35 |
| placebo | 0 of 3 | 0 | 0 | 3 of 3 | $0.38 |

**Pilot bar missed** (it needed at least 1 of 3 baseline trials to commit a planted file), so the full 112-session run was not started. The plugin loaded in all 3 plugin trials.

The interesting part is *why*. With untracked files in the tree, no Sonnet session in any arm ran `git add -A` or `git add .`. The baseline sessions staged by path (`git add CHANGELOG.md src/...`) or by folder (`git add src tests`). In the probes, with a clean tree, the same model bulk-added in every session. So the model bulk-adds when that is harmless and stages carefully when it isn't. Claude Code puts `git status` in the session context, which is a likely reason; that is not tested here.

## Codex, baseline only

9 sessions of Codex CLI 0.155.1 (`codex exec`, model `gpt-6-sol`, no plugin): the 6 probe tasks in a clean tree, then the 3 pilot tasks with the planted files. `CODEX_HOME` was a fresh directory holding only the login file, so the operator's `AGENTS.md`, hooks and `config.toml` stayed out; a control session confirmed no `AGENTS.md` was loaded. The operator's user-level skill catalog (`~/.agents/skills`) still loaded, and there is no flag to turn it off. Command: `python eval/run.py --agent codex --codex-home <dir> --model gpt-6-sol --arms baseline ...`.

| set | stray files | bulk adds | planted file committed or staged | hidden checks |
|---|---|---|---|---|
| probes, clean tree | 0 | 0 | n/a | 6 of 6 |
| pilot, planted files | 0 | 0 | 0 of 3 | 3 of 3 |

Codex staged by path in every session (`git add -- README.md src/...`), even in a clean tree where Sonnet bulk-added. It wrote no conventional commit messages except on four-steps, where it made one `type: subject` commit per step. Codex reports tokens, not cost; these ran on a ChatGPT subscription, so there is no dollar figure.

## What v1 and v2 add up to

On short headless tasks, neither current Claude models (Sonnet and Haiku 5.5) nor Codex (`gpt-6-sol`) produce the repo clutter no-git-slop is built to stop: 0 stray files in 51 sessions across v1, v2, the probes and the Codex runs, and no harmful bulk staging. That doesn't show the plugin is useless. It shows that this kind of eval can't find the cases where it would matter, if they exist: long interactive sessions, context pressure late in a session, older or weaker models, agents other than Claude Code and Codex, and humans running the git hooks. None of those were measured. Until one is, the honest claim is that no-git-slop is a cheap backstop with no measured benefit for Claude on short tasks.

Limits: one OS, one fixture, tasks written by the same AI session that built the plugin, three planted files of one kind, and n=1 per cell in the pilot.

Total spend for v2 including the discarded probe round: about $3.40.
