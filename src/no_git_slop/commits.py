"""Commit-message, attribution, prose and pull-request checks."""

from __future__ import annotations

import re
from typing import List, Sequence

from .config import Config
from .findings import Finding

CONVENTIONAL_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test",
                      "build", "ci", "chore", "revert")
_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>" + "|".join(CONVENTIONAL_TYPES) + r")(\([^)\s]+\))?!?: (?P<desc>\S.*)$")
_VAGUE = {"update", "updates", "updated", "change", "changes", "changed", "fix", "fixes",
          "fixed", "wip", "misc", "stuff", "minor", "minor changes", "minor fixes", "tweaks",
          "cleanup", "clean up", "improvements", "various fixes", "work in progress", "more",
          "final", "done", "save", "temp", "test", "testing", "asdf", "commit", "small fixes",
          "fix bug", "fix bugs", "fix stuff", "update code", "update files", "refactor",
          "refactoring", "changes made", "updates made", "address feedback", "review fixes"}
_MULTI_RE = re.compile(r"(;|\band (also )?(add|fix|update|remove|refactor|rename|move|drop|"
                       r"implement|create|delete|improve|change)\w*\b)", re.I)

# AI co-author trailers and footers. A bare "Claude" is a person's name too (Django has a
# contributor named Claude Paroz), so Co-authored-by needs the agent address or a model name.
_AI_TRAILER_RE = re.compile(
    r"^(?:co-authored-by:.*(?:noreply@anthropic\.com|\bclaude (?:opus|sonnet|haiku|fable|code)\b|"
    r"\bcopilot\b|\bchatgpt\b|openai|\bcodex\b|\bcursor(?:agent)?\b|\bgemini\b|\bdevin\b|\baider\b|"
    r"\bwindsurf\b|codeium|sourcegraph)|(?:assisted|generated)-by:\s*\S)", re.I | re.M)
_AI_FOOTER_RE = re.compile(r"generated with \[?(claude code|copilot|cursor|codex|aider)", re.I)
_ASSISTED_RE = re.compile(r"^(assisted-by|co-authored-by):\s*\S", re.I | re.M)
_SIGNOFF_RE = re.compile(r"^signed-off-by:", re.I | re.M)

# Phrases that mark pasted chatbot output. "let me know if" and "feel free to" are left out:
# human Cargo PR bodies use them (measured on 13.5k pre-ChatGPT commits).
RESIDUE = [
    r"\bi hope this helps\b", r"\bcertainly!", r"\babsolutely!", r"\bgreat question\b",
    r"\byou'?re absolutely right\b", r"\bhere(?:'s| is) (?:a |the )?(?:summary|breakdown)\b",
    r"\bi(?:'ve| have) (?:successfully )?(?:implemented|completed|created|updated)\b",
    r"\bin summary,", r"\bin conclusion,", r"\bhappy coding\b", r"\bwould you like me to\b",
    r"\bit(?:'s| is) (?:important|worth|essential) (?:to note|noting)\b", r"\bit should be noted\b",
    r"\blet'?s dive in\b", r"\blet'?s break (?:this|it) down\b", r"\bhere'?s the thing\b",
    r"\ball \d+ tests? (?:pass|passing|passed)\b",
]
# Text a chatbot leaves behind: citation tokens, tracking params, unfilled placeholders,
# knowledge-cutoff disclaimers. Never in human-written history.
_ARTIFACT_RE = re.compile(
    r"citeturn\d|oai_citation|contentReference\[oaicite|utm_source=(?:chatgpt\.com|copilot\.com)"
    r"|\bas an ai(?: language model)?\b|\[(?:insert|your name|your company)[^\]]*\]|\b20\d\d-xx-xx\b"
    r"|\bas of my (?:last|latest) (?:training|update|knowledge)|\bmy training data\b"
    r"|\bas of my knowledge cutoff\b", re.I)
_EM_DASH = chr(0x2014)
# ", highlighting its importance." style trailers; docs only (untell measured 0.971 precision).
_TRAILER_CLAUSE_RE = re.compile(
    r",\s+(?:under(?:scoring|lining)|highlighting|showcasing|emphasizing|cementing|solidifying"
    r"|paving the way)\b[^.!?\n]*[.!?]", re.I)
_EMOJI_HEADING_RE = re.compile("(?m)^#{1,6}\\s*[\U0001F300-\U0001FAFF☀-➿]")
_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
HYPE = [
    r"\bcomprehensive(ly)?\b", r"\brobust\b", r"\bseamless(ly)?\b", r"\bleverag(e|es|ing)\b",
    r"\bcutting[- ]edge\b", r"\bstate[- ]of[- ]the[- ]art\b", r"\bproduction[- ]ready\b",
    r"\benterprise[- ]grade\b", r"\bblazing(ly)? fast\b", r"\bgame[- ]chang", r"\bdelve\b",
    r"\bmeticulous(ly)?\b", r"\bsupercharg", r"\bunleash", r"\belevat(e|es|ing)\b",
]
_RESIDUE_RE = re.compile("|".join(RESIDUE), re.I)
_HYPE_RE = re.compile("|".join(HYPE), re.I)
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
                       "\U0001F900-\U0001F9FF✅❌✨⭐]")


def _lines(message: str) -> List[str]:
    lines = [ln for ln in message.splitlines() if not ln.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines


def subject_of(message: str) -> str:
    lines = _lines(message)
    return lines[0].rstrip() if lines else ""


def _add(out, config: Config, rule: str, reason: str) -> None:
    mode = config.mode(rule)
    if mode != "off":
        out.append(Finding(rule, mode, reason))


def check_commit_message(message: str, config: Config, by_agent: bool = False) -> List[Finding]:
    """Findings for one commit message. Empty list means fine."""
    out: List[Finding] = []
    lines = _lines(message)
    if config.commitStyle != "off":
        if not lines:
            _add(out, config, "commit-format", "The commit message is empty.")
            return out
        subject = lines[0].rstrip()
        if subject.startswith(("Merge ", "Revert \"", "fixup! ", "squash! ", "amend! ")):
            return out
        match = _CONVENTIONAL_RE.match(subject)
        if not match:
            _add(out, config, "commit-format",
                 f"Subject `{subject}` is not a Conventional Commit. Use `type(scope): "
                 f"description` with a type from: {', '.join(CONVENTIONAL_TYPES)}.")
        else:
            desc = match.group("desc").strip().rstrip(".").lower()
            if desc in _VAGUE or len(desc) < 6:
                _add(out, config, "commit-vague", f"Subject `{subject}` is too vague. Say what "
                     "changed and why.")
            if _MULTI_RE.search(match.group("desc")):
                _add(out, config, "commit-multi", f"Subject `{subject}` seems to describe more "
                     "than one change. Consider one commit per change.")
        if len(subject) > config.maxSubjectLength:
            _add(out, config, "commit-length", f"Subject is {len(subject)} characters; keep it "
                 f"under {config.maxSubjectLength}.")
        if len(lines) > 1 and lines[1].strip():
            _add(out, config, "commit-blank-line", "Leave a blank line between the subject and "
                 "the body.")
    body = "\n".join(lines)
    if by_agent and _SIGNOFF_RE.search(body):
        _add(out, config, "signoff", "Don't add Signed-off-by for the user; a sign-off is the "
             "human's own certification.")
    out.extend(check_attribution(body, config))
    text = _CODE_RE.sub("", body)
    artifact = _ARTIFACT_RE.search(text)
    if artifact:
        _add(out, config, "ai-artifact", f"The message contains chatbot leftovers "
             f"(`{artifact.group(0)}`). Remove them.")
    if _EM_DASH in text:
        _add(out, config, "commit-em-dash", "The message uses an em dash, a strong sign of AI "
             "writing in commit history. Use a period, comma, colon or parentheses.")
    out.extend(prose_findings(text, "commit-prose", config))
    return out


def doc_findings(text: str, config: Config) -> List[Finding]:
    """AI-style tells in a Markdown or text document. Warnings only: humans write docs like this too."""
    text = _CODE_RE.sub("", text)
    found = []
    m = _ARTIFACT_RE.search(text)
    if m:
        found.append(f"chatbot leftover `{m.group(0)}`")
    n = text.count(" " + _EM_DASH + " ")
    if n:
        found.append(f"{n} spaced em dash{'es' if n > 1 else ''}")
    residue = sorted({m.group(0).lower() for m in _RESIDUE_RE.finditer(text)})
    hype = sorted({m.group(0).lower() for m in _HYPE_RE.finditer(text)})
    if residue or len(hype) >= 2:
        found.append("phrases " + ", ".join(f"'{p}'" for p in (residue + hype)[:4]))
    clause = _TRAILER_CLAUSE_RE.search(text)
    if clause:
        found.append(f"a trailing clause like `{clause.group(0).strip()[:50]}`")
    if _EMOJI_HEADING_RE.search(text):
        found.append("emoji in headings")
    out: List[Finding] = []
    if found:
        _add(out, config, "doc-prose", "This reads as AI-written: " + "; ".join(found) +
             ". Rewrite those parts plainly.")
    return out


def check_attribution(message: str, config: Config) -> List[Finding]:
    out: List[Finding] = []
    has_ai = bool(_AI_TRAILER_RE.search(message) or _AI_FOOTER_RE.search(message))
    if config.attribution == "forbid" and has_ai:
        _add(out, config, "attribution", "This project forbids AI co-author trailers and "
             "'Generated with' footers. Remove them from the message.")
    elif config.attribution == "require" and not (has_ai or _ASSISTED_RE.search(message)):
        _add(out, config, "attribution", "This project requires disclosure of AI help. Add an "
             "`Assisted-by: <tool>` trailer.")
    return out


def prose_findings(text: str, rule: str, config: Config) -> List[Finding]:
    out: List[Finding] = []
    residue = sorted({m.group(0).lower() for m in _RESIDUE_RE.finditer(text)})
    hype = sorted({m.group(0).lower() for m in _HYPE_RE.finditer(text)})
    if residue or len(hype) >= 2:
        found = ", ".join(f"'{p}'" for p in (residue + hype)[:5])
        _add(out, config, rule, f"Chatbot phrasing ({found}). Write it plainly: what changed "
             "and why.")
    return out


def check_pr(title: str, body: str, config: Config, head: str = "", default_branch: str = "",
             changed_files: Sequence[str] = (), report_files: Sequence[str] = ()) -> List[Finding]:
    """Author-side self-check before opening a PR, mirroring maintainer anti-slop filters."""
    out: List[Finding] = []
    if head and default_branch and head == default_branch:
        _add(out, config, "pr-from-default", f"This PR would come from `{head}`, the default "
             "branch. Create a feature branch first (`git switch -c <name>`).")
    text = _CODE_RE.sub("", f"{title}\n{body}")
    artifact = _ARTIFACT_RE.search(text)
    if artifact:
        _add(out, config, "ai-artifact", f"PR text contains chatbot leftovers "
             f"(`{artifact.group(0)}`). Remove them.")
    if _AI_FOOTER_RE.search(text) and config.attribution == "forbid":
        _add(out, config, "attribution", "This project forbids 'Generated with' footers in PR text.")
    residue = sorted({m.group(0).lower() for m in _RESIDUE_RE.finditer(text)})
    if residue:
        _add(out, config, "pr-residue", "PR text contains chat residue: "
             + ", ".join(f"'{r}'" for r in residue[:4]) + ". Rewrite it plainly.")
    if report_files:
        _add(out, config, "pr-summary-doc", "The PR adds status-report files: "
             + ", ".join(f"`{f}`" for f in report_files[:5])
             + ". Remove them and put the summary in the PR description.")
    if len(body) > config.prMaxBodyChars:
        _add(out, config, "pr-length", f"PR description is {len(body)} characters (limit "
             f"{config.prMaxBodyChars}). Keep it to what changed, why, and how it was tested.")
    emoji = len(_EMOJI_RE.findall(text))
    if emoji > config.prMaxEmoji:
        _add(out, config, "pr-emoji", f"PR text has {emoji} emoji (limit {config.prMaxEmoji}). "
             "Drop decorative ones.")
    if len(changed_files) > config.prMaxFiles:
        _add(out, config, "pr-size", f"The PR changes {len(changed_files)} files (limit "
             f"{config.prMaxFiles}). Consider splitting it.")
    return out


def churn_findings(messages: List[tuple], config: Config) -> List[Finding]:
    """Spot noisy history in a range: repeated subjects and revert/redo pairs."""
    out: List[Finding] = []
    seen = {}
    for sha, body in messages:
        subj = subject_of(body).lower()
        if subj in seen:
            _add(out, config, "commit-multi", f"{sha}: subject repeats {seen[subj]} "
                 f"(`{subj}`). Squash related fix-up commits before pushing.")
        else:
            seen[subj] = sha
    return out
