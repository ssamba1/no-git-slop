"""What did the user actually ask for?

Only the UserPromptSubmit hook feeds this module, so overrides always come
from text the human typed, never from the agent's own output.
"""

from __future__ import annotations

import hashlib
import re

from .presets import glob_match

# File-like tokens: `docs/x.md`, report.md, ./notes/plan.txt, "my file.md"
_PATH_RE = re.compile(r"""(?:[`'"]([^`'"\n]{1,200}\.[A-Za-z0-9]{1,8})[`'"])|"""
                      r"""((?:\.{0,2}/)?\.?[\w@~+-][\w@~+./-]*\.[A-Za-z0-9]{1,8})\b""")
_DIR_RE = re.compile(r"(?:^|\s)[`'\"]?((?:[\w.-]+/)+)[`'\"]?(?=\s|$|[,.;:!?])")

PHRASES = {
    "doc": re.compile(
        r"\b(write|create|make|save|add|generate|draft|put|produce|keep)\b[^.!?\n]{0,60}?"
        r"\b(report|summary|summaries|notes|write-?up|document(ation)?|docs?|readme|"
        r"changelog|md file|markdown( file)?|memo|plan|guide)\b", re.I),
    "version": re.compile(
        r"\b(new version|v\d+\b|separate (file|copy|version|module)|duplicate (it|the file|this)|"
        r"(a |make a )copy (of|it)|alternative (version|implementation)|keep (the )?(old|original))",
        re.I),
    "backup": re.compile(r"\b(back ?up|\.bak\b|keep a copy|save a copy)", re.I),
    "script": re.compile(r"\b(script|scratch|prototype|quick test|one-?off|debug(ging)? file|"
                         r"playground|demo)\b", re.I),
}

# Which phrase category can soften which rule.
RULE_CATEGORY = {
    "report-doc": "doc", "root-doc": "doc",
    "version-copy": "version", "near-duplicate": "version",
    "backup-copy": "backup", "copy-folder": "backup",
    "throwaway-script": "script", "root-script": "script",
}

_ALLOW_TOKEN = re.compile(r"(?:^|\s)>ngs\s+allow\s+(\S+)", re.I)
_OFF_TOKEN = re.compile(r"(?:^|\s)>ngs\s+off\b", re.I)
_ON_TOKEN = re.compile(r"(?:^|\s)>ngs\s+on\b", re.I)
CODE_RE = re.compile(r"\bngs-[0-9a-f]{6}\b")


def norm(path: str) -> str:
    path = path.strip().strip("`'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def record_prompt(session, prompt: str) -> list:
    """Update the session's intent from a user prompt. Returns notices."""
    notices = []
    intent = session.intent
    for m in _PATH_RE.finditer(prompt):
        token = norm(m.group(1) or m.group(2) or "")
        if not token or token.startswith(("http:", "https:", "www.")) or "://" in token:
            continue
        if token not in intent["paths"]:
            intent["paths"].append(token)
        base = token.rsplit("/", 1)[-1]
        if base not in intent["basenames"]:
            intent["basenames"].append(base)
    for m in _DIR_RE.finditer(prompt):
        d = norm(m.group(1))
        if d and d + "/**" not in intent.setdefault("dirs", []):
            intent["dirs"].append(d + "/**")
    for name, rx in PHRASES.items():
        if rx.search(prompt) and name not in intent["phrases"]:
            intent["phrases"].append(name)
    for m in _ALLOW_TOKEN.finditer(prompt):
        glob = norm(m.group(1))
        if glob not in intent["allow_globs"]:
            intent["allow_globs"].append(glob)
        notices.append(f"no-git-slop: allowing `{glob}` for the rest of this session.")
    if _OFF_TOKEN.search(prompt):
        session.data["off"] = True
        notices.append("no-git-slop: file and commit checks are off for this session "
                       "(type `>ngs on` to resume). Self-protection stays on.")
    if _ON_TOKEN.search(prompt):
        session.data["off"] = False
        notices.append("no-git-slop: checks resumed.")
    for code in CODE_RE.findall(prompt):
        target = session.data["pending_codes"].pop(code, None)
        if target:
            if target not in intent["paths"]:
                intent["paths"].append(target)
            notices.append(f"no-git-slop: `{target}` approved once by the user.")
    return notices


def allow_code(session, rel: str) -> str:
    code = "ngs-" + hashlib.sha1(f"{session.key}:{rel}".encode()).hexdigest()[:6]
    session.data["pending_codes"][code] = rel
    return code


def user_named(session, rel: str, by_name: bool = True) -> bool:
    """Did the user name this file? by_name=False needs the path, not just a matching file name."""
    if session is None:
        return False
    intent = session.intent
    rel = norm(rel)
    base = rel.rsplit("/", 1)[-1]
    if rel in intent["paths"] or (by_name and base in intent["basenames"]):
        return True
    if any(rel.endswith("/" + p) for p in intent["paths"] if "/" in p):
        return True
    return any(glob_match(rel, g) for g in intent["allow_globs"])


def soften(session, rule_id: str, rel: str, decision: str) -> tuple:
    """Apply user intent to a decision. Returns (decision, note)."""
    if session is None or decision not in ("deny", "ask"):
        return decision, ""
    category = RULE_CATEGORY.get(rule_id)
    if category is None:
        return decision, ""
    if user_named(session, rel):
        if category == "backup":
            return "ask", "user named this file; backups still need confirmation"
        return "ok", "user asked for this file"
    # If the user named a specific file of this type, other files of the type stay blocked.
    ext = rel.rsplit(".", 1)[-1].lower() if "." in rel.rsplit("/", 1)[-1] else ""
    if ext and any(p.lower().endswith("." + ext) for p in session.intent["paths"]):
        return decision, ""
    in_named_dir = any(glob_match(norm(rel), g) for g in session.intent.get("dirs", []))
    if category in session.intent["phrases"] or (in_named_dir and category == "doc"):
        return "ask", "user's request mentions this kind of file"
    return decision, ""
