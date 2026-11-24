"""Compact list of existing function and class signatures.

Agents rewrite helpers they can't see. A short signature list (not full
source) more than doubled reuse in RepoReuse (arXiv 2609.35357), so this
prints one line per definition for Claude to scan before writing new code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, List

from . import gitutil

_PATTERNS = {
    (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"): [
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)\s*(\([^)]*\))"),
        re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(\([^)]*\)|\w+)\s*(?::[^=]+)?=>"),
        re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)()"),
        re.compile(r"^\s*(?:export\s+)?(?:interface|type)\s+(\w+)()"),
    ],
    (".go",): [re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*(\([^)]*\))"),
               re.compile(r"^type\s+(\w+)\s+(?:struct|interface)()")],
    (".rs",): [re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?(\([^)]*\))"),
               re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)()")],
    (".rb",): [re.compile(r"^\s*def\s+([\w.?!]+)\s*(\([^)]*\))?"),
               re.compile(r"^\s*(?:class|module)\s+([\w:]+)()")],
    (".java", ".kt", ".cs", ".swift", ".php"): [
        re.compile(r"^\s*(?:public|private|protected|internal|static|final|override|open|fun|func|"
                   r"function|\s)+\s*[\w<>\[\],? ]*?\b(\w+)\s*(\([^)]*\))\s*[{:]"),
        re.compile(r"^\s*(?:public\s+|abstract\s+|final\s+|data\s+)*(?:class|interface|struct|enum)\s+(\w+)()")],
}


def _python(path: Path, text: str) -> List[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    out = []

    def walk(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("__") and node.name != "__init__":
                    continue
                args = [a.arg for a in node.args.args]
                out.append(f"{prefix}{node.name}({', '.join(args)})  :{node.lineno}")
            elif isinstance(node, ast.ClassDef):
                out.append(f"class {prefix}{node.name}  :{node.lineno}")
                walk(node.body, prefix=f"{node.name}.")

    walk(tree.body)
    return out


def _regex(path: Path, text: str) -> List[str]:
    suffix = path.suffix.lower()
    for exts, patterns in _PATTERNS.items():
        if suffix in exts:
            out = []
            for n, line in enumerate(text.splitlines(), 1):
                for rx in patterns:
                    m = rx.match(line)
                    if m:
                        sig = (m.group(2) or "").strip()
                        out.append(f"{m.group(1)}{sig}  :{n}")
                        break
            return out
    return []


def build(root: Path, paths: Iterable[str] = (), limit: int = 400) -> List[str]:
    targets = [p.rstrip("/") for p in paths]
    lines: List[str] = []
    for rel in gitutil.tracked_files(root):
        if targets and not any(rel == t or rel.startswith(t + "/") for t in targets):
            continue
        path = root / rel
        try:
            if path.stat().st_size > 500_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sigs = _python(path, text) if rel.endswith(".py") else _regex(path, text)
        if sigs:
            lines.append(f"{rel}")
            lines += [f"  {s}" for s in sigs]
        if len(lines) >= limit:
            lines.append(f"... (truncated at {limit} lines; narrow the path)")
            break
    return lines
