"""Ship-time private-term scan for the gift bundle.

The bundle's own scanners look for secrets and for the operator's identity. They
cannot know which words belong to a private matter, so a bundle built from a tree
that carries such text passes them; one did. This scan reads a list of private
terms from an untracked file and refuses any bundle that contains one. It names a
hit by path, line and term NUMBER, never by the term, because the term itself is
the private value.

Where the list comes from (first match wins):
  1. $GRIP_GIFT_PRIVATE_TERMS: a file path, or the word "none" to skip the scan
     on purpose; a skip is printed, never silent.
  2. config/ship-scan-terms.txt inside the operator's private (guardian)
     repository, found through the gitignored config/guardian.local.json
     ("guardian_repo") under the GRIP install root.
No list at all is itself a finding, so a bundle is never built unscanned by
accident. The list format: one term per line, case-insensitive, matched as a
literal; blank lines, lines starting with '#', and terms shorter than three
characters are ignored.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ENV = "GRIP_GIFT_PRIVATE_TERMS"
GUARDIAN_LIST = Path("config") / "ship-scan-terms.txt"
MIN_TERM = 3
HOW_TO = (f"set {ENV} to the list's path, or add {GUARDIAN_LIST} to the repository named by "
          f"config/guardian.local.json; {ENV}=none skips the scan on purpose")


@dataclass(frozen=True)
class TermList:
    terms: tuple
    source: str
    skipped: bool = False


@dataclass(frozen=True)
class Hit:
    rel_path: str
    line_no: int
    term_no: int


def _read_terms(path: Path) -> tuple:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return tuple(t for t in lines if len(t) >= MIN_TERM and not t.startswith("#"))


def _guardian_list(claude_dir: Path):
    """The guardian repository's term list, or None when it is not configured."""
    try:
        config = json.loads((claude_dir / "config" / "guardian.local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    repo = config.get("guardian_repo") if isinstance(config, dict) else None
    path = Path(repo).expanduser() / GUARDIAN_LIST if repo else None
    return path if path is not None and path.is_file() else None


def load_terms(claude_dir, env=None):
    """The term list to scan with, a skipped TermList, or None when none exists.

    The source text never names the file, since its path is itself private."""
    env = os.environ if env is None else env
    chosen = (env.get(ENV) or "").strip()
    if chosen.lower() == "none":
        return TermList((), f"private-term scan skipped on purpose ({ENV}=none)", skipped=True)
    path = Path(chosen).expanduser() if chosen else _guardian_list(Path(claude_dir))
    if path is None or not path.is_file():
        return None
    terms = _read_terms(path)
    return TermList(terms, f"private-term scan: {len(terms)} term(s) from an untracked list")


def _hits_in(path: Path, rel: str, pattern, numbers: dict) -> list:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    found = []
    for line_no, line in enumerate(text.split("\n"), 1):
        match = pattern.search(line)
        if match:
            found.append(Hit(rel, line_no, numbers.get(match.group(0).lower(), 0)))
    return found


def scan(root, terms) -> list:
    """Every line under `root` that contains a term, as path, line and term number."""
    if not terms:
        return []
    root = Path(root)
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    numbers = {t.lower(): n for n, t in enumerate(terms, 1)}
    hits = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        hits += _hits_in(path, path.relative_to(root).as_posix(), pattern, numbers)
    return hits
