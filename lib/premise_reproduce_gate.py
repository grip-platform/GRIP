"""Premise-reproduce detector: demand a controlled repro before chasing a failure premise.

Origin (2026-07-14, hypothesis H798 — the RTK incident): an ``rg -rn`` typo
(``-r`` is ``--replace``, not "recursive") silently rewrote every match in the
command's own output, the mangled reads were blamed on an innocent external
tool (RTK), and a two-session investigation — including an external review
council — CONFIRMED the false story because every reviewer was asked about the
conclusion ("is the tool bad?") instead of the premise ("do reads actually
come back mangled?"). A 30-second controlled test would have killed the
premise at minute one.

This module is the pure, zero-side-effect detector behind
``hooks/premise-reproduce-gate.py`` (and the slug source for
``hooks/phantom-chase-breaker.py``). Given the prompt of an expensive dispatch
(investigation agent, council, deep-research fan-out) it answers two
questions:

1. Does the prompt ASSERT a failure premise — "X is broken", "Y is missing",
   "Z corrupted my data" — about a named tool / file / system?
2. Does the prompt CARRY reproduce evidence — a fenced command + observed
   output block, a "reproduced:" label, a controlled / ground-truth test
   mention, an exit code, or a file:line citation?

A failure premise with no reproduce evidence earns verdict ``"warn"``
(advisory only in v1 — the hook never denies). Everything else is ``"ok"``.

``classify_dispatch_prompt`` returns::

    {
        "premise_detected": bool,
        "evidence_present": bool,
        "verdict": "ok" | "warn",
        "reason": str,                # plain-language explanation
        "premises": [                 # every distinct failure claim found
            {"pattern": str, "subject": str, "claim": str}, ...
        ],
        "evidence": [str, ...],       # names of the evidence markers found
    }

No I/O, no state, no environment reads — fully deterministic and mockable.
"""

from __future__ import annotations

import itertools
import re

__all__ = ["classify_dispatch_prompt"]

# A named subject directly before the failure phrase: a tool name (RTK), a
# file path (lib/hal/ladder.py), or a system (hal-server:4010).
_SUBJECT = r"(?P<subject>[A-Za-z0-9_][\w./:~$-]*)"

# Subjects that negate or hollow out the claim ("nothing is broken" is the
# OPPOSITE of a failure premise; "what is broken?" is a question).
_NON_PREMISE_SUBJECTS = frozenset(
    {"nothing", "none", "neither", "nobody", "what", "which", "who"}
)

# Word-boundaried failure-premise assertions about a named subject.
_FAILURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("is-broken", re.compile(
        _SUBJECT + r"\s+is\s+(?:broken|bad|faulty|corrupt(?:ed)?|mangled"
        r"|missing|absent|down|wrong)\b", re.I)),
    ("does-not-work", re.compile(
        _SUBJECT + r"\s+(?:doesn'?t|does\s+not|isn'?t|is\s+not|won'?t"
        r"|will\s+not)\s+work(?:ing)?\b", re.I)),
    ("not-working", re.compile(_SUBJECT + r"\s+not\s+working\b", re.I)),
    ("active-damage", re.compile(
        _SUBJECT + r"\s+is\s+(?:mangling|corrupting|breaking|destroying"
        r"|clobbering|rewriting|losing)\b", re.I)),
    ("past-damage", re.compile(
        _SUBJECT + r"\s+(?:corrupted|mangled|broke|destroyed|clobbered"
        r"|deleted|wiped|lost)\s+(?:my|our|the)\b", re.I)),
)

# Reproduce-evidence markers: signs a cheap controlled test was actually run.
_EVIDENCE_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("reproduced-label", re.compile(r"\brepro(?:duced|duction)?\s*:", re.I)),
    ("controlled-test", re.compile(
        r"\bcontrolled\s+(?:[\w-]+\s+)?test\b|\bground[- ]truth\s+test\b", re.I)),
    ("exit-code", re.compile(
        r"\bexit\s+(?:code|status)\b|\bexit(?:ed)?\s+(?:with\s+)?\d+\b"
        r"|\becho\s+\$\?", re.I)),
    ("file-line-citation", re.compile(r"\b[\w./-]+\.[A-Za-z]{1,5}:\d+\b")),
    ("observed-output", re.compile(
        r"\bobserved\s+output\b|\bobserved:\s|\bactual\s+output\b", re.I)),
)

_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_SHELL_PROMPT_LINE = re.compile(r"^\s*[$%>]\s?\S", re.M)


def _claims_for(name: str, pattern: re.Pattern[str], text: str) -> list[dict]:
    """All claims one failure pattern makes in ``text`` (non-premise subjects dropped)."""
    return [
        {"pattern": name, "subject": m.group("subject"), "claim": m.group(0)}
        for m in pattern.finditer(text)
        if m.group("subject").lower() not in _NON_PREMISE_SUBJECTS
    ]


def _premise_matches(text: str) -> list[dict]:
    """Every distinct failure-premise claim in ``text`` (deduped by pattern+subject)."""
    all_claims = itertools.chain.from_iterable(
        _claims_for(name, pattern, text) for name, pattern in _FAILURE_PATTERNS
    )
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for claim in all_claims:
        key = (claim["pattern"], claim["subject"].lower())
        if key not in seen:
            seen.add(key)
            found.append(claim)
    return found


def _block_shows_command_and_output(block: str) -> bool:
    """True when one fenced block contains a shell command line plus output.

    A block with only the command line is not a reproduction — the evidence is
    the output that came back, so at least two lines are required.
    """
    return bool(_SHELL_PROMPT_LINE.search(block)) and len(block.strip().splitlines()) >= 2


def _evidence_names(text: str) -> list[str]:
    """Names of every reproduce-evidence marker present in ``text``."""
    names = [name for name, pattern in _EVIDENCE_MARKERS if pattern.search(text)]
    if any(_block_shows_command_and_output(b) for b in _FENCED_BLOCK.findall(text)):
        names.append("command-output-block")
    return names


def _reason(premises: list[dict], evidence: list[str], verdict: str) -> str:
    """Plain-language explanation of the verdict."""
    if not premises:
        return "no failure-premise assertion detected"
    claims = "; ".join(f'"{p["claim"]}"' for p in premises[:3])
    if verdict == "ok":
        return (f"failure premise ({claims}) carries reproduce evidence: "
                f"{', '.join(evidence)}")
    return (f"failure premise asserted ({claims}) with no controlled-reproduce "
            "evidence — run the <30s ground-truth test first and verify the "
            "premise, not the conclusion")


def classify_dispatch_prompt(prompt: str) -> dict:
    """Classify a dispatch prompt: failure premise asserted vs repro evidence carried.

    Returns the verdict dict documented in the module docstring. ``"warn"``
    means the prompt asserts something is broken/missing/corrupted without any
    sign the claim was reproduced by a controlled test; ``"ok"`` means either
    no failure premise, or the premise arrives with reproduce evidence.
    """
    text = prompt if isinstance(prompt, str) else ""
    premises = _premise_matches(text)
    evidence = _evidence_names(text)
    verdict = "warn" if premises and not evidence else "ok"
    return {
        "premise_detected": bool(premises),
        "evidence_present": bool(evidence),
        "verdict": verdict,
        "reason": _reason(premises, evidence, verdict),
        "premises": premises,
        "evidence": evidence,
    }
