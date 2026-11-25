"""Spot a bare `python3` about to run GRIP code, and name the `grip-py` form.

Origin: 2026-09-14. `python3 ~/.claude/lib/grip_save.py` was typed by hand. It
did not fail. `python3` resolves to Homebrew's interpreter here, which carries no
`cryptography`, so the IDR seal silently downgraded to the DEMO seal — an HMAC
under a secret that on this installation is world-readable and committed. The
record was honestly labelled `custody: demo-shared-key`, printed one warning
above a wall of green ticks, and reported `12 passed, 0 failed`.

ADVISORY ONLY, AND DELIBERATELY UNPROMOTABLE TO DENY. A gate that could refuse a
`python3` invocation could refuse the very command needed to repair a broken
venv — the deadlock shape `rules/gate-resolution-escape-hatch.md` was written
after. The hook prints one line and always exits 0.

PURE. No I/O, no environment reads at call time, so it is testable without a
filesystem and cannot be the thing that breaks a Bash call.

WHAT IT MATCHES, and why the bar is where it is. Three conditions must all hold:
the interpreter is BARE (`python3`, not `.../venv/bin/python3`, which is already
right); a following argument is a `.py` path inside a GRIP tree; and that path is
not an explicit venv path. Anything narrower misses the real case; anything wider
fires on `python3 -c`, `python3 -m pytest` and `/tmp/scratch.py`, which are fine
and which would train the advisory to be ignored.

KNOWN AND ACCEPTED FALSE POSITIVE: a command that merely QUOTES the broken form
— `echo "python3 lib/grip_save.py"`, or a heredoc writing documentation — fires.
Suppressing that needs a shell parser, and the cost of being wrong here is one
stderr line. The module's own files are exempted by name so authoring this
feature does not trip it.

rules/never-system-python-for-grip.md · tests/test_system_python_detector.py
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: Files that may legitimately contain the broken form: this feature's own.
#: Line-scoped elsewhere; here it is the whole command, because a command that
#: writes one of these paths is authoring the feature, not running GRIP code.
SELF_EXEMPT = (
    "system_python_detector",
    "system-python-hook",
    "never-system-python-for-grip",
    "test_system_python_detector",
)

#: A bare interpreter: `python`, `python3`, `python3.14` — with NO path in front.
#: The negative lookbehind is the load-bearing half: `venv/bin/python3` and
#: `$VENV_PY` must not match, because those are already correct.
_BARE_PY = r"(?<![\w/.$${}-])(?:python3(?:\.\d+)?|python)(?![\w.-])"

#: A `.py` file inside a GRIP tree. Covers the spellings that actually appear:
#: `~/.claude/...`, `$HOME/.claude/...`, `$CLAUDE_DIR/...`, `${CLAUDE_DIR:-...}/...`,
#: `~/.grip/...`, and a bare relative `lib/foo.py` (which is a GRIP path whenever
#: the cwd is the checkout — the common shape in docs and in muscle memory).
_GRIP_PY_FILE = (
    r"[\"\']?"                      # a leading quote is common and must not block the match
    r"(?P<path>"
    r"(?:[~$][^\s|;&\"\']*?(?:\.claude|\.grip|CLAUDE_DIR|GRIP_DIR)[^\s|;&\"\']*?|"
    r"(?:\./)?lib)"
    r"/[^\s|;&\"\']*\.py)"
)

#: Interpreter options that may sit between `python3` and the script path.
#:
#: The first version rejected every option whose first character after `-` was
#: alphanumeric, which is EVERY real option -- so `python3 -u lib/grip_save.py`
#: and `python3 -B lib/grip_save.py` were both missed. Found by Codex on
#: GRIP#5640 and confirmed against `python3 --help`, whose usage line is
#: `[option] ... [-c cmd | -m mod | file | -] [arg] ...`.
#:
#: Two shapes, kept separate so neither can swallow the path: options that take
#: a following VALUE (`-X utf8`, `-W ignore`) are matched with their value, and
#: plain option clusters (`-u`, `-B`, `-OO`, `-Es`) are matched alone. A single
#: greedy "optional argument" would match the script path itself and leave
#: nothing for the path group.
_OPT_WITH_ARG = r"-(?:X|W|-check-hash-based-pycs)\s+\S+"
_OPT_PLAIN = r"--?[A-Za-z][\w.-]*"
_INTERPRETER_FLAGS = rf"(?:\s+(?:{_OPT_WITH_ARG}|{_OPT_PLAIN}))*"

#: `-c` and `-m` TERMINATE the option list: what follows is code or a module
#: name, never the script this detector is about. `python3 -m pytest lib/x.py`
#: runs pytest, not the module, and must stay quiet. Applied as a post-filter in
#: `scan` rather than as a lookahead, because expressing "this cluster contains
#: no c or m" inside the pattern is where the first version went wrong.
_TERMINATING_OPT = re.compile(r"(?:^|\s)--?[A-Za-z]*[cm](?![\w.-])")

_PATTERN = re.compile(
    _BARE_PY + r"(?P<flags>" + _INTERPRETER_FLAGS + r")\s+" + _GRIP_PY_FILE
)

#: An explicit venv interpreter anywhere in the token — already correct.
_VENV = re.compile(r"venv[/\\](?:bin|Scripts)[/\\]python", re.IGNORECASE)


class Finding(NamedTuple):
    """One bare-`python3`-runs-GRIP-code occurrence, and its `grip-py` form."""

    matched: str   #: the offending substring, verbatim
    path: str      #: the GRIP .py file it would have run
    suggestion: str  #: the same invocation, via the shim


def scan(command: str) -> list[Finding]:
    """Every bare-interpreter GRIP invocation in one shell command.

    Returns [] for anything correct, anything without a GRIP `.py` argument, and
    anything naming this feature's own files. Never raises: a detector that can
    throw would take down the Bash call it is only advising on.
    """
    try:
        if not command or not isinstance(command, str):
            return []
        if any(token in command for token in SELF_EXEMPT):
            return []
        out: list[Finding] = []
        for m in _PATTERN.finditer(command):
            matched = m.group(0)
            if _VENV.search(matched):
                continue
            if _TERMINATING_OPT.search(m.group("flags") or ""):
                continue  # -c / -m: the .py that follows is not what runs
            path = m.group("path")
            flags = (m.group("flags") or "").strip()
            suggestion = f"grip-py {flags + ' ' if flags else ''}{path}".replace("  ", " ")
            out.append(Finding(matched=matched.strip(), path=path,
                               suggestion=suggestion))
        return out
    except Exception:  # noqa: BLE001 — advisory only; never break the caller
        return []


def advisory(findings: list[Finding]) -> str:
    """One operator-facing line. Empty string when there is nothing to say."""
    if not findings:
        return ""
    first = findings[0]
    extra = f" (+{len(findings) - 1} more)" if len(findings) > 1 else ""
    return (
        f"[system-python] `{first.matched}` runs GRIP code under the system "
        f"interpreter, which has no `cryptography` — anything that signs a record "
        f"will silently write a forgeable demo seal instead of failing. "
        f"Use: {first.suggestion}{extra}"
    )
