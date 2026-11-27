"""Rule catalog and the Finding type every check returns.

Each rule has an id, a default decision, a category used for intent
matching, and a short explanation shown by `no-git-slop explain`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Decision strength, strongest first.
ORDER = {"deny": 3, "ask": 2, "warn": 1, "ok": 0, "off": -1}


@dataclass(frozen=True)
class Rule:
    id: str
    default: str
    category: str  # file, doc, version, backup, script, guard, git, commit, push, pr, verify
    title: str
    why: str
    fix: str


_RULES = [
    # --- new files -------------------------------------------------------------
    Rule("junk-file", "deny", "file", "OS or tool junk file",
         "Files like .DS_Store, *.log and *.pyc are machine output, not source.",
         "Don't create it. If a tool produces it, add it to .gitignore."),
    Rule("backup-copy", "deny", "backup", "Backup or copy of a file",
         "Git already keeps history; *_backup, *.bak, 'copy of' files just rot.",
         "Edit the original file in place. Recover old versions with git."),
    Rule("copy-folder", "deny", "backup", "File inside a backup or fix-attempt folder",
         "Folders like NuclearFix/ or DataExplorerBackup/ hold duplicate code that "
         "breaks builds (anthropics/claude-code#1342).",
         "Edit the original file in place."),
    Rule("version-copy", "deny", "version", "New version of an existing file",
         "parser_v2.py next to parser.py means two copies to maintain.",
         "Edit the existing file. If the old behavior must stay, use a flag or a branch."),
    Rule("near-duplicate", "ask", "version", "New file nearly identical to an existing one",
         "A renamed copy slips past name rules but duplicates the code all the same.",
         "Edit or reuse the existing file instead of copying it."),
    Rule("report-doc", "deny", "doc", "Status-report document",
         "Summary, notes and report files written for one chat go stale immediately.",
         "Put the summary in your reply. Real docs go in README.md or docs/."),
    Rule("root-doc", "ask", "doc", "New top-level document",
         "Top-level docs are few by convention (README, CHANGELOG, CONTRIBUTING...).",
         "Put it in docs/ or extend README.md."),
    Rule("throwaway-script", "deny", "script", "Throwaway script",
         "debug_*, tmp_*, scratch_* scripts get committed by accident.",
         "Write it under the scratch folder (git-ignored), or make it a real test."),
    Rule("root-script", "deny", "script", "Loose script at the repo root",
         "Root-level test_*.py or check_*.sh files bypass the test layout.",
         "Put tests in the tests folder and one-off checks in the scratch folder."),
    Rule("new-top-dir", "warn", "file", "New top-level folder",
         "Agents invent folders like analysis/ or helpers/ that split the layout in two.",
         "Use an existing folder, or confirm the new one with the user."),
    Rule("misplaced-test", "warn", "file", "Test outside the tests folder",
         "A test_*.py next to the code it tests is easy to miss when the repo keeps tests in tests/.",
         "Move it into the tests folder."),
    Rule("agent-local", "deny", "file", "Agent history or per-user agent file",
         "Chat transcripts, agent caches and CLAUDE.local.md are personal context; committed, "
         "they load into every teammate's sessions.",
         "Keep them out of git with .gitignore or .git/info/exclude."),
    Rule("deny-pattern", "deny", "file", "Matches a project deny pattern",
         "The project's .no-git-slop.json forbids this path.",
         "Pick another location, or ask the user to change the config."),
    Rule("file-budget", "ask", "file", "Too many new files this session",
         "Sprawl usually shows up as many new files for one small request.",
         "Fold the content into existing files, or confirm with the user."),
    # --- self-protection ---------------------------------------------------------
    Rule("guard-config", "ask", "guard", "Edit to a guardrail config",
         "An agent that hits a block should not rewrite the rules that blocked it.",
         "Ask the user before changing hooks, CI or no-git-slop settings."),
    Rule("guard-state", "deny", "guard", "Write to no-git-slop's own state",
         "Session state records what the user approved; only the hooks may write it.",
         "Leave .git/no-git-slop/ alone."),
    Rule("hooks-bypass", "deny", "guard", "Disabling git hooks",
         "core.hooksPath, HUSKY=0, SKIP=... and deleting hooks turn the checks off.",
         "Fix what the hooks report instead of disabling them."),
    Rule("no-verify", "deny", "guard", "Skipping hooks with --no-verify",
         "--no-verify silently skips every pre-commit and commit-msg check.",
         "Run the command without --no-verify and fix what the hooks report."),
    # --- staging -------------------------------------------------------------------
    Rule("bulk-add", "deny", "git", "Bulk staging",
         "git add -A / . / :/ / * sweeps stray files into the commit "
         "(and fails under agent sandboxes anyway).",
         "Stage the files you changed by path."),
    Rule("pathspec-file", "ask", "git", "Staging from a pathspec file",
         "--pathspec-from-file can stage anything; the list isn't visible here.",
         "Stage files by path so the user can see them."),
    Rule("commit-all", "deny", "git", "git commit -a",
         "-a commits every modified tracked file, including unrelated edits.",
         "Stage the intended files by path, then commit."),
    Rule("staged-clutter", "deny", "git", "Clutter about to be staged or committed",
         "Files that would be blocked on creation shouldn't enter history either.",
         "Unstage them (git restore --staged <file>) and delete or move them."),
    Rule("plan-staged", "deny", "git", "Framework plan file staged",
         "This project keeps planning files out of history (plans: ignore).",
         "Unstage it; plans stay local. Change 'plans' in .no-git-slop.json to commit them."),
    # --- commits ---------------------------------------------------------------------
    Rule("commit-format", "deny", "commit", "Commit message is not a Conventional Commit",
         "Conventional Commits make history scannable and drive changelogs.",
         "Use `type(scope): description`, e.g. fix(api): handle empty token."),
    Rule("commit-vague", "deny", "commit", "Vague commit message",
         "'update', 'fix', 'changes' say nothing about what changed.",
         "Say what changed and why."),
    Rule("commit-length", "deny", "commit", "Commit subject too long",
         "Long subjects get truncated in every git UI.",
         "Keep the subject short; put detail in the body."),
    Rule("commit-blank-line", "deny", "commit", "No blank line after the subject",
         "Git treats everything before the first blank line as the subject.",
         "Leave one blank line between the subject and the body."),
    Rule("commit-multi", "warn", "commit", "Commit seems to do several things",
         "Subjects joined with 'and' or ';' usually describe two commits.",
         "Consider splitting it into one commit per logical change."),
    Rule("commit-size", "ask", "commit", "Very large commit",
         "Large commits are reviewed badly and merged less often.",
         "Split it into smaller commits, one logical change each."),
    Rule("signoff", "ask", "commit", "Agent adding Signed-off-by",
         "A sign-off certifies the human's Developer Certificate of Origin; "
         "projects like the Linux kernel forbid agents adding it.",
         "Let the human add their own sign-off."),
    Rule("attribution", "deny", "commit", "AI attribution policy",
         "This project has an explicit policy on AI co-author trailers.",
         "Follow the attribution setting in .no-git-slop.json."),
    Rule("commit-prose", "warn", "commit", "Chatbot phrasing in commit message",
         "Phrases like 'This commit comprehensively...' read as pasted chat output.",
         "Write it plainly: what changed and why."),
    Rule("ai-artifact", "deny", "commit", "Chatbot leftovers in a commit or PR",
         "Citation tokens (citeturn), utm_source=chatgpt.com, [Insert ...] placeholders and "
         "knowledge-cutoff disclaimers only come from pasted chat output.",
         "Remove them."),
    Rule("commit-em-dash", "deny", "commit", "Em dash in a commit message",
         "Em dashes appeared in 0.01% to 0.06% of pre-ChatGPT commits in django, curl and cargo, "
         "but in over a third of one agent-written repo's commits.",
         "Use a period, comma, colon or parentheses."),
    Rule("doc-prose", "warn", "doc", "AI-style writing in a document",
         "Spaced em dashes, chat phrases, hype words, trailing ', highlighting...' clauses and "
         "emoji headings read as AI-written; humans use some of them too, so this only warns.",
         "Rewrite the flagged parts plainly."),
    # --- push and PRs ---------------------------------------------------------------
    Rule("push-force", "ask", "push", "Force push",
         "Force pushes rewrite shared history.",
         "Confirm with the user first."),
    Rule("push-default-branch", "ask", "push", "Pushing straight to the default branch",
         "Changes to main normally go through a pull request.",
         "Push a feature branch and open a PR, or confirm with the user."),
    Rule("push-unclean", "deny", "push", "Unpushed commits break the rules",
         "Once pushed, bad messages and clutter are hard to remove.",
         "Fix the listed commits (amend, or rebase and reword) before pushing."),
    Rule("pr-from-default", "deny", "pr", "Pull request from the default branch",
         "Maintainers' anti-slop filters reject PRs opened from main/master.",
         "Create a feature branch first."),
    Rule("pr-residue", "deny", "pr", "Chat residue in PR text",
         "'I hope this helps' and similar phrases mark pasted chatbot output.",
         "Rewrite the PR text plainly."),
    Rule("pr-summary-doc", "deny", "pr", "PR adds a status-report document",
         "Summary files in a PR are the most common slop signal maintainers flag.",
         "Remove the file and put the summary in the PR description."),
    Rule("pr-length", "warn", "pr", "Long PR description",
         "Walls of text hide what reviewers need: what, why, how to test.",
         "Keep it short: what changed, why, how it was tested."),
    Rule("pr-emoji", "warn", "pr", "Emoji-heavy PR text",
         "Decorative emoji are a common AI-PR signal for maintainers.",
         "Drop decorative emoji."),
    Rule("pr-size", "ask", "pr", "Very large pull request",
         "Big agent PRs are merged far less often.",
         "Split it into smaller PRs."),
    # --- end of turn ------------------------------------------------------------------
    Rule("verify-failed", "deny", "verify", "Project checks failed",
         "The configured verify command is the definition of done.",
         "Fix the failures; don't weaken the tests."),
    Rule("diff-budget", "warn", "verify", "Large uncommitted diff",
         "Agent changes run about twice the size developers' changes do.",
         "Remove changes the task doesn't need, or split the work."),
]

RULES: Dict[str, Rule] = {r.id: r for r in _RULES}


@dataclass
class Finding:
    rule: str
    decision: str
    reason: str
    path: str = ""
    actions: List[str] = field(default_factory=list)
    note: str = ""  # e.g. why a decision was downgraded

    @property
    def strength(self) -> int:
        return ORDER.get(self.decision, 0)

    def to_dict(self) -> dict:
        rule = RULES.get(self.rule)
        return {
            "rule": self.rule,
            "decision": self.decision,
            "path": self.path,
            "reason": self.reason,
            "fix": rule.fix if rule else "",
            "actions": self.actions,
            "note": self.note,
        }

    def line(self) -> str:
        return f"[{self.rule}] {self.reason}"


def worst(findings: List[Finding]) -> Optional[Finding]:
    live = [f for f in findings if f.strength > 0]
    return max(live, key=lambda f: f.strength) if live else None


def at_least(findings: List[Finding], decision: str) -> List[Finding]:
    floor = ORDER[decision]
    return [f for f in findings if f.strength >= floor]
