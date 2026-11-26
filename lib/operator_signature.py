"""Who signs a commit: the operator's signature, found the way the commit standards say.

``rules/commit-standards.md`` names the order: the session's active identity
(``state/session/active-identity``), then the signature in ``facts/identity.md``,
then the name from git config when no signature is set (a clean clone, CI, or an
install not yet activated).

``strict=True`` stops after the two signature rungs. A caller whose commit must
never carry a person's real name -- a public repository, a pseudonymous bootstrap
commit -- uses it and refuses to commit when nothing answers, instead of falling
back to the git config name.

A signature rung accepts only a signature in GRIP's documented form, one capital
letter and ``>>`` (V>>, A>>). Anything else counts as unset, so strict mode cannot
pass a typed name (even one ending in ``>>``), and a second line cannot reach a
commit as an extra trailer.

Usage::

    from operator_signature import resolve_signature
    resolve_signature()             # "V>>", or the git config name, or ""
    resolve_signature(strict=True)  # "V>>", or "" when no signature is set
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

#: The value facts/identity.md.template ships with. An unedited copy names no one.
TEMPLATE_PLACEHOLDER = "Y>>"

# `signature: "V>>"`, `signature: V>>`, or the template's `signature: "Y>>"  # comment`.
_SIGNATURE_LINE = re.compile(r'^\s*signature:\s*"?([^"#]*?)"?\s*(?:#.*)?$', re.IGNORECASE)
_SIGNATURE_SHAPE = re.compile(r"[A-Z]>>")


def _clean(value: str) -> str:
    """``value`` when it is a signature, else "" (the template placeholder, a name, a second line)."""
    value = value.strip()
    if value == TEMPLATE_PLACEHOLDER or not _SIGNATURE_SHAPE.fullmatch(value):
        return ""
    return value


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):  # ValueError covers a file that is not UTF-8
        return ""


def from_session(claude_dir: Path) -> str:
    """The signature the current session declared, or ""."""
    return _clean(_read(claude_dir / "state" / "session" / "active-identity"))


def from_facts(claude_dir: Path) -> str:
    """The first ``signature:`` field of facts/identity.md, or "" (missing, empty, placeholder, not a signature).

    The first match decides even when it is unset: the template also carries an
    "Example Identity" block with ``signature: "J>>"``, and reading on past an
    unedited placeholder would sign commits with that example.
    """
    for line in _read(claude_dir / "facts" / "identity.md").splitlines():
        match = _SIGNATURE_LINE.match(line)
        if match:
            return _clean(match.group(1))
    return ""


def from_git(cwd: Path | None = None) -> str:
    """``git config user.name`` as seen from ``cwd``, or "" when git cannot answer."""
    try:
        out = subprocess.run(["git", "config", "user.name"], cwd=cwd, capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def resolve_signature(claude_dir: Path | None = None, *, strict: bool = False,
                      cwd: Path | None = None) -> str:
    """The operator's signature, or "" when no rung answers. Never raises."""
    root = Path(claude_dir) if claude_dir else get_claude_dir()
    for rung in (from_session, from_facts):
        value = rung(root)
        if value:
            return value
    return "" if strict else from_git(cwd)
