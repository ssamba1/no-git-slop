"""Static analysis of Bash commands: what would they write, and what git
or gh calls do they make?

Nothing here executes anything. Commands too dynamic to resolve (variables,
command substitution, globs in targets) are skipped and left to the Stop
sweep, so parsing can only ever make the guard stricter, never weaker.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MAX_DEPTH = 5
_SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "|&", ";;"}
_OUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
_IN_REDIRECTS = {"<", "<<<", "<>"}
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{"}
_WRAPPERS = {"sudo", "nohup", "time", "command", "exec", "builtin", "nice", "ionice",
             "timeout", "stdbuf", "chronic", "unbuffer", "caffeinate", "doas"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
_DYNAMIC = re.compile(r"[$`*?\[\]{}]")


@dataclass
class Command:
    argv: List[str]
    cwd: Path
    env: Dict[str, str] = field(default_factory=dict)
    out_redirects: List[Tuple[str, Optional[str]]] = field(default_factory=list)  # (target, heredoc body)
    stdin_body: Optional[str] = None

    @property
    def name(self) -> str:
        base = os.path.basename(self.argv[0]) if self.argv else ""
        return base[:-4] if base.lower().endswith(".exe") else base


@dataclass
class WriteTarget:
    path: Path
    kind: str  # redirect, tee, cp, mv, ln, touch, mkdir, dd, download, script, inplace, delete, chmod
    content: Optional[str] = None
    source: Optional[Path] = None


# ------------------------------------------------------------------ parsing

def _quoted_offsets(text: str) -> List[bool]:
    """For each character, whether it sits inside single or double quotes."""
    inside, quote, escaped = [], "", False
    for ch in text:
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            if ch == quote:
                quote = ""
                inside.append(False)
                continue
        elif ch in ("'", '"'):
            quote = ch
        inside.append(bool(quote))
    return inside


def _strip_heredocs(command: str) -> Tuple[str, List[str]]:
    """Remove unquoted heredoc bodies (they'd be tokenized as commands); return them in order.

    Heredocs inside quotes, like git commit -m "$(cat <<'EOF' ... EOF)", stay intact.
    """
    quoted = _quoted_offsets(command)
    lines = command.split("\n")
    out, bodies = [], []
    i = 0
    offset = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        pending = [m for m in _HEREDOC_RE.finditer(line)
                   if not (offset + m.start() < len(quoted) and quoted[offset + m.start()])]
        offset += len(line) + 1
        i += 1
        for m in pending:
            dash, delim = m.group(1), m.group(3)
            body = []
            while i < len(lines):
                cand = lines[i].lstrip("\t") if dash else lines[i]
                offset += len(lines[i]) + 1
                if cand.strip() == delim or cand.rstrip() == delim:
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            bodies.append("\n".join(body))
    return "\n".join(out), bodies


def _strip_comments(text: str) -> str:
    """Drop unquoted `# ...` comments; shlex would choke on an apostrophe inside one."""
    quoted = _quoted_offsets(text)
    out, i = [], 0
    while i < len(text):
        if text[i] == "#" and not quoted[i] and (i == 0 or text[i - 1] in " \t\n;&|()"):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _tokens(command: str) -> Optional[List[str]]:
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
        lex.whitespace_split = True
        lex.whitespace = " \t\r"
        lex.commenters = ""
        return list(lex)
    except ValueError:
        return None


def _resolve(cwd: Path, raw: str) -> Optional[Path]:
    if not raw or _DYNAMIC.search(raw) or raw.startswith("/dev/"):
        return None
    if raw.startswith("/") and not Path(raw).is_absolute():
        # Windows: Bash paths are POSIX. Map Git Bash /c/... to C:/...; other rooted paths (/tmp) are outside any repo.
        m = re.match(r"/([a-zA-Z])(/.*)?$", raw)
        if not m:
            return None
        raw = m.group(1).upper() + ":" + (m.group(2) or "/")
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        p = cwd / p
    return Path(os.path.normpath(str(p)))


_PS_HERE = re.compile(r"""@(['"])[ \t]*\r?\n(.*?)\r?\n\1@""", re.S)
_PS_ENV = re.compile(r"\$env:(\w+)\s*=\s*", re.I)


def from_powershell(text: str) -> str:
    """Rewrite a PowerShell command line into the POSIX shape parse() reads.

    shortcut: covers blocks, here-strings, $env: assignments and backslash paths, not .NET calls
    like [IO.File]::WriteAllText; extend if agents start using those.
    """
    text = _PS_HERE.sub(lambda m: shlex.quote(m.group(2)), text)
    text = text.replace("`\r\n", " ").replace("`\n", " ")
    text = _PS_ENV.sub(r"export \1=", text)
    out, quote = [], None
    for ch in text:
        if ch == "\\":
            ch = "/"  # a path separator in PowerShell, never an escape
        elif quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "{}":
            ch = ";"
        out.append(ch)
    return "".join(out)


def parse(command: str, cwd: Path, depth: int = 0) -> List[Command]:
    """Split a command line into simple commands, unwrapping env/sudo/bash -c etc."""
    if depth > MAX_DEPTH or not command:
        return []
    text, bodies = _strip_heredocs(command)
    tokens = _tokens(_strip_comments(text))
    if tokens is None:
        return []
    commands: List[Command] = []
    seg: List[str] = []
    cur_cwd = cwd
    exported: Dict[str, str] = {}

    def flush():
        nonlocal seg, cur_cwd
        if seg:
            for cmd in _build(seg, cur_cwd, bodies, depth):
                if cmd.name == "export":
                    exported.update(a.split("=", 1) for a in cmd.argv[1:] if _ASSIGN_RE.match(a))  # type: ignore[misc]
                cmd.env = {**exported, **cmd.env}
                if cmd.name in ("cd", "pushd") and len(cmd.argv) >= 2:
                    target = _resolve(cur_cwd, cmd.argv[-1])
                    if target is not None:
                        cur_cwd = target
                    continue
                commands.append(cmd)
        seg = []

    for tok in tokens:
        if tok in _SEPARATORS or (tok and set(tok) <= set(";&|()\n")):
            flush()
        else:
            seg.append(tok)
    flush()
    return commands


def _build(seg: List[str], cwd: Path, bodies: List[str], depth: int) -> List[Command]:
    env: Dict[str, str] = {}
    argv: List[str] = []
    redirects: List[Tuple[str, Optional[str]]] = []
    stdin_body = None
    i = 0
    while i < len(seg):
        tok = seg[i]
        nxt = seg[i + 1] if i + 1 < len(seg) else None
        if tok in _OUT_REDIRECTS:
            if nxt is not None and not (tok == ">&" and nxt.isdigit()) and not nxt.startswith("&"):
                redirects.append((nxt, None))
            i += 2
            continue
        if tok == "<<" or tok == "<<-":
            stdin_body = bodies.pop(0) if bodies else ""
            i += 2
            continue
        if tok in _IN_REDIRECTS:
            if tok == "<<<":
                stdin_body = nxt
            i += 2
            continue
        if tok.isdigit() and nxt in _OUT_REDIRECTS | _IN_REDIRECTS:
            i += 1
            continue
        if not argv and _ASSIGN_RE.match(tok):
            k, v = tok.split("=", 1)
            env[k] = v
            i += 1
            continue
        argv.append(tok)
        i += 1
    if redirects and stdin_body is not None:
        redirects = [(t, stdin_body) for t, _ in redirects]
    elif redirects and argv and os.path.basename(argv[0]) in ("echo", "printf"):
        text = " ".join(a for a in argv[1:] if not a.startswith("-"))
        redirects = [(t, text) for t, _ in redirects]
    return _unwrap(Command(argv, cwd, env, redirects, stdin_body), depth)


def _unwrap(cmd: Command, depth: int) -> List[Command]:
    """Peel env/sudo/timeout/... and recurse into `bash -c "..."`."""
    argv = list(cmd.argv)
    cwd = cmd.cwd
    env = dict(cmd.env)
    guard = 0
    while argv and guard < 20:
        guard += 1
        name = os.path.basename(argv[0])
        if name in _KEYWORDS:
            argv = argv[1:]
            continue
        if name == "env":
            j = 1
            while j < len(argv):
                a = argv[j]
                if a in ("-i", "--ignore-environment", "-0", "--null", "-v"):
                    j += 1
                elif a in ("-u", "--unset"):
                    j += 2
                elif a in ("-C", "--chdir") and j + 1 < len(argv):
                    nd = _resolve(cwd, argv[j + 1])
                    cwd = nd if nd else cwd
                    j += 2
                elif a.startswith("--chdir="):
                    nd = _resolve(cwd, a.split("=", 1)[1])
                    cwd = nd if nd else cwd
                    j += 1
                elif a.startswith("-S") or a == "--split-string":
                    rest = a[2:] if len(a) > 2 else (argv[j + 1] if j + 1 < len(argv) else "")
                    return parse(rest, cwd, depth + 1)
                elif _ASSIGN_RE.match(a):
                    k, v = a.split("=", 1)
                    env[k] = v
                    j += 1
                else:
                    break
            argv = argv[j:]
            continue
        if name in _WRAPPERS:
            j = 1
            while j < len(argv) and argv[j].startswith("-"):
                # Options that take a value.
                if name == "timeout":
                    takes = ("-s", "-k")
                elif name in ("sudo", "nice", "ionice", "stdbuf", "doas"):
                    takes = ("-u", "-g", "-n", "-c", "-s", "-k", "-o", "-e", "-i", "-p")
                else:
                    takes = ()
                j += 2 if argv[j] in takes else 1
            if name == "timeout" and j < len(argv):
                j += 1  # duration
            argv = argv[j:]
            continue
        if name in _SHELLS:
            for j, a in enumerate(argv[1:], start=1):
                if a == "-c" or (a.startswith("-") and not a.startswith("--") and "c" in a[1:]):
                    if j + 1 < len(argv):
                        inner = parse(argv[j + 1], cwd, depth + 1)
                        for c in inner:
                            c.env = {**env, **c.env}
                        return inner
            return [Command(argv, cwd, env, cmd.out_redirects, cmd.stdin_body)]
        if name.lower() in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            for j, a in enumerate(argv[1:], start=1):
                if a.lower() in ("-c", "-command") and j + 1 < len(argv):
                    inner = parse(from_powershell(" ".join(argv[j + 1:])), cwd, depth + 1)
                    for c in inner:
                        c.env = {**env, **c.env}
                    return inner
        break
    if not argv and not cmd.out_redirects:
        return []
    return [Command(argv, cwd, env, cmd.out_redirects, cmd.stdin_body)]


# ------------------------------------------------------------ write targets

_PY_OPEN = re.compile(r"""open\(\s*(?:r|f|rb|b)?(['"])([^'"]+)\1\s*,\s*(['"])[^'"]*[wax]""")
_PY_PATHW = re.compile(r"""Path\(\s*(['"])([^'"]+)\1\s*\)\s*\.write_(?:text|bytes)""")
_JS_WRITE = re.compile(r"""(?:writeFileSync|writeFile|appendFileSync|createWriteStream)\(\s*(['"`])([^'"`]+)\1""")
_RB_WRITE = re.compile(r"""File\.(?:write|open)\(\s*(['"])([^'"]+)\1(?:\s*,\s*['"][wa])?""")
_INTERPRETERS = {"python", "python3", "python2", "py", "node", "deno", "bun", "ruby", "perl", "php"}


def _plain_args(args: List[str], takes_value=()) -> Tuple[List[str], Dict[str, str]]:
    plain, opts = [], {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            plain.extend(args[i + 1:])
            break
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            opts[k] = v
        elif a in takes_value and i + 1 < len(args):
            opts[a] = args[i + 1]
            i += 1
        elif a.startswith("-") and len(a) > 1:
            opts[a] = ""
        else:
            plain.append(a)
        i += 1
    return plain, opts


def _read_small(path: Path) -> Optional[str]:
    try:
        if path.is_file() and path.stat().st_size <= 300_000:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


_PS_CMDLETS = {**dict.fromkeys(("set-content", "sc", "add-content", "ac", "out-file", "tee-object"), "write"),
               **dict.fromkeys(("new-item", "ni"), "new"),
               **dict.fromkeys(("copy-item", "cpi", "copy"), "cp"),
               **dict.fromkeys(("move-item", "mi", "move"), "mv"),
               **dict.fromkeys(("remove-item", "ri", "del", "erase", "rd", "rmdir"), "delete")}
_PS_SWITCHES = {"-force", "-append", "-nonewline", "-noclobber", "-passthru", "-whatif", "-confirm",
                "-recurse", "-container"}


def _ps_write_targets(cmd: Command, kind: str) -> List[WriteTarget]:
    named: Dict[str, str] = {}
    pos: List[str] = []
    args = cmd.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-") and len(a) > 1:
            k = a.lower().rstrip(":")
            if k in _PS_SWITCHES or i + 1 >= len(args):
                named[k] = ""
                i += 1
            else:
                named[k] = args[i + 1]
                i += 2
        else:
            pos.append(a)
            i += 1
    paths = [named[k] for k in ("-path", "-literalpath", "-filepath") if named.get(k)] or pos[:1]
    if kind in ("cp", "mv"):
        dest = named.get("-destination") or (pos[1] if len(pos) > 1 else None)
        if not paths or not dest:
            return []
        return write_targets(Command([kind, paths[0], dest], cmd.cwd, cmd.env))
    targets = [p for p in (_resolve(cmd.cwd, a) for a in paths) if p]
    if kind == "delete":
        return [WriteTarget(p, "delete") for p in targets]
    if kind == "new" and named.get("-itemtype", "").lower() == "directory":
        return [WriteTarget(p, "mkdir") for p in targets]
    value = named.get("-value") or (pos[1] if kind == "write" and len(pos) > 1 else None)
    return [WriteTarget(p, "redirect", value) for p in targets]


def write_targets(cmd: Command) -> List[WriteTarget]:
    out: List[WriteTarget] = []
    for raw, body in cmd.out_redirects:
        p = _resolve(cmd.cwd, raw)
        if p is not None and str(p) not in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"):
            out.append(WriteTarget(p, "redirect", body))
    name, args = cmd.name, cmd.argv[1:]
    if not name:
        return out
    if name.lower() in _PS_CMDLETS:
        return out + _ps_write_targets(cmd, _PS_CMDLETS[name.lower()])
    if name == "tee":
        plain, _ = _plain_args(args)
        out += [WriteTarget(p, "tee", cmd.stdin_body) for p in map(lambda a: _resolve(cmd.cwd, a), plain) if p]
    elif name in ("cp", "mv", "install", "ln", "rsync"):
        plain, opts = _plain_args(args, takes_value=("-t", "--target-directory", "-m", "--mode",
                                                    "-o", "--owner", "-g", "--group", "-S", "--suffix"))
        target_dir = opts.get("-t") or opts.get("--target-directory")
        if name == "install" and ("-d" in opts or "--directory" in opts):
            out += [WriteTarget(p, "mkdir") for p in (_resolve(cmd.cwd, a) for a in plain) if p]
            return out
        if target_dir:
            dest, srcs = _resolve(cmd.cwd, target_dir), plain
            as_dir = True
        elif len(plain) >= 2:
            dest, srcs = _resolve(cmd.cwd, plain[-1]), plain[:-1]
            as_dir = plain[-1].endswith("/") or (dest is not None and dest.is_dir()) or len(srcs) > 1
        else:
            return out
        if dest is None:
            return out
        kind = {"cp": "cp", "install": "cp", "rsync": "cp", "mv": "mv", "ln": "ln"}[name]
        for s in srcs:
            src = _resolve(cmd.cwd, s)
            if src is None:
                continue
            target = dest / src.name if as_dir else dest
            content = _read_small(src) if kind == "cp" else None
            out.append(WriteTarget(target, kind, content, src))
    elif name == "touch":
        plain, _ = _plain_args(args, takes_value=("-d", "-t", "-r", "--reference", "--date"))
        out += [WriteTarget(p, "touch") for p in (_resolve(cmd.cwd, a) for a in plain) if p]
    elif name == "mkdir":
        plain, _ = _plain_args(args, takes_value=("-m", "--mode"))
        out += [WriteTarget(p, "mkdir") for p in (_resolve(cmd.cwd, a) for a in plain) if p]
    elif name == "dd":
        for a in args:
            if a.startswith("of="):
                p = _resolve(cmd.cwd, a[3:])
                if p:
                    out.append(WriteTarget(p, "dd"))
    elif name in ("curl", "wget"):
        for i, a in enumerate(args):
            val = None
            if a in ("-o", "--output", "-O" if name == "wget" else "\0", "--output-document") and i + 1 < len(args):
                val = args[i + 1]
            elif a.startswith(("--output=", "--output-document=")):
                val = a.split("=", 1)[1]
            p = _resolve(cmd.cwd, val) if val else None
            if p:
                out.append(WriteTarget(p, "download"))
    elif name in ("sed", "perl", "gsed") and any(a == "-i" or a.startswith(("-i", "--in-place"))
                                                 or (a.startswith("-") and "i" in a[1:] and not a.startswith("--"))
                                                 for a in args):
        plain, _ = _plain_args(args, takes_value=("-e", "-f", "--expression", "--file"))
        has_script_opt = any(a in ("-e", "--expression", "-f", "--file") for a in args)
        files = plain if has_script_opt else plain[1:]
        out += [WriteTarget(p, "inplace") for p in (_resolve(cmd.cwd, a) for a in files) if p]
    elif name in ("rm", "unlink", "shred", "truncate"):
        plain, _ = _plain_args(args, takes_value=("-s", "--size"))
        out += [WriteTarget(p, "delete") for p in (_resolve(cmd.cwd, a) for a in plain) if p]
    elif name == "chmod":
        plain, _ = _plain_args(args)
        if plain and re.match(r"^([0-7]{3,4}|[ugoa]*[+=-][rwxXst]*)(,|$)", plain[0]):
            plain = plain[1:]
        out += [WriteTarget(p, "chmod") for p in (_resolve(cmd.cwd, a) for a in plain) if p]
    if name in _INTERPRETERS or re.match(r"python\d", name):
        code = None
        for i, a in enumerate(args):
            if a in ("-c", "-e", "--eval", "-p", "-pe", "-ne") and i + 1 < len(args):
                code = args[i + 1]
                break
        if code is None and cmd.stdin_body and not [a for a in args if not a.startswith("-")]:
            code = cmd.stdin_body
        if code:
            for rx in (_PY_OPEN, _PY_PATHW, _JS_WRITE, _RB_WRITE):
                for m in rx.finditer(code):
                    p = _resolve(cmd.cwd, m.group(2))
                    if p:
                        out.append(WriteTarget(p, "script"))
    return out


# ------------------------------------------------------------------ git & gh

@dataclass
class GitCall:
    sub: str
    args: List[str]
    cwd: Path
    configs: Dict[str, str]
    env: Dict[str, str]


def git_call(cmd: Command) -> Optional[GitCall]:
    if cmd.name != "git":
        return None
    argv = cmd.argv[1:]
    cwd = cmd.cwd
    configs: Dict[str, str] = {}
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        a = argv[i]
        if a == "-C" and i + 1 < len(argv):
            nd = _resolve(cwd, argv[i + 1])
            cwd = nd if nd else cwd
            i += 2
        elif a == "-c" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            configs[k.lower()] = v
            i += 2
        elif a.startswith("--config-env="):
            configs[a.split("=", 1)[1].split("=", 1)[0].lower()] = "<env>"
            i += 1
        elif a in ("--git-dir", "--work-tree", "--namespace", "--exec-path") and "=" not in a:
            i += 2
        else:
            i += 1
    if i >= len(argv):
        return None
    return GitCall(argv[i], argv[i + 1:], cwd, configs, cmd.env)


@dataclass
class PrCall:
    action: str
    title: str = ""
    body: Optional[str] = None
    base: str = ""
    head: str = ""
    fill: bool = False


def gh_pr_call(cmd: Command) -> Optional[PrCall]:
    if cmd.name != "gh" or len(cmd.argv) < 3 or cmd.argv[1] != "pr":
        return None
    action = cmd.argv[2]
    if action not in ("create", "edit", "new"):
        return None
    pr = PrCall(action)
    args = cmd.argv[3:]
    i = 0
    while i < len(args):
        a = args[i]
        val = args[i + 1] if i + 1 < len(args) else ""
        if a in ("-t", "--title"):
            pr.title, i = val, i + 2
        elif a in ("-b", "--body"):
            pr.body, i = val, i + 2
        elif a in ("-F", "--body-file"):
            p = _resolve(cmd.cwd, val) if val != "-" else None
            pr.body = (cmd.stdin_body if val == "-" else (_read_small(p) if p else None)) or pr.body
            i += 2
        elif a in ("-B", "--base"):
            pr.base, i = val, i + 2
        elif a in ("-H", "--head"):
            pr.head, i = val, i + 2
        elif a.startswith("--title="):
            pr.title, i = a.split("=", 1)[1], i + 1
        elif a.startswith("--body="):
            pr.body, i = a.split("=", 1)[1], i + 1
        elif a in ("-f", "--fill", "--fill-first", "--fill-verbose"):
            pr.fill, i = True, i + 1
        else:
            i += 1
    return pr


# -------------------------------------------------------- commit messages

_SUBST_HEREDOC = re.compile(r"^\$\(\s*cat\s+<<-?\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\s*\1\s*\)\s*$", re.S)


def commit_message(args: List[str], cwd: Path) -> Optional[str]:
    """Message given to `git commit`, or None if it can't be known statically."""
    parts: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-m", "--message") and i + 1 < len(args):
            parts.append(args[i + 1])
            i += 2
            continue
        if a.startswith("--message="):
            parts.append(a.split("=", 1)[1])
        elif a.startswith("-") and not a.startswith("--") and "m" in a[1:] and "F" not in a[1:a.find("m")]:
            rest = a[a.index("m", 1) + 1:]
            if rest:
                parts.append(rest)
            elif i + 1 < len(args):
                parts.append(args[i + 1])
                i += 2
                continue
        elif a in ("-F", "--file") and i + 1 < len(args):
            p = _resolve(cwd, args[i + 1])
            text = _read_small(p) if p else None
            if text is None:
                return None
            parts.append(text)
            i += 2
            continue
        elif a.startswith("--file="):
            p = _resolve(cwd, a.split("=", 1)[1])
            text = _read_small(p) if p else None
            if text is None:
                return None
            parts.append(text)
        elif a in ("-C", "-c", "--reuse-message", "--reedit-message") or \
                a.startswith(("--fixup", "--squash", "--reuse-message=", "--reedit-message=")):
            return None
        i += 1
    if not parts:
        return None
    resolved = []
    for p in parts:
        m = _SUBST_HEREDOC.match(p.strip())
        if m:
            p = m.group(2)
        elif "$(" in p or "`" in p or re.search(r"\$\w", p):
            return None
        resolved.append(p)
    return "\n\n".join(resolved)


# git options that take a separate value, so the next argument is neither a flag nor a path.
_GIT_VALUE_OPTS = {"--author", "--date", "--message", "--file", "--template", "--cleanup", "--trailer",
                   "--fixup", "--squash", "--reuse-message", "--reedit-message", "--push-option",
                   "--repo", "--receive-pack", "--exec", "--chmod", "--pathspec-from-file"}


def split_args(args: List[str], short_values: str = "mFCct") -> Tuple[set, List[str]]:
    """Short flags (-am -> {a, m}) and positional args, skipping option values like `-m "- x"`."""
    flags, plain, skip = set(), [], False
    for k, a in enumerate(args):
        if skip:
            skip = False
        elif a == "--":
            plain += args[k + 1:]
            break
        elif a.startswith("--"):
            skip = "=" not in a and a in _GIT_VALUE_OPTS
        elif a.startswith("-") and len(a) > 1:
            for idx, ch in enumerate(a[1:]):
                flags.add(ch)
                if ch in short_values:
                    skip = idx == len(a) - 2  # the value is the next argument
                    break
        else:
            plain.append(a)
    return flags, plain


def short_flags(args: List[str], short_values: str = "mFCct") -> set:
    return split_args(args, short_values)[0]
