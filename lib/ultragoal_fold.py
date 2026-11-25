#!/usr/bin/env python3
"""ultragoal_fold.py — the Banach fold core of the /ultragoal kernel.

Layer 1 of the ultragoal stack (fold <- probes <- composition/CLI): pure
text projections with the converge-kernel runtime witness. No subprocess,
no filesystem, no LLM, no network. Split out of lib/ultragoal.py per the
AGORA DEFER review on the /ultragoal PR (#3914): one module, one concern.

Public surface (re-exported by lib/ultragoal.py — import from there):
    FoldError, fold_brief, fold_hash, goal_hash
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


class FoldError(RuntimeError):
    """The fold failed to reach a fixed point — a non-contracting pass,
    or the converge kernel itself is unavailable (both fail LOUD)."""


def _fold_pass(text: str) -> str:
    """ONE normalisation pass — the contraction fold_brief() iterates.

    Composition of projections, each a no-op on already-folded input:
    CRLF->LF, strip trailing whitespace per line, collapse blank-line runs
    to one (leading blanks included), exactly one trailing newline.
    """
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln == "" and (not out or out[-1] == ""):
            continue  # collapse blank runs AND strip leading blanks
        out.append(ln)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def fold_brief(text: str) -> str:
    """Idempotent fold, executed ON the converge kernel (lib/converge.py).

    converge(state=text, criterion=already-a-fixed-point, improve=_fold_pass)
    is loop-engineering's blessed degenerate case — and `result.satisfied`
    is a RUNTIME Banach witness: a future non-contracting pass raises
    FoldError at the moment of the fold, not later at selftest.

    Import guard (AGORA DEFER item): a missing converge kernel raises
    FoldError with an actionable message — NEVER a silent single-pass
    fallback, which would remove the witness while looking identical.
    """
    try:
        from converge import converge  # lib sibling via _LIB sys.path insert
    except ImportError as exc:
        raise FoldError(
            "the converge kernel (lib/converge.py) is unavailable — the "
            "fold's runtime Banach witness cannot run. Repair the GRIP "
            "install (lib/converge.py missing from this checkout) rather "
            "than bypassing the witness."
        ) from exc

    result = converge(
        state=text,
        criterion=lambda s: _fold_pass(s) == s,
        improve=_fold_pass,
        max_depth=3,
        label="ultragoal-fold",
    )
    if not result.satisfied:
        raise FoldError(
            "fold did not reach a fixed point within depth 3 — was a "
            "non-contracting normalisation step introduced?"
        )
    return result.final_state


def fold_hash(text: str) -> str:
    """Short content hash of the FOLDED text — stable across noise."""
    return hashlib.sha256(fold_brief(text).encode("utf-8")).hexdigest()[:8]


def goal_hash(text: str) -> str:
    """PROGRESS-INVARIANT goal identity: ticking boxes never changes it;
    editing a deliverable's label or probe (the contract) does."""
    untucked = re.sub(r"(?m)^- \[[xX]\] ", "- [ ] ", fold_brief(text))
    return hashlib.sha256(untucked.encode("utf-8")).hexdigest()[:8]
