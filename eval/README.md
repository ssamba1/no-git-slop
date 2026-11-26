# Evaluation

v2, with untracked local files planted in the tree, is in [v2/](v2/README.md). This page is v1.

A four-arm test of whether no-git-slop changes what Claude Code leaves in a repository. The design and its pass bars are in [preregistration.md](preregistration.md), committed before the first paid session.

```
python -m pytest eval/test_score.py          # scorer decoys, free
python eval/run.py --tasks pilot --trials 1 --out eval-runs/pilot
python eval/summarize.py eval-runs/pilot
```

`run.py` copies [fixture/](fixture/) (a small invoice-parsing package) into a fresh temp repo per trial and runs one headless `claude -p` session per task and arm. `score.py` counts new files that are not git-ignored and match none of the task's allowlist, plus copy and throwaway names (`*_v2.py`, `debug_*.py`, `*.bak`) anywhere, and reads the transcript for bulk staging that actually ran. It doesn't use no-git-slop's own rules, so the plugin is not graded by itself.

## Pilot, 2026-11-26

12 sessions: 3 tasks × 4 arms × 1 trial, `claude-sonnet-5-5`, Claude Code 2.1.303 on Windows. Raw scores in [results/pilot-2026-11-26/](results/pilot-2026-11-26/). Total cost $1.53 (mean $0.13 per session).

| arm | stray files | bulk staging that ran | conventional commits | hidden checks passed | cost |
|---|---|---|---|---|---|
| baseline | 0 in 3 trials | 2 of 3 | 2 of 2 | 3 of 3 | $0.31 |
| plugin | 0 | 0 | 2 of 2 | 3 of 3 | $0.45 |
| prose (rules as CLAUDE.md) | 0 | 0 | 2 of 2 | 3 of 3 | $0.32 |
| placebo | 0 | 1 of 3 | 2 of 2 | 3 of 3 | $0.45 |

What it shows, and what it doesn't:

- **No arm left a stray file.** Neither did 6 earlier probe sessions without the plugin. On short, well-specified tasks, this model doesn't produce the report files and copies the plugin is built to catch, so these tasks can't measure the primary outcome. The preregistered primary bar (plugin ≤ 50% of baseline) can't be met when the baseline is 0.
- **Bulk staging differed** (baseline 2 of 3, plugin 0, prose 0), but no hook fired in any plugin trial: those sessions staged by path on their own. The plugin's session-start reminder did the same work as writing the rules into `CLAUDE.md`. With 3 trials per arm this is not evidence of anything.
- Task success was unaffected (all hidden checks passed) and nothing was blocked by mistake.

Decision: the full 96-session run was **not** started. At about $12 it is cheap, but it would score 0 against 0 on the primary outcome. The next version of this evaluation needs tasks where agents do leave slop: long multi-step sessions, vague requests, and work that runs into failures. That's a new preregistration, not an edit to this one.

Limits: one model, one OS, tasks written by the same AI session that built the plugin, and the operator's user-level `CLAUDE.md` loaded in every arm (confirmed later; v2 excludes it with `claudeMdExcludes`).
