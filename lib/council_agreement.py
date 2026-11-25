"""Measure inter-speaker agreement on GRIP's sealed sovereign councils.

Answers the question the federation-ceilings analysis named as its own central
gap: where does a real GRIP council actually sit on agreement, and is the
error-correlation rho measurable from the records we already keep?

Two findings are structural, not incidental, and this module encodes both:

1. Per-speaker output IS recorded (``speakers[].answer``), but as free prose.
   Only a minority of councils emit a parseable ``VERDICT: <LABEL>`` line, so
   the measurable population is much smaller than the seal count.

2. rho is NOT measurable from these records. rho is the correlation of
   *errors*, which requires knowing which speaker was *wrong*. No seal, and no
   record in the IDR chain, carries an outcome field. ``rho_measurable``
   reports that verdict against live data rather than asserting it in a docstring.

Read-only: nothing here writes to the council store.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "extract_verdict",
    "council_verdicts",
    "pairwise_agreement",
    "AgreementReport",
    "analyse",
    "load_seals",
    "rho_measurable",
    "GROUND_TRUTH_TOKENS",
]

# A verdict line looks like "VERDICT: BUILD-MINIMAL" or "**Verdict**: NEEDS-FIX".
# The character class deliberately excludes "," so an option list truncates
# rather than swallowing the whole sentence; the tail is then inspected below.
_VERDICT_RE = re.compile(
    r"\**\s*VERDICT\**\s*[:\-—]\s*\**\s*([A-Za-z][A-Za-z0-9 _/\-]{0,40})",
    re.IGNORECASE,
)

# A capture opening with one of these is the speaker restating the *question*,
# not answering it ("VERDICT: is the estate FINISHED?").
_INTERROGATIVE = frozenset(
    {"IS", "WAS", "SHOULD", "DOES", "DO", "ARE", "CAN", "WILL", "HAS", "HAVE",
     "WHICH", "WHAT", "WHETHER", "WHY", "HOW"}
)

# A capture opening with one of these is prose spilling past the label.
_PROSE_OPENER = frozenset(
    {"GIVEN", "BASED", "AFTER", "WHILE", "BECAUSE", "SINCE", "THE", "THIS",
     "THAT", "MY", "IT", "THERE", "AN", "A"}
)

# Tail begins with a separator then another ALL-CAPS option => the capture was
# the first item of a restated option list ("VERDICT: MERGE, REQUEST_CHANGES, ...").
_TAIL_OPTION_LIST = re.compile(r"^\s*[,/]\s*\**\s*[A-Z][A-Z_]{2,}")
_TAIL_OR = re.compile(r"^\s*\**\s*\bor\b", re.IGNORECASE)
# Tail begins with ":" => the capture was a lead-in label, not a verdict.
_TAIL_COLON = re.compile(r"^\s*\**\s*:")

# Keys that would let a record say which verdict turned out wrong.
# Deliberately narrow. A bare "correct" was tried first and false-positived on
# `decision.corrections` — a free-text list of document edits in an unrelated
# ip-moat record, which says nothing about whether a SPEAKER was wrong.
GROUND_TRUTH_TOKENS = (
    "outcome", "refuted", "reverted", "was_right",
    "ground_truth", "actual_verdict", "verdict_correct",
)


def _normalise(raw: str) -> str:
    """Collapse a raw capture to a comparable label."""
    text = raw.strip().strip("*_ .")
    text = re.split(r"\s{2,}|\s*\(|\s*—|\s*--", text)[0]
    return re.sub(r"\s+", " ", text).strip().upper()


def _is_title_case_heading(raw: str) -> bool:
    """True for "CodeTonight Estate Finale" — a heading, not a verdict label."""
    words = raw.strip().strip("*_ .").split()
    if len(words) < 2:
        return False
    if any(w.isupper() and len(w) > 2 for w in words):
        return False
    return all(w[:1].isupper() for w in words if w)


def _is_artefact(raw: str, tail: str) -> bool:
    """True when a regex hit is not actually a verdict the speaker chose."""
    label = _normalise(raw)
    if not label:
        return True
    head = label.split(" ", 1)[0]
    if " OR " in f" {label} ":
        return True
    if head in _INTERROGATIVE or head in _PROSE_OPENER:
        return True
    if _is_title_case_heading(raw):
        return True
    return bool(
        _TAIL_OPTION_LIST.match(tail)
        or _TAIL_OR.match(tail)
        or _TAIL_COLON.match(tail)
    )


def extract_verdict(answer: str | None) -> str | None:
    """Return the speaker's categorical verdict, or None if it emitted none.

    Returns None both when no ``VERDICT:`` line exists and when the line is an
    artefact (a restated question, a restated option list, a heading, or prose).
    """
    if not answer:
        return None
    match = _VERDICT_RE.search(answer)
    if not match:
        return None
    tail = answer[match.end(1):match.end(1) + 60]
    if _is_artefact(match.group(1), tail):
        return None
    return _normalise(match.group(1))


def council_verdicts(seal: dict) -> dict[str, str]:
    """Map speaker name -> verdict, for speakers that answered and chose one."""
    out: dict[str, str] = {}
    for speaker in seal.get("speakers") or []:
        if not speaker.get("ok"):
            continue
        verdict = extract_verdict(speaker.get("answer"))
        if verdict:
            out[str(speaker.get("name"))] = verdict
    return out


def pairwise_agreement(verdicts: dict[str, str]) -> tuple[int, int]:
    """Return (agreeing_pairs, total_pairs) over the speakers that voted."""
    names = sorted(verdicts)
    agree = total = 0
    for left, right in combinations(names, 2):
        total += 1
        agree += verdicts[left] == verdicts[right]
    return agree, total


@dataclass
class AgreementReport:
    """Result of an agreement pass over a set of seals."""

    seals_total: int = 0
    seals_multi_speaker: int = 0
    seals_measurable: int = 0
    agreeing_pairs: int = 0
    total_pairs: int = 0
    unanimous: int = 0
    per_council: list[tuple[str, int, int, dict[str, str]]] = field(default_factory=list)

    @property
    def agreement_rate(self) -> float | None:
        """Pooled pairwise agreement, or None when nothing was measurable."""
        if not self.total_pairs:
            return None
        return self.agreeing_pairs / self.total_pairs

    @property
    def unanimity_rate(self) -> float | None:
        if not self.seals_measurable:
            return None
        return self.unanimous / self.seals_measurable


def analyse(seals: Iterable[dict]) -> AgreementReport:
    """Measure pairwise agreement across every council that recorded verdicts."""
    report = AgreementReport()
    for seal in seals:
        report.seals_total += 1
        speakers = seal.get("speakers") or []
        if len(speakers) > 1:
            report.seals_multi_speaker += 1
        verdicts = council_verdicts(seal)
        if len(verdicts) < 2:
            continue
        agree, total = pairwise_agreement(verdicts)
        report.seals_measurable += 1
        report.agreeing_pairs += agree
        report.total_pairs += total
        report.unanimous += agree == total
        report.per_council.append(
            (str(seal.get("sha256", ""))[:10], agree, total, verdicts)
        )
    return report


def load_seals(directory: Path) -> list[dict]:
    """Read every sealed council JSON in ``directory``, skipping unreadable files."""
    seals: list[dict] = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            seals.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
    return seals


def rho_measurable(records: Sequence[dict]) -> tuple[bool, str]:
    """Report whether error-correlation rho can be computed from ``records``.

    rho needs to know which speaker was *wrong*, so it needs a per-verdict
    outcome. This walks every nested key and reports the verdict against the
    data actually present rather than asserting it.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if any(tok in key.lower() for tok in GROUND_TRUTH_TOKENS):
                    found.add(key)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for record in records:
        walk(record)
    if found:
        return True, f"outcome-bearing keys present: {sorted(found)}"
    return False, (
        "no outcome field at any nesting depth — which verdict was WRONG is "
        "never recorded, so agreement is measurable but rho is not"
    )
