"""Parse a KERN kernel file: the operative clauses of one rule.

A kernel is a rule file (rules/<rule>.md) whose body opens with a header line:

    K <rule> <version> [@<scope>] [P!]   // optional commentary

and continues with one clause per line. Optional YAML frontmatter, such as a
``paths:`` scope, is kept above the header and is not a clause. Every non-blank
body line, the header included, is a CLAUSE: it loads into the model, so each
one must be traceable to the annex that explains it (lib/lexicon/ledger.py).

The grammar inside a clause is deliberately not parsed here. Which operators
and tags KERN uses is a notation decision that may still change; the tooling
depends only on the header and on the one-clause-per-line shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The PARAMOUNT flag is accepted in both spellings while the notation is open:
# v0 wrote "P!", which measures 2 tokens on o200k and cl100k; "Paramount"
# measures 1. The notation ruling (HITL lock 1) narrows this to one spelling.
HEADER_RE = re.compile(
    r"^K (?P<rule>[a-z0-9][a-z0-9._-]*) (?P<version>\S+)"
    r"(?: @(?P<scope>[A-Za-z][\w-]*))?(?P<paramount> (?:P!|Paramount))?\s*(?://.*)?$")
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


class KernelError(ValueError):
    """The file claims to be a kernel but its header does not parse."""


@dataclass(frozen=True)
class Kernel:
    rule: str
    version: str
    scope: str | None
    paramount: bool
    clauses: tuple[str, ...]  # every non-blank body line, header first, verbatim


def body(text: str) -> str:
    """The text after any YAML frontmatter."""
    match = _FRONTMATTER_RE.match(text)
    return text[match.end():] if match else text


def clause_lines(text: str) -> list[str]:
    return [line for line in body(text).split("\n") if line.strip()]


def is_kernel(text: str) -> bool:
    """True when the first body line opens with the kernel keyword ``K ``."""
    lines = clause_lines(text)
    return bool(lines) and lines[0].startswith("K ")


def parse(text: str) -> Kernel:
    lines = clause_lines(text)
    if not lines:
        raise KernelError("empty kernel")
    match = HEADER_RE.match(lines[0])
    if not match:
        raise KernelError(f"header does not parse: {lines[0]!r}; expected "
                          "'K <rule> <version> [@<scope>] [P!]'")
    return Kernel(rule=match.group("rule"), version=match.group("version"),
                  scope=match.group("scope"), paramount=bool(match.group("paramount")),
                  clauses=tuple(lines))
