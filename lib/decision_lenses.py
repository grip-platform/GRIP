"""Decision lenses — deterministic four-lens advisory evaluator for decision contexts.

GRIP-native design. Given a decision context (any prose describing a decision,
its rationale, and its evidence), four structural lenses are applied:

- evidence:    does the text ground its claims in specifics (paths, numbers,
               URLs, dates, symbols, citations)?
- consistency: does the text avoid internal contradictions (conflicting
               numeric claims for one label; always/never polarity flips)?
- challenge:   does the text engage counter-positions (falsification
               conditions, risks, trade-offs, limitations)?
- synthesis:   deterministic fold of the three lens results into one
               advisory verdict (STRONG / ADEQUATE / WEAK) plus gap list.

Design properties (each mechanically checkable, see tests/test_decision_lenses.py):

- Deterministic: same input always produces byte-identical output. Zero LLM
  calls, zero network, zero filesystem reads. Stdlib only.
- Falsifiable: every lens documents exactly what it detects; a verdict is
  wrong if a listed marker is present but unreported, or reported but absent.
- Anti-gaming: markers are counted as DISTINCT matches — repeating one
  marker ("risk risk risk") cannot inflate a score.
- Advisory only: no gate wiring. The CLI always exits 0 on valid input;
  the verdict lives in the report, never in the exit code.

Known limits (documented, not hidden): lenses are lexical heuristics over
English prose. They measure the structural SHAPE of a decision write-up
(grounded / consistent / challenged), not its semantic truth.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LensResult:
    """Outcome of one lens applied to one decision context."""

    lens: str
    score: float  # 0.0 .. 1.0
    findings: tuple[str, ...]
    advisory: str


@dataclass(frozen=True)
class SynthesisReport:
    """Fold of all lens results into one advisory verdict."""

    overall: float  # 0.0 .. 1.0
    verdict: str  # STRONG | ADEQUATE | WEAK
    lenses: tuple[LensResult, ...]
    gaps: tuple[str, ...]


# ---------------------------------------------------------------------------
# Tunables (module-level constants so tests can anchor them)
# ---------------------------------------------------------------------------

# Evidence markers required scales with claim volume (sentence count),
# floored at 3 so a one-line context still needs real grounding.
EVIDENCE_FLOOR = 3

# Distinct challenge markers needed for a full challenge score.
CHALLENGE_TARGET = 3

# Each detected contradiction removes half the consistency score.
CONSISTENCY_PENALTY = 0.5

# A lens scoring below this contributes a remediation gap to the synthesis.
GAP_THRESHOLD = 0.6

# Synthesis weights: ungrounded claims are the worst failure shape,
# contradiction next, missing challenge least fatal. Must sum to 1.0
# (anchored by a test).
WEIGHTS = {"evidence": 0.40, "consistency": 0.35, "challenge": 0.25}

VERDICT_STRONG = 0.75
VERDICT_ADEQUATE = 0.45

_EVIDENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("path", re.compile(r"(?:^|[\s(`'\"])((?:[\w.~-]+/)+[\w.-]+\.\w{1,8})")),
    ("url", re.compile(r"https?://\S+")),
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("number", re.compile(r"(?<![\w.\-])\d+(?:\.\d+)?%?(?![\w.\-])")),
    ("symbol", re.compile(r"`[^`\n]+`")),
    ("citation", re.compile(r"\b(?:per|see|source:|ref:)\s+\S+", re.IGNORECASE)),
)

_CHALLENGE_MARKERS: tuple[str, ...] = (
    "wrong if",
    "fails if",
    "falsif",  # falsified / falsifiable / falsification
    "risk",
    "however",
    "trade-off",
    "tradeoff",
    "downside",
    "counter-argument",
    "counterargument",
    "objection",
    "limitation",
    "edge case",
    "unless",
    "alternative",
    "on the other hand",
    "caveat",
    "weakness",
)

_NUMERIC_CLAIM = re.compile(
    r"\b([\w-]+)\s+(?:is|was|=|equals)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE
)
_POLARITY = re.compile(r"\b(always|never)\s+(\w+)", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"[.!?]+(?:\s|$)")

_ADVISORIES = {
    "evidence": "Ground each claim in a specific: a path, number, URL, date, symbol, or citation.",
    "consistency": "Resolve the contradiction: one label must carry one value; drop always/never flips.",
    "challenge": "State what would make this decision wrong: a falsification condition, risk, or trade-off.",
}


def _sentence_count(text: str) -> int:
    return sum(1 for s in _SENTENCE_SPLIT.split(text) if s.strip())


# ---------------------------------------------------------------------------
# The three scanning lenses
# ---------------------------------------------------------------------------


def lens_evidence(text: str) -> LensResult:
    """Score how grounded the text is in distinct, specific evidence markers.

    score = min(1.0, distinct_markers / max(EVIDENCE_FLOOR, sentence_count)).
    Distinct = unique matched strings per kind, so repetition cannot inflate.
    """
    findings: list[str] = []
    total = 0
    for kind, pattern in _EVIDENCE_PATTERNS:
        matches = sorted(set(pattern.findall(text)))
        if matches:
            total += len(matches)
            findings.append(f"{kind}: {len(matches)} distinct (e.g. {matches[0].strip()})")
    required = max(EVIDENCE_FLOOR, _sentence_count(text))
    score = min(1.0, total / required) if required else 0.0
    advisory = "" if score >= GAP_THRESHOLD else _ADVISORIES["evidence"]
    return LensResult("evidence", round(score, 4), tuple(findings), advisory)


def lens_consistency(text: str) -> LensResult:
    """Score internal consistency: 1.0 minus a penalty per contradiction.

    Detects (a) one label asserted with two distinct numeric values, and
    (b) the same word used under both 'always' and 'never'.
    """
    conflicts: list[str] = []
    values: dict[str, set[str]] = {}
    for label, value in _NUMERIC_CLAIM.findall(text):
        values.setdefault(label.lower(), set()).add(value)
    for label in sorted(values):
        if len(values[label]) > 1:
            conflicts.append(
                f"numeric conflict: '{label}' = {{{', '.join(sorted(values[label]))}}}"
            )
    polarity: dict[str, set[str]] = {}
    for word_pol, term in _POLARITY.findall(text):
        polarity.setdefault(term.lower(), set()).add(word_pol.lower())
    for term in sorted(polarity):
        if polarity[term] == {"always", "never"}:
            conflicts.append(f"polarity conflict: '{term}' asserted both always and never")
    score = max(0.0, 1.0 - CONSISTENCY_PENALTY * len(conflicts))
    advisory = "" if score >= GAP_THRESHOLD else _ADVISORIES["consistency"]
    return LensResult("consistency", round(score, 4), tuple(conflicts), advisory)


def lens_challenge(text: str) -> LensResult:
    """Score engagement with counter-positions via distinct challenge markers.

    score = min(1.0, distinct_markers_present / CHALLENGE_TARGET). Each marker
    phrase counts at most once however often it repeats (anti-stuffing).
    """
    lowered = text.lower()
    present = tuple(m for m in _CHALLENGE_MARKERS if m in lowered)
    score = min(1.0, len(present) / CHALLENGE_TARGET)
    findings = tuple(f"marker: '{m}'" for m in present)
    advisory = "" if score >= GAP_THRESHOLD else _ADVISORIES["challenge"]
    return LensResult("challenge", round(score, 4), findings, advisory)


# ---------------------------------------------------------------------------
# Synthesis lens (deterministic fold — no text scanning)
# ---------------------------------------------------------------------------


def synthesise(results: tuple[LensResult, LensResult, LensResult]) -> SynthesisReport:
    """Fold the three scanning-lens results into one advisory verdict.

    overall = sum(WEIGHTS[lens] * score). Bands: >= VERDICT_STRONG -> STRONG,
    >= VERDICT_ADEQUATE -> ADEQUATE, else WEAK. Every lens below
    GAP_THRESHOLD contributes its remediation advisory to the gap list.
    """
    overall = round(sum(WEIGHTS[r.lens] * r.score for r in results), 4)
    if overall >= VERDICT_STRONG:
        verdict = "STRONG"
    elif overall >= VERDICT_ADEQUATE:
        verdict = "ADEQUATE"
    else:
        verdict = "WEAK"
    gaps = tuple(f"{r.lens}: {r.advisory}" for r in results if r.advisory)
    return SynthesisReport(overall, verdict, tuple(results), gaps)


def evaluate(text: str) -> SynthesisReport:
    """Run all four lenses over one decision context.

    Empty/whitespace input short-circuits to WEAK with zero scores — a vacuous
    context must never be propped up by vacuous consistency (no text, no
    contradictions, would otherwise score 1.0).
    """
    if not text.strip():
        empty = tuple(
            LensResult(name, 0.0, (), _ADVISORIES[name])
            for name in ("evidence", "consistency", "challenge")
        )
        return SynthesisReport(0.0, "WEAK", empty, ("no decision context provided",))
    results = (lens_evidence(text), lens_consistency(text), lens_challenge(text))
    return synthesise(results)


# ---------------------------------------------------------------------------
# Serialisation + CLI
# ---------------------------------------------------------------------------


def to_dict(report: SynthesisReport) -> dict:
    """JSON-safe dict form of a report (tuples become lists)."""
    return {
        "overall": report.overall,
        "verdict": report.verdict,
        "lenses": [
            {
                "lens": r.lens,
                "score": r.score,
                "findings": list(r.findings),
                "advisory": r.advisory,
            }
            for r in report.lenses
        ],
        "gaps": list(report.gaps),
    }


def render_text(report: SynthesisReport) -> str:
    """Human-readable structured report."""
    lines = [f"verdict: {report.verdict}  (overall {report.overall:.2f})"]
    for r in report.lenses:
        lines.append(f"  {r.lens:<12} {r.score:.2f}")
        lines.extend(f"    - {f}" for f in r.findings)
    if report.gaps:
        lines.append("gaps:")
        lines.extend(f"  ! {g}" for g in report.gaps)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grip-lenses",
        description="Decision-lens advisory: evidence / consistency / challenge / synthesis.",
    )
    parser.add_argument("path", nargs="?", help="file with the decision context (default: stdin)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)
    if args.path:
        with open(args.path, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()
    report = evaluate(text)
    print(json.dumps(to_dict(report), indent=2) if args.json else render_text(report))
    return 0  # advisory only — verdict lives in the report, never the exit code


if __name__ == "__main__":
    raise SystemExit(main())
