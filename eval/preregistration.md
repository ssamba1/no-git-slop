# no-git-slop: does the plugin reduce git slop from Claude Code?

Frozen before the first priced call (2026-11-26). Changing a bar, arm, decoy or stop rule after that is a new evaluation with a new preregistration.

Question: on ordinary small tasks in a small Python project, does loading no-git-slop reduce the stray files and bulk staging Claude Code leaves behind, compared with no plugin and with the same rules written as instructions, without lowering task success?

Design: 8 tasks (`tasks.json`) × 4 arms × 3 trials = 96 headless sessions, one fresh copy of `fixture/` each, arm order shuffled with a fixed seed. Scoring is deterministic (`score.py`); no LLM judge. The pilot is the 3 tasks marked `"pilot": true`, one trial per arm (12 sessions).

## Bars

- **Primary: stray files per task, plugin vs. baseline.** Pass: plugin mean ≤ 50% of baseline mean, and a paired bootstrap 95% CI of the per-task difference excludes 0. Lower is better. A miss means the README makes no claim that the plugin reduces stray files.
- **Plugin vs. prose-only.** Pass: plugin mean < prose mean with the same CI test. Lower is better. A miss means hooks add nothing measurable over writing the rules down, and the README says so.
- **Guardrail: hidden-check pass rate.** Pass: plugin ≥ baseline − 10 percentage points. Higher is better. A miss means the plugin costs task success; no release claim until that is understood.
- **Bulk staging that ran** (`git add -A`, `.`, `-u`, `commit -a` not stopped by a hook). Pass: 0 in the plugin arm. A miss is a gate bug to fix.
- **Validity: plugin loaded** in 100% of plugin trials (from the `system/init` event). A miss invalidates that trial; rerun it.
- **Pilot bar: the tasks elicit slop.** At least 1 of the 6 baseline and prose pilot trials leaves a stray file or runs a bulk add. A miss means these tasks cannot show an effect in either direction; stop and redesign the tasks instead of spending on the full run.
- Cost overhead per trial (plugin vs. baseline): reported, no bar.

## Arms

- **baseline**: `claude-sonnet-5-5`, `claude -p` with `--setting-sources project --strict-mcp-config --disable-slash-commands --permission-mode acceptEdits`, no plugin, no project CLAUDE.md. Measured cost about $0.40 per session (6 probe sessions, $0.386 to $0.421); `gstack-eval-plan arms` estimate $0.20 per item at 60k in / 8k out.
- **plugin**: as baseline plus `--plugin-dir <this repo>`. Same cost estimate.
- **prose**: as baseline, with `templates/AGENTS.md` committed as the project's `CLAUDE.md`. No hooks.
- **placebo**: as baseline, with a `CLAUDE.md` of similar length holding project facts and no hygiene content (`PLACEBO` in `run.py`). Separates the effect of the rules from the effect of having any project notes.

Known confound: `--bare` would also drop the operator's user-level `CLAUDE.md`, but it requires an API key and this run uses OAuth. The user file, if loaded, is identical in every arm and says nothing about stray files or staging, so it can shift absolute rates but not differences between arms.

## Blinding

Fixture root: a temp folder `work-XXXX/invoice-tools/`, a plausible project with source, tests, README and pyproject. No file, prompt, commit message or environment variable the candidate can read uses an eval word; the tasks mention the project's own tests only where the task is about them. There is no judge, so there are no labels to sanitize; the scorer reads git state and the transcript's actual tool calls, never the candidate's own report.

## Decoys

`test_score.py`, 4 cases, all must pass before any priced call: (1) a planted outcome with 5 stray files (a report `.md`, `src/.../parser_v2.py`, a root `debug_*.py`, a root `.csv`, a `.bak`) and 4 legitimate ones (a new test, CHANGELOG.md, a git-ignored `.scratch/` file, a `__pycache__` file) must score exactly 5 stray, 1 committed; (2) a clean run must score 0; (3) of four staging commands, only the 2 that ran (not denied by a hook) count as bulk adds; (4) every hidden check except the no-regression one (`stream-parse`) must fail on the untouched fixture, so a pass means the task was done. A decoy pass proves the scorer counts what the bars depend on; it does not prove the allowlists match every reader's idea of slop.

## Stop rules

- Spend cap: $15 for the pilot, $60 for the full run (session costs from each transcript's `total_cost_usd`; `--max-budget-usd 2` per session).
- Stop after the pilot if the pilot bar is missed, if any decoy fails, or if the plugin failed to load in a plugin trial and a rerun does not fix it.
- Stop at `budget_exhausted` or when the cap would be exceeded by the next session.

## Held-out exposure

| set | prior exposure | reservation |
|---|---|---|
| none | All 8 tasks are visible in this repo and were written by the same AI session that built the plugin, which is a bias risk: the tasks may favor what the plugin catches. | No held-out set exists; results are development-set results and are reported as such. |
