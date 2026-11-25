#!/usr/bin/env python3
"""inception.py — overnight divergent idea generation with a mechanical limbo test.

GRIP's existing loops (/converge, /rsi, /afk, the overnight runner) are
CONVERGENT: they are handed a task and driven to a criterion. This module is the
DIVERGENT counterpart — it generates candidate ideas nobody asked for, cheaply,
while the operator sleeps, and then throws almost all of them away.

The structure is borrowed from the film, but only where the mapping is genuinely
structural rather than decorative (Gentner SMT — relations must map, not surface
features):

    dream levels        -> recursion depth, each level elaborating the last
    time dilation       -> overnight wall-clock buys many Pool-B iterations at $0
    the kick            -> an EXOGENOUS criterion that ends a level from outside
                           it (rules/exogenous-anchor-law.md)
    the totem           -> a provenance seal: how you tell which reality a thing
                           came from (rules/cryptographic-causation.md)
    limbo               -> self-referential drift with no ground truth, i.e.
                           GRIP's documented confabulation failure mode

DELIBERATELY NOT MAPPED: the film's core mechanic is making the subject believe a
planted idea was their own. That is self-deception, which is the precise failure
this codebase exists to resist — a sovereign council (seal b32af619) already
ruled self-reports structurally unreliable. Ideas produced here stay untrusted
until an external anchor is reached. Nothing pretends to be a finding.

THE LOAD-BEARING IDEA — limbo is computable.

Each idea carries evidence edges. Some point at EXTERNAL anchors (a real file, a
test, an issue, a measured number, an exogenous verdict); others point only at
other dreamt ideas. Treat that as a directed graph and admissibility is a
reachability query: an idea survives only if its evidence chain TERMINATES at an
external anchor. An idea supported only by other dreams — however plausible it
sounds — is in limbo and is discarded on waking. No model judgement is involved,
so the check cannot itself be confabulated.

DESIGN CONSTRAINT — success is measured by how FEW ideas survive.

CORRECTED 2026-08-01. The first version of this module justified the constraint
with a 23:1 add-to-delete ratio taken from a usage report, and that figure was
wrong. Measured directly against origin/main from 29 June: 443 commits,
+1,341,866 / -1,257,833 across 5,775 files — a ratio of 1.07:1. The repository
deletes very nearly as much as it writes and is NOT accreting lines. The report
was counting lines produced inside sessions, where rewrites register as additions
and almost nothing registers as a deletion. The same wrong figure was passed to
the sovereign council (seal bb549e47) as part of its premise, so its
"accumulation rather than improvement" verdict inherits the error — a clean
verdict over a dirty input, which is the failure `prove-it-claims-canonical.md`
names exactly. The constraint survives; its stated reason did not.

The REAL pathology, measured the same day, is UNSELECTED ARTEFACT accumulation —
building things that are never evaluated, rather than writing too many lines:

  - the genome carries 777 genes; ALL 2,500 contribution records are False, none
    is True, and 93.5% of genes have no record at all. Its `generation` field
    equals the total run counter exactly. Machinery built, never selected against.
  - the achievement lineage holds 6,973 entries of which 6,745 (96.7%) are
    "Auto-checkpoint at N% context" — records that record nothing.
  - the ONE place selection demonstrably happens is learning/hypotheses.jsonl:
    664 predictions, 199 refuted against 142 confirmed. Refutation is the only
    mechanism in this system that costs something and can return a negative.

An overnight idea generator is precisely a machine for producing unselected
artefacts, so without a constraint it becomes the 778th gene: a subsystem whose
output nobody ever evaluates. Hence TWO admission requirements, not one — an idea
must be GROUNDED (its evidence chain reaches something real) and it must be
FALSIFIABLE (it states what would prove it wrong). An idea that cannot be proven
wrong cannot be selected against, and an idea that cannot be selected against is
exactly the thing this system already has too much of.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

__all__ = [
    "Idea",
    "USER_FACING",
    "INTERNAL",
    "AnchorResolver",
    "default_anchor_resolver",
    "limbo_sweep",
    "grounding_reasons",
    "descend",
    "WakeReport",
]

# An evidence string is a candidate EXTERNAL anchor when it looks like one of
# these. Shape-matching only decides candidacy; the resolver still has to confirm
# the referent exists, so a plausible-looking citation to a file that is not
# there does NOT anchor an idea. That distinction is the whole point: a
# fabricated citation must fail, exactly as it does in the /prove-it floor.
_FILE_LINE_RE = re.compile(r"^(?P<path>[\w./\-]+\.\w+)(?::(?P<line>\d+))?$")
_ISSUE_RE = re.compile(r"^(?:https://github\.com/[\w.\-]+/[\w.\-]+/(?:issues|pull)/\d+|#\d+)$")
_SEAL_RE = re.compile(r"^(?:seal|idr):[0-9a-f]{8,64}$", re.IGNORECASE)
_TEST_RE = re.compile(r"^test:[\w./\-]+(?:::[\w\-\[\]]+)?$")
_MEASURED_RE = re.compile(r"^measured:[^\s].*$")

# Evidence pointing at another dreamt idea.
_IDEA_REF_RE = re.compile(r"^idea:(?P<id>[\w\-]+)$")


def _is_asserted_anchor(evidence: str) -> bool:
    """True when this anchor is accepted on SHAPE and never checked.

    Issue, seal and measured anchors name referents outside the filesystem, so
    ``default_anchor_resolver`` admits them without probing — deliberate, and
    documented there. The consequence is that they are self-certifying: the
    generator writes ``#4248`` and the sweep believes it.

    File and test anchors are the opposite. They are probed against the real
    checkout, so a fabricated one FAILS. Those are the only anchors that carry
    information the generator could not simply assert.

    ``limbo_sweep`` needs the distinction the resolver deliberately does not
    make, which is why this lives here and the resolver is left alone.
    """
    ev = evidence.strip()
    return bool(_ISSUE_RE.match(ev) or _SEAL_RE.match(ev) or _MEASURED_RE.match(ev))

# What an idea is FOR. Two values, because the only distinction that needs
# enforcing is whether anyone outside the machine would ever notice.
#
# USER_FACING covers anything the operator sees, reads, waits on or feels — the
# wording of a report, the shape of a review, a delight, a surface that is
# currently ugly or silent. INTERNAL covers everything else.
#
# The default is INTERNAL deliberately: an idea is only user-facing when someone
# says so, so the balance check below cannot be satisfied by accident.
USER_FACING = "user-facing"
INTERNAL = "internal"


@dataclass(frozen=True)
class Idea:
    """One candidate generated during a descent.

    ``evidence`` holds opaque strings. Each is either an external-anchor
    candidate (``lib/foo.py:12``, ``#4231``, ``seal:bb549e47``, ``test:tests/t.py``,
    ``measured:1.07:1 add-to-delete``) or a reference to another idea
    (``idea:<id>``). Anything unrecognised anchors nothing — unknown shapes fail
    closed rather than being generously admitted.

    ``falsifier`` states what observation would prove the idea wrong. It is
    required for admission, not decorative: this system's measured pathology is
    accumulating artefacts nobody ever evaluates (777 genes with zero recorded
    successes; 96.7% of its achievement log content-free), and an idea with no
    falsifier cannot be evaluated even in principle. Grounding says the idea
    connects to reality; the falsifier says reality can push back.
    """

    id: str
    text: str
    level: int
    evidence: tuple[str, ...] = ()
    parent_id: Optional[str] = None
    falsifier: Optional[str] = None
    domain: str = INTERNAL

    def is_falsifiable(self) -> bool:
        """True when the idea states a non-empty way it could be proven wrong."""
        return bool(self.falsifier and self.falsifier.strip())

    def totem(self) -> str:
        """Content hash binding this idea to its text, level and evidence.

        Not a security signature — the IDR chain does that. This is the cheap
        check that an idea surfacing in the morning is the one that was generated,
        unmodified, in the night.
        """
        payload = json.dumps(
            {"id": self.id, "text": self.text, "level": self.level,
             "evidence": list(self.evidence), "parent": self.parent_id,
             "falsifier": self.falsifier},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# A resolver answers one question: does this evidence string name something that
# really exists? Injected so tests never touch the filesystem or the network.
AnchorResolver = Callable[[str], bool]


def _resolve_file_anchor(root: Path, evidence: str) -> bool:
    """Confirm a ``path`` or ``path:line`` anchor against a real checkout."""
    m = _FILE_LINE_RE.match(evidence)
    if not m:
        return False
    target = root / m.group("path")
    if not target.is_file():
        return False
    line = m.group("line")
    if line is None:
        return True
    try:
        with target.open(encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh) >= int(line)
    except OSError:
        return False


def default_anchor_resolver(repo_root: str | Path) -> AnchorResolver:
    """Resolver that confirms file/test anchors against a real checkout.

    Issue, seal and measured anchors are accepted on shape: their referents live
    outside the filesystem (GitHub, the council record, a report), and probing
    them here would make the sweep non-deterministic and network-dependent. That
    is a deliberate, stated weakening — a fabricated issue number passes this
    layer and must be caught downstream by /prove-it.
    """
    root = Path(repo_root)

    def resolve(evidence: str) -> bool:
        ev = evidence.strip()
        if _ISSUE_RE.match(ev) or _SEAL_RE.match(ev) or _MEASURED_RE.match(ev):
            return True
        if _TEST_RE.match(ev):
            return (root / ev.split(":", 1)[1].split("::", 1)[0]).exists()
        return _resolve_file_anchor(root, ev)

    return resolve


def limbo_sweep(
    ideas: Iterable[Idea], resolver: AnchorResolver,
) -> tuple[list[Idea], list[Idea]]:
    """Partition ideas into ``(admitted, limbo)`` on TWO independent conditions.

    An idea is ADMITTED only when both hold:

    1. GROUNDED — its evidence chain reaches at least one PROBED anchor, directly
       or transitively through other ideas. Probed means checked against the real
       checkout: a file or test path that actually exists. An anchor accepted on
       shape alone (``#4248``, ``seal:…``, ``measured:…``) may corroborate but
       cannot ground, because the generator can simply assert it.
    2. FALSIFIABLE — it states what would prove it wrong.

    It is in LIMBO otherwise: a cycle of mutually-supporting dreams, a reference
    to an idea never generated, a citation to a file that does not exist, an idea
    resting only on self-certifying anchors, no evidence at all — or a
    well-grounded idea that cannot be proven wrong.

    The second condition is not decoration. Grounding alone admits ideas that
    connect to reality but can never be evaluated, and unevaluated artefacts are
    this system's measured pathology (see the module docstring: 777 genes with
    zero recorded successes, 96.7% of the achievement log content-free). An idea
    nothing can refute would join that pile.

    Note the asymmetry: grounding propagates through the graph, falsifiability
    does NOT. An idea may inherit support from its parent, but it may not inherit
    a falsifier — the parent's refutation condition tests the parent's claim, not
    this one. Each idea states its own or it does not survive.

    Inheritance is available only to an idea that makes NO evidential claim of
    its own. An idea that cites something and is wrong about it has staked a
    testable claim and lost; rescuing it through its parent would treat a FAILED
    claim exactly like an ABSENT one, which is the immunising move this module
    exists to resist. Silence may inherit. A refuted citation may not.

    Cycles terminate safely: a node already being visited contributes no support,
    so ``A cites B cites A`` is limbo rather than infinite recursion. That case is
    not hypothetical — mutual support is the characteristic shape of a
    confabulated chain, which is why it is tested explicitly.
    """
    admitted, limbo, _ = _sweep(ideas, resolver)
    return admitted, limbo


def grounding_reasons(
    ideas: Iterable[Idea], resolver: AnchorResolver,
) -> dict[str, str]:
    """Why each idea was or was not grounded, keyed by idea id.

    Same walk as ``limbo_sweep`` — literally the same call — so the two can
    never disagree. This exists because a bare survival rate cannot answer the
    question that actually matters: *is the rate real?* The 2026-08-01 defect
    (#4251) read 100% survival while every checkable citation in the run was
    fabricated, and a reason histogram would have shown ``own-shape-only`` at
    100% on the very first night instead of surviving two runs and a merged PR.

    Values:
      ``own-probed``     an own file/test anchor was confirmed against the checkout
      ``via-idea-ref``   grounded transitively through another grounded idea
      ``via-parent``     claimed nothing and inherited from a grounded parent
      ``own-shape-only`` cited only self-certifying anchors — no longer enough
      ``unresolved``     staked claims and every one of them failed
      ``none``           no evidence and no grounded parent
    """
    return _sweep(ideas, resolver)[2]


# Reasons that count as grounded. The other four are the ways an idea fails.
_GROUNDING_REASONS = frozenset({"own-probed", "via-idea-ref", "via-parent"})


def _own_evidence_reason(
    idea: Idea, resolver: AnchorResolver, grounded: Callable[[str], bool],
) -> Optional[str]:
    """Verdict from the idea's OWN evidence, or None if it staked nothing.

    None means the idea is silent — it cited nothing at all — and only silence
    is allowed to inherit from a parent. An idea that cited something and was
    wrong about it returns a failing reason here rather than None, so its parent
    cannot rescue it: treating a FAILED claim like an ABSENT one is the
    immunising move this module exists to resist.
    """
    saw_asserted = False
    for ev in idea.evidence:
        ref = _IDEA_REF_RE.match(ev.strip())
        if ref:
            if grounded(ref.group("id")):
                return "via-idea-ref"
            continue
        if resolver(ev):
            if _is_asserted_anchor(ev):
                saw_asserted = True  # corroboration; keep looking for real support
                continue
            return "own-probed"
    if idea.evidence:
        return "own-shape-only" if saw_asserted else "unresolved"
    return None


def _sweep(
    ideas: Iterable[Idea], resolver: AnchorResolver,
) -> tuple[list[Idea], list[Idea], dict[str, str]]:
    """Shared implementation behind ``limbo_sweep`` and ``grounding_reasons``."""
    by_id = {i.id: i for i in ideas}
    reasons: dict[str, str] = {}
    visiting: set[str] = set()

    def reason_for(idea: Idea) -> str:
        own = _own_evidence_reason(idea, resolver, grounded)
        if own is not None:
            return own
        # Only silence inherits — see _own_evidence_reason.
        if idea.parent_id and grounded(idea.parent_id):
            return "via-parent"
        return "none"

    def reason(idea_id: str) -> str:
        if idea_id in reasons:
            return reasons[idea_id]
        if idea_id in visiting:
            return "cycle"  # contributes no independent support
        idea = by_id.get(idea_id)
        if idea is None:
            return "dangling"  # a reference to an idea never generated
        visiting.add(idea_id)
        result = reason_for(idea)
        visiting.discard(idea_id)
        reasons[idea_id] = result
        return result

    def grounded(idea_id: str) -> bool:
        return reason(idea_id) in _GROUNDING_REASONS

    # Grounding is transitive (it may come from a parent); falsifiability is not
    # (a parent's refutation condition does not test this idea's claim).
    admitted, limbo = [], []
    for idea in by_id.values():
        ok = grounded(idea.id) and idea.is_falsifiable()
        (admitted if ok else limbo).append(idea)
    return admitted, limbo, {i.id: reason(i.id) for i in by_id.values()}


@dataclass
class WakeReport:
    """What survived the night, and whether the run behaved."""

    admitted: list[Idea] = field(default_factory=list)
    limbo: list[Idea] = field(default_factory=list)
    levels: int = 0

    @property
    def generated(self) -> int:
        return len(self.admitted) + len(self.limbo)

    @property
    def survival_rate(self) -> float:
        return (len(self.admitted) / self.generated) if self.generated else 0.0

    def is_healthy(self, ceiling: float = 0.35, floor: float = 0.05) -> bool:
        """A run is healthy when MOST — but not ALL — of what it dreamt died.

        Inverted deliberately at the top. A generator whose output is mostly
        admitted is either anchoring trivially or not diverging at all, and
        either way it adds to the pile of things nobody evaluates.

        The FLOOR was added 2026-08-01 (#4251) and is not symmetry for its own
        sake. The test used to be ``survival_rate <= ceiling``, and 0.00 <= 0.35
        is True — so a night in which the generator produced nothing usable
        whatsoever reported as HEALTHY. That is worse than the stuck-False it
        replaced: an uninformative constant became an actively misleading one.
        Both ends now have to be cleared.

        An empty run is still exempt: generating nothing is a valid, honest
        night, and is different in kind from generating eleven ideas of which
        none survive.

        Both thresholds are UNCALIBRATED. They cannot be calibrated until the
        rate is capable of varying, which is what the rest of #4251 fixes; the
        distribution over the next runs is what should set them.
        """
        if self.generated == 0:
            return True
        return floor <= self.survival_rate <= ceiling

    def grounding_mix(self, reasons: dict[str, str]) -> dict[str, int]:
        """Histogram of WHY each idea was grounded, from ``grounding_reasons``.

        The survival rate says how many survived; this says whether the number
        means anything. A run reading ``own-shape-only`` at 100% is a run whose
        every idea rested on an anchor the generator simply asserted — exactly
        the 2026-08-01 defect, which a single glance at this histogram would
        have caught on night one.
        """
        mix: dict[str, int] = {}
        for idea in (*self.admitted, *self.limbo):
            key = reasons.get(idea.id, "unknown")
            mix[key] = mix.get(key, 0) + 1
        return mix

    @property
    def domain_mix(self) -> dict[str, int]:
        """Admitted ideas counted by domain."""
        mix: dict[str, int] = {}
        for idea in self.admitted:
            mix[idea.domain] = mix.get(idea.domain, 0) + 1
        return mix

    def is_balanced(self) -> bool:
        """True when the night produced at least one USER-FACING idea.

        A generator left to itself drifts entirely into internals — refactors,
        gates, calibration — because that is what its evidence is richest about.
        A night that dreams only about the machine has failed its remit even when
        every idea it admitted is sound: the point is also to improve what the
        operator actually sees and feels, which is the half no internal metric
        ever asks for. An empty night is balanced by default; nothing was
        neglected because nothing was produced.
        """
        return not self.admitted or any(
            i.domain == USER_FACING for i in self.admitted
        )

    def to_dict(self) -> dict:
        return {
            "generated": self.generated,
            "admitted": len(self.admitted),
            "limbo": len(self.limbo),
            "survival_rate": round(self.survival_rate, 4),
            "levels": self.levels,
            "healthy": self.is_healthy(),
            "balanced": self.is_balanced(),
            "domain_mix": self.domain_mix,
            "ideas": [
                {"id": i.id, "text": i.text, "level": i.level,
                 "domain": i.domain, "evidence": list(i.evidence),
                 "falsifier": i.falsifier, "totem": i.totem()}
                for i in self.admitted
            ],
        }


def _expand(
    frontier: list[Idea], generate: Callable[[Idea, int], list[Idea]], depth: int,
) -> list[Idea]:
    """Produce one level's worth of ideas from the current frontier."""
    produced: list[Idea] = []
    for idea in frontier:
        produced.extend(generate(idea, depth))
    return produced


def descend(
    seeds: Iterable[Idea],
    generate: Callable[[Idea, int], list[Idea]],
    resolver: AnchorResolver,
    *,
    levels: int = 3,
    kick: Optional[Callable[[list[Idea], int], bool]] = None,
) -> WakeReport:
    """Run a descent, then sweep.

    ``generate`` is injected (DIP): in production it calls a Pool-B model, in
    tests it is a pure function, so the whole cycle is exercised without spending
    a provider call or depending on non-deterministic output.

    ``kick`` is the exogenous stop. Returning False ends the descent at that level
    — the mechanism for a deterministic test or an outside council to halt a
    runaway from outside the loop. Without a kick the only bound is ``levels``,
    which is the film's point: you need something external to wake you.
    """
    everything: list[Idea] = list(seeds)
    frontier: list[Idea] = list(everything)
    reached = 0

    for depth in range(1, max(0, levels) + 1):
        if not frontier:
            break
        produced = _expand(frontier, generate, depth)
        if not produced:
            break
        everything.extend(produced)
        reached = depth
        if kick is not None and not kick(produced, depth):
            break
        frontier = produced

    admitted, limbo = limbo_sweep(everything, resolver)
    return WakeReport(admitted=admitted, limbo=limbo, levels=reached)
