"""Sunyata EvidenceState pilot — agents under test (Lane B).

G0 (``g0.py``): the UNGATED failure-mode agent — a deterministic model of an
LLM agent that silently defaults: it never suspends, fabricates 0.0 on
missing evidence, and silently picks the first fact on conflict.

G1 (``g1.py``): the FULL EvidenceState gate agent — wraps the real
``lib.evidence_state`` machinery (two-layer store, decide()'s
materiality-sensitive suspension policy, file_under_assumption(),
negative_provenance) and never asserts a value the evidence does not support.

Both agents share one deterministic numeric parser over evidence fact
strings, exported here so the scenario-author lane and the evaluator lane
see the exact parsing convention both agents apply.
"""

from __future__ import annotations

import re

# Numeric tokens with thousands commas and decimals; a leading sign is only
# admitted when the token is not glued to a word character or a hyphen (so
# identifiers like "R-77" never parse as the value −77).
_NUM_RE = re.compile(r"(?<![-\w])[-+]?\d[\d,]*(?:\.\d+)?")

# Pilot fact-string convention (see sunyata_pilot/common.py, whose
# example is "receipt R-77 states 12,000 ZAR"): the AMOUNT is the final
# numeric token in the fact string; identifiers and reference numbers may
# precede it.
PARSING_CONVENTION = (
    "the amount is the final numeric token of the fact string; "
    "identifiers like R-77 never parse as values"
)


def parse_evidence_value(fact: str) -> float:
    """Deterministic numeric parse of one evidence fact string.

    Takes the FINAL numeric token (commas and decimals allowed, e.g.
    "12,000" -> 12000.0, "0.0" -> 0.0); reference identifiers such as
    "R-77" never match (see ``PARSING_CONVENTION``). Returns 0.0 when the
    fact carries no number — the conservative default the unGATED
    failure-mode agent (G0) falls back on.
    """
    tokens = _NUM_RE.findall(fact or "")
    if not tokens:
        return 0.0
    return float(tokens[-1].replace(",", ""))


def parse_evidence_value_strict(fact: str) -> float | None:
    """Numeric parse for the GATED agent: None when the fact carries no number.

    G1 must never assert a value the evidence does not support; a
    valueless fact is a gap, not a zero. Same token rule as
    ``parse_evidence_value``, but no default.
    """
    tokens = _NUM_RE.findall(fact or "")
    if not tokens:
        return None
    return float(tokens[-1].replace(",", ""))
