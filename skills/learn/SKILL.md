---
name: learn
description: Use when the user wants to tune no-git-slop for their project, asks why something keeps getting blocked, or wants to review what the guard has been catching.
---

# Tune no-git-slop from evidence

1. Run `no-git-slop stats` to see which rules fired, how often, and which paths keep getting flagged. Run `no-git-slop doctor` to see the active profile, presets and tools.
2. Sort repeat findings into two groups:
   - **False positives**: real project files that keep getting blocked (for example `docs/adr/0007-*.md`). Propose an `allow` glob for each.
   - **Real slop the agent keeps producing**: propose nothing to loosen. Instead suggest a line for AGENTS.md that names where those files should go.
3. If a whole rule is wrong for this project, propose a per-rule mode in `"rules"` (for example `"root-doc": "warn"`), or a profile (`relaxed`, `default`, `strict`). Explain each rule with `no-git-slop explain <rule>`.
4. Show the user the exact JSON change to `.no-git-slop.json` and wait for their approval. Editing that file asks for confirmation by design; never loosen it without the user's say-so.
