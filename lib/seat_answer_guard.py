"""Did a council seat actually ANSWER, or did it merely return?

A seat dispatched through `claude -p` can come back with exit 0, no error on
any channel, and no answer. Three shapes were observed on this machine on
2026-09-20, all of them exit 0:

  * completely empty output;
  * only the harness's own stdin warning ("no stdin data received in 3s");
  * a harness operational message where the answer should be -- verbatim,
    "Autocompact is thrashing: the context refilled to the limit within 3
    turns of the previous compact ... Try reading in smaller chunks, or use
    /clear to start fresh." That is the harness talking to its operator, not
    the model answering the question.

Every one of those is indistinguishable from a real answer to a caller that
checks the exit code, and a council that counts them is counting a seat that
said nothing. GRIP already had this bug once tonight in a different costume:
two canned stubs outvoted one live seat and produced "PROCEED 81%". A seat
that did not answer must not vote.

DELIBERATELY NARROW. This matches harness-emitted operational text and
emptiness -- never content. A one-line disagreement is a complete answer and
must pass, because a guard that judged quality would quietly become a filter
for replies the caller found unconvincing, which is a far worse failure than
the one it fixes.
"""

from __future__ import annotations

import re
from enum import Enum

# Harness operational chatter. Each pattern is anchored on wording the HARNESS
# emits, never on anything a model would write when answering.
_OPERATIONAL = (
    re.compile(r"\bAutocompact is thrashing\b", re.I),
    re.compile(r"\bno stdin data received in \d+s\b", re.I),
    re.compile(r"\bContext low \(.*?\) · Run /compact\b", re.I),
    re.compile(r"^\s*Execution error\s*$", re.I | re.M),
    re.compile(r"\bCredit balance is too low\b", re.I),
    re.compile(r"\bPlease run /login\b", re.I),
    re.compile(r"\b5-hour limit reached\b", re.I),
)

# Lines that are noise around a real answer rather than evidence of its
# absence: stripped before the emptiness check, never treated as a verdict.
_NOISE = (
    re.compile(r"^\s*SessionEnd hook\b.*$", re.M),
    re.compile(r"^\s*Warning:\s.*$", re.M),
    re.compile(r"^\s*\[exited with code \d+\]\s*$", re.M),
    re.compile(r"^\s*---\s*exit:\s*\d+\s*---\s*$", re.M),
    # The CALLER's own banner, e.g. "==== SEAT 2 - FABLE ====". It is 58
    # characters of scaffolding that the length floor happily read as an
    # answer, so a seat printing nothing looked like a seat that spoke.
    # Anchored on a line that BOTH opens and closes with a run of separator
    # characters -- a shape a model answering a question does not produce.
    re.compile(r"^[ \t]*[=\u2550\u2500\-]{3,}.*?[=\u2550\u2500\-]{3,}[ \t]*$", re.M),
    re.compile(r"^[ \t]*[=\u2550\u2500\-]{3,}[ \t]*$", re.M),
)

# Below this, a "reply" is a fragment rather than an answer. Low on purpose:
# "No. The cap catches a broken sensor." is a complete answer at 44 chars.
MIN_ANSWER_CHARS = 24


class Seat(Enum):
    ANSWERED = "answered"
    EMPTY = "empty"
    OPERATIONAL = "operational"
    FAILED = "failed"

    @property
    def counts_as_a_vote(self) -> bool:
        return self is Seat.ANSWERED


def strip_noise(raw: str) -> str:
    """Remove harness scaffolding that surrounds an answer without being one."""
    out = raw or ""
    for pat in _NOISE:
        out = pat.sub("", out)
    return out.strip()


def classify(raw: str, returncode: int = 0, min_chars: int | None = None) -> Seat:
    """Decide whether this seat's output may be counted as a vote.

    The exit code is checked LAST, not first, because the whole point is that
    it was 0 in every observed failure. It is a tiebreaker, never the test.

    `min_chars` exists because the right floor depends on what happens next.
    Standalone, a 20-character fragment is not a usable answer. But a caller
    with its own parser and its own fail-open path -- ultrajudge degrades a
    malformed reply to a labelled stub -- must NOT have short content taken
    from it: "this is not JSON, oops" is 22 characters of a model genuinely
    replying, and reporting that as silence would be a worse diagnosis than
    the one this guard exists to fix. Such callers pass `min_chars=1`, keeping
    only the two judgements a parser cannot make: did anything arrive, and was
    what arrived the harness talking rather than the model.
    """
    body = strip_noise(raw)

    for pat in _OPERATIONAL:
        if pat.search(raw or ""):
            return Seat.OPERATIONAL

    floor = MIN_ANSWER_CHARS if min_chars is None else min_chars
    if len(body) < floor:
        return Seat.EMPTY

    if returncode != 0:
        return Seat.FAILED

    return Seat.ANSWERED


def explain(seat: Seat, name: str) -> str:
    """One sentence for the transcript, naming what was NOT counted and why."""
    if seat is Seat.ANSWERED:
        return ""
    reason = {
        Seat.EMPTY: "returned no answer",
        Seat.OPERATIONAL: "returned a harness operational message, not an answer",
        Seat.FAILED: "exited non-zero",
    }[seat]
    return (
        f"SEAT NOT COUNTED — {name} {reason}. A seat that did not answer is not "
        "a seat that agreed, so it is excluded from the vote rather than "
        "counted as assent."
    )
