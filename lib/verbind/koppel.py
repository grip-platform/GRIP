"""Verbind Koppel round -- the AskUserQuestion payload for one round.

Koppel (Afrikaans: to couple, to connect) is the round in which the operator
says what a coined verb means. Every payload passes the universal gates in
``lib/hitl_quality_gates.py`` (header of at most 12 characters, 2 to 4 options,
a question ending in "?", no option named Other or Skip) and three of
Verbind's own, checked by ``check_payload``:

  * the floor's recommendation, when it has one, is the FIRST option and its
    label ends "(Recommended)";
  * a free-text path is always present: "Not this, let me explain", or "Let
    me explain" when no operation is offered;
  * a defer path is always present, "Defer, treat as text", so the operator
    can always decline -- and a deferred verb is never asked about again.

Only operations the authority floor would permit are offered, so choosing an
offered option can never end in a denial. A floor candidate that is not on the
read-only list is named in the question as context, never as an option.

The payload is plain JSON. A harness that cannot render AskUserQuestion can
still show the same question and options as numbered text.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from lib.hitl_quality_gates import collect_violations

from .gate import Candidate, Evidence, Meaning, describe_operation

__all__ = [
    "DEFER_LABEL",
    "EXPLAIN_LABEL",
    "EXPLAIN_LABEL_ALONE",
    "HEADER",
    "MAX_OFFERED",
    "RECOMMENDED_SUFFIX",
    "build_payload",
    "check_payload",
]

HEADER = "Koppel"
EXPLAIN_LABEL = "Not this, let me explain"
EXPLAIN_LABEL_ALONE = "Let me explain"
DEFER_LABEL = "Defer, treat as text"
RECOMMENDED_SUFFIX = " (Recommended)"
MAX_OFFERED = 2
_OPTION_WORD = re.compile(r"\b(?:Other|Skip)\b")


def _operation(c: Candidate) -> str:
    return describe_operation(Meaning(c.kind, c.slug))


def _summary(c: Candidate, verb: str) -> str:
    """The candidate's own description, unless it would read as a UI option."""
    text = " ".join((c.summary or "").split())
    if not text or _OPTION_WORD.search(text):
        return f"Bind /{verb} to {_operation(c)}."
    return text


def _option(c: Candidate, verb: str, recommended: bool) -> dict:
    label = _operation(c) + (RECOMMENDED_SUFFIX if recommended else "")
    return {"label": label, "description": _summary(c, verb), "kind": c.kind, "slug": c.slug}


def _question(verb: str, evidence: Evidence, offered: list) -> str:
    top = evidence.top
    if top is not None and top not in offered:
        return (f"The closest match, {_operation(top)}, is not on the v1 read-only list, "
                f"so it cannot be bound. What should /{verb} mean here?")
    if not offered:
        return f"Nothing on the v1 read-only list matched. What should /{verb} mean here?"
    return f"What should /{verb} mean here?"


def build_payload(verb: str, evidence: Evidence,
                  permitted: Callable[[str, str], bool]) -> dict:
    """The Koppel payload, checked; the minimal two-option payload on any fault."""
    offered = [c for c in evidence.candidates if permitted(c.kind, c.slug)][:MAX_OFFERED]
    rec = evidence.recommended
    recommended = rec if rec is not None and rec in offered else None
    if recommended is not None:
        offered = [recommended] + [c for c in offered if c != recommended]
    options = [_option(c, verb, c == recommended) for c in offered]
    explain = EXPLAIN_LABEL if offered else EXPLAIN_LABEL_ALONE
    options.append({"label": explain,
                    "description": f"Say in your own words what /{verb} should do; it can be "
                                   "bound only to an existing read-only operation."})
    options.append({"label": DEFER_LABEL,
                    "description": f"Leave /{verb} unbound and run this prompt as plain text; "
                                   "it will not be asked about again."})
    payload = _payload(verb, _question(verb, evidence, offered), options, recommended)
    return payload if not check_payload(payload) else _minimal(verb)


def _payload(verb: str, question: str, options: list, recommended: Optional[Candidate]) -> dict:
    ui_options = [{"label": o["label"], "description": o["description"]} for o in options]
    return {
        "questions": [{"header": HEADER, "question": question,
                       "multiSelect": False, "options": ui_options}],
        "verb": verb,
        "recommended": {"kind": recommended.kind, "slug": recommended.slug} if recommended else None,
        "offered": [{"label": o["label"], "kind": o["kind"], "slug": o["slug"]}
                    for o in options if "slug" in o],
    }


def _minimal(verb: str) -> dict:
    options = [
        {"label": EXPLAIN_LABEL_ALONE,
         "description": f"Say in your own words what /{verb} should do."},
        {"label": DEFER_LABEL,
         "description": f"Leave /{verb} unbound and run this prompt as plain text."},
    ]
    return _payload(verb, f"What should /{verb} mean here?", options, None)


def check_payload(payload: dict) -> list:
    """Every violation, universal and Verbind's own; an empty list is a pass."""
    try:
        questions = payload["questions"]
        labels = [o["label"] for o in questions[0]["options"]]
    except (KeyError, IndexError, TypeError):
        return ["payload is not shaped like an AskUserQuestion call"]
    faults = list(collect_violations(questions))
    if len(questions) != 1 or questions[0].get("header") != HEADER:
        faults.append("a Koppel round asks exactly one question headed 'Koppel'")
    if labels[-1:] != [DEFER_LABEL]:
        faults.append("the defer path must be present, as the last option")
    if not ({EXPLAIN_LABEL, EXPLAIN_LABEL_ALONE} & set(labels)):
        faults.append("a free-text path must be present")
    recommended = [i for i, label in enumerate(labels) if label.endswith(RECOMMENDED_SUFFIX)]
    if recommended not in ([], [0]) or bool(recommended) != bool(payload.get("recommended")):
        faults.append("the recommended default must be named first, and only when one exists")
    return faults
