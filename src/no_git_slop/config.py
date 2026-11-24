"""Project configuration: .no-git-slop.json at the repo root.

Every key is optional. A missing or broken file means defaults, so a typo
can never wedge a session.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List

from .findings import RULES

CONFIG_FILE = ".no-git-slop.json"

PROFILES = ("relaxed", "default", "strict")
_FILE_CATEGORIES = {"file", "doc", "version", "backup", "script"}


@dataclass
class Config:
    # Strictness preset; per-rule "rules" overrides win over it.
    profile: str = "default"
    # rule id -> deny | ask | warn | off
    rules: Dict[str, str] = field(default_factory=dict)
    # Globs that are always allowed / always blocked for new files.
    allow: List[str] = field(default_factory=list)
    deny: List[str] = field(default_factory=list)
    # Git-ignored folder where throwaway work belongs.
    scratchDir: str = ".scratch"
    # Framework presets: "auto" (detect), "off", or a list of preset names.
    presets: object = "auto"
    # What to do with framework plan files at commit time: commit | ignore.
    plans: str = "commit"
    # Conventional Commits ("conventional") or no message checks ("off").
    commitStyle: str = "conventional"
    maxSubjectLength: int = 72
    # AI co-author trailers: ignore | forbid | require
    attribution: str = "ignore"
    # Ask after this many new files in one session (0 disables).
    newFileBudget: int = 12
    # Only apply the budget once the repo has this many tracked files.
    budgetMinTrackedFiles: int = 20
    # Ask when a single commit stages more files / changed lines than this.
    commitFileBudget: int = 30
    commitLineBudget: int = 1000
    # Warn at Stop when uncommitted changes exceed this many lines.
    diffBudget: int = 400
    # Similarity (0-1) at which a new file counts as a near-duplicate.
    nearDuplicateThreshold: float = 0.85
    # PR checks.
    prMaxFiles: int = 50
    prMaxBodyChars: int = 2500
    prMaxEmoji: int = 2
    # Command run at the end of each turn that changed files, e.g. "npm test".
    verify: str = ""
    verifyTimeout: int = 300
    verifyMaxRetries: int = 2
    # Optional external tools: name -> command template, or "off".
    integrations: Dict[str, str] = field(default_factory=dict)
    # Run integrations automatically at Stop (slow) instead of only via `check --deep`.
    integrationsAtStop: bool = False

    def mode(self, rule_id: str) -> str:
        if rule_id in self.rules and self.rules[rule_id] in ("deny", "ask", "warn", "off"):
            return self.rules[rule_id]
        rule = RULES.get(rule_id)
        base = rule.default if rule else "deny"
        if self.profile == "relaxed":
            if rule and rule.category in _FILE_CATEGORIES | {"commit", "pr"}:
                return {"deny": "ask", "ask": "warn", "warn": "off"}.get(base, base)
        elif self.profile == "strict":
            if rule and rule.category not in {"push"}:
                return {"ask": "deny", "warn": "ask"}.get(base, base)
        return base


def load_config(root: Path) -> Config:
    config = Config()
    path = root / CONFIG_FILE
    if not path.is_file():
        return config
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    if not isinstance(data, dict):
        return config
    defaults = {f.name: getattr(config, f.name) for f in fields(Config)}
    for key, value in data.items():
        if key not in defaults:
            continue
        expected = type(defaults[key])
        if key == "presets" and (isinstance(value, (str, list))):
            setattr(config, key, value)
        elif expected is float and isinstance(value, (int, float)):
            setattr(config, key, float(value))
        elif isinstance(value, expected) and not (expected is int and isinstance(value, bool)):
            setattr(config, key, value)
    if config.profile not in PROFILES:
        config.profile = "default"
    return config
