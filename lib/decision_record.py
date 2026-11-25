"""Decision record — the "mind" of a delegated IDR (Decision Anatomy, Session A).

The signed / chained / provenanced IDR skeleton already captures WHEN a decision was
made and HOW (which model produced it). This module adds the missing layer: WHY, how
sure, what it was unsure about, what it assumed, and how it could be proven wrong —
as a first-class, plain-language, content-addressable part of a *delegated* IDR.

PRIME DIRECTIVE (rules/exogenous-anchor-law.md): a model's self-reported confidence
and reasoning are TELEMETRY, never CRITERION. This module therefore splits two layers
and never lets the second depend on the model's own word:

  * TRANSPARENCY (disclosed, signed, honest): why, confidence, uncertainties, assumptions
  * GOVERNANCE  (must resolve against something EXTERNAL): falsification_criteria

Nothing here returns a go / no-go from ``confidence`` — confidence is disclosure only.
Using it as a merge / ratify gate would rebuild self-certification (forbidden). The
``falsification_criteria`` of a delegated decision must name something an external
process can check, or it is rejected.

SMT gate (gentner, structural / one-to-one / relational): a field is admissible ONLY
if it maps to the decision-notarisation relation. Surface grafts — tone, persona,
"personality", confidence-as-criterion — are rejected by construction.

Pure stdlib, no GRIP-internal imports, so both ``from lib.decision_record import ...``
and ``from decision_record import ...`` resolve.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Optional, Tuple

__all__ = [
    "Decision",
    "DecisionError",
    "ADMISSIBLE_FIELDS",
    "validate",
    "is_predicate_shaped",
    "reject_surface_fields",
    "from_model_dict",
    "render_plain",
    "embed_in_idr_ref",
]


class DecisionError(ValueError):
    """Raised when a Decision violates the anatomy / honesty / SMT invariants."""


# The ONLY admissible fields — each maps to the decision-notarisation relation.
ADMISSIBLE_FIELDS = frozenset({
    "what", "why", "confidence", "uncertainties",
    "assumptions", "falsification_criteria", "falsifier_ref",
})

# Surface grafts that do NOT map to the relation — rejected by name even if a caller
# tries to smuggle them in (tone / persona / personality; confidence-as-criterion).
_SURFACE_GRAFTS = frozenset({
    "tone", "persona", "personality", "mood", "style", "voice", "vibe",
    "confidence_threshold", "confidence_gate", "ratify", "merge_decision",
    "approve", "auto_merge", "should_merge",
})

# External-checkability markers — a falsification criterion must contain >=1.
_PREDICATE_MARKERS = re.compile(
    r"(<=|>=|==|!=|<|>|"
    r"\b(?:exceeds?|below|above|equals?|differs?|matches?|mismatch|greater|less|"
    r"within|rejects?|fails?\s+if|falsified\s+if|wrong\s+if|breaks?\s+if|"
    r"diverges?|returns?|when\b|unless\b|over\b|under\b)|"
    r"test_|\.py\b|/|\bCI\b|AGORA|hypothesis)",
    re.IGNORECASE,
)
# Purely-subjective tells — present with NO predicate marker ⇒ not externally checkable.
_SUBJECTIVE_ONLY = re.compile(
    r"(i\s+think|i\s+believe|seems?\b|probably\b|feels?\s+right|"
    r"looks?\s+(?:good|right|fine)|i'?m\s+confident|trust\s+me)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Decision:
    """One delegated decision's anatomy. Immutable; content-addressable via
    ``content_hash``. Tiered: a deterministic decision (confidence 1.0) is minimal;
    a delegated one (confidence < 1.0) carries the full falsifiable anatomy."""

    what: str
    why: str = ""
    confidence: float = 1.0
    uncertainties: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    falsification_criteria: str = ""
    falsifier_ref: Optional[str] = None

    def is_deterministic(self) -> bool:
        return self.confidence >= 1.0

    def to_dict(self) -> dict:
        """Canonical, JSON-serialisable mapping (lists, not tuples)."""
        return {
            "what": self.what,
            "why": self.why,
            "confidence": self.confidence,
            "uncertainties": list(self.uncertainties),
            "assumptions": list(self.assumptions),
            "falsification_criteria": self.falsification_criteria,
            "falsifier_ref": self.falsifier_ref,
        }

    def content_hash(self) -> str:
        """SHA-256 over canonical JSON (sorted keys) — the structure-commit.

        Sign THIS (the structure); gate the plaintext separately for sensitive
        content (rules/no-sensitive-info-in-grip.md). Deterministic across runs and
        processes, so a dropped/edited field is a detectable hash divergence."""
        canon = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def is_predicate_shaped(text: str) -> bool:
    """True iff ``text`` names something an EXTERNAL process can check (a comparison,
    threshold, test / CI / file / metric reference, or a 'fails if ...' clause) and is
    not purely subjective. Heuristic floor — the exogenous-resolution guarantee is
    tracked over N>=10 by H-DECISION-ANATOMY, not asserted by this regex alone."""
    t = (text or "").strip()
    if not t:
        return False
    has_marker = bool(_PREDICATE_MARKERS.search(t))
    if not has_marker:
        return False
    # A marker plus a subjective frame is still external (e.g. "I think it fails if
    # latency > 200ms" has a real predicate); reject only when subjective AND no marker
    # — already returned above. So a marker present ⇒ predicate-shaped.
    return True


def validate(decision: Decision) -> Decision:
    """Enforce the tiered anatomy + honesty invariants. Return ``decision`` on
    success; raise ``DecisionError`` otherwise.

    * ``what`` non-empty; ``confidence`` a real number in [0, 1].
    * deterministic (confidence == 1.0): minimal — uncertainties AND assumptions MUST
      be empty (a certain decision that lists doubts is fabricating them).
    * delegated (confidence < 1.0): falsification_criteria MUST be non-empty AND
      predicate-shaped (externally checkable) — else the claim is self-certifying.
    """
    if not (decision.what or "").strip():
        raise DecisionError("Decision.what must be non-empty")
    c = decision.confidence
    if not isinstance(c, (int, float)) or isinstance(c, bool) or math.isnan(c) \
            or c < 0.0 or c > 1.0:
        raise DecisionError(f"confidence must be a real number in [0,1], got {c!r}")
    if decision.is_deterministic():
        if decision.uncertainties:
            raise DecisionError(
                "deterministic decision (confidence=1.0) must not list uncertainties "
                "— that fabricates doubt it does not have")
        if decision.assumptions:
            raise DecisionError(
                "deterministic decision (confidence=1.0) must not list assumptions")
    else:
        fc = (decision.falsification_criteria or "").strip()
        if not fc:
            raise DecisionError(
                "delegated decision (confidence<1.0) must state falsification_criteria")
        if not is_predicate_shaped(fc):
            raise DecisionError(
                "falsification_criteria must be externally checkable (a predicate / "
                f"threshold / test / 'fails if ...'), not self-certifying: {fc!r}")
    return decision


def reject_surface_fields(raw: dict) -> None:
    """SMT gate (gentner, mechanical): every key must map to the decision-notarisation
    relation. Reject known surface grafts (tone / persona / personality;
    confidence-as-criterion) and any non-admissible key. Raise ``DecisionError``."""
    keys = set(raw or {})
    grafts = keys & _SURFACE_GRAFTS
    if grafts:
        raise DecisionError(
            "surface-graft fields rejected (not the decision-notarisation relation): "
            f"{sorted(grafts)}")
    unknown = keys - ADMISSIBLE_FIELDS
    if unknown:
        raise DecisionError(f"non-admissible fields rejected: {sorted(unknown)}")


def from_model_dict(raw: dict) -> Decision:
    """Build + validate a Decision from a model's structured output — the delegated
    emit path. Applies the SMT gate, then the anatomy / honesty validator."""
    reject_surface_fields(raw)
    decision = Decision(
        what=raw.get("what", ""),
        why=raw.get("why", "") or "",
        confidence=float(raw.get("confidence", 1.0)),
        uncertainties=tuple(raw.get("uncertainties") or ()),
        assumptions=tuple(raw.get("assumptions") or ()),
        falsification_criteria=raw.get("falsification_criteria", "") or "",
        falsifier_ref=raw.get("falsifier_ref"),
    )
    return validate(decision)


def render_plain(decision: Decision) -> str:
    """Plain-language render (rules/canonical-output-style.md; eli5 the what / why).
    No jargon — a first-time reader understands it without asking."""
    sure = "certain (100%)" if decision.is_deterministic() \
        else f"{round(decision.confidence * 100)}% sure"
    lines = [f"Decision: {decision.what}"]
    if decision.why.strip():
        lines.append(f"Why: {decision.why}")
    lines.append(f"How sure: {sure}")
    if decision.uncertainties:
        lines.append("Unsure about: " + "; ".join(decision.uncertainties))
    if decision.assumptions:
        lines.append("Assuming: " + "; ".join(decision.assumptions))
    if decision.falsification_criteria.strip():
        lines.append(f"Would be wrong if: {decision.falsification_criteria}")
    if decision.falsifier_ref:
        lines.append(f"Checkable against: {decision.falsifier_ref}")
    return "\n".join(lines)


def embed_in_idr_ref(idr_ref_body: dict, decision: Optional[Decision]) -> dict:
    """Return a copy of an IDR-record body with a *validated* Decision attached —
    plus its ``content_hash``, so whoever signs the body also commits the decision
    (signed-into-the-IDR). Pure and back-compat: ``decision is None`` returns a
    shallow copy of the body unchanged, so existing IDR records are byte-identical.
    Never mutates the input. Raises ``DecisionError`` if the decision is invalid —
    a malformed decision is never embedded."""
    out = dict(idr_ref_body or {})
    if decision is None:
        return out
    validate(decision)
    out["decision"] = decision.to_dict()
    out["decision_hash"] = decision.content_hash()
    return out
