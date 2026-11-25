#!/usr/bin/env python3
"""sme.py — Structure-Mapping Engine kernel (Gentner 1983).

Phase B of the SMT dogfood goal (lib/smt/AUDIT.md gap list):

  G1. Predicate representation — entities, attributes vs relations (with
      arity), higher-order tiering; the substrate every other fix needs.
  G2. Structural consistency — true 1-1 (both directions), parallel
      connectivity, systematicity as connectedness (not coverage).
  G3. SME stages 1-3 — local match construction (depth-weighted) + GMap
      greedy merge + structural evaluation.
  G4. SME stage 4 — candidate inference: unmapped base structure is
      PROJECTED onto the target, not penalised.

Faithful to the theory: relations matter more than attributes, higher-order
relations matter most, and systematicity is the size of the largest connected
substructure the mapping keeps consistent — not a coverage percentage.

The AUDIT's falsification probe must fail here:
    base:  YELLOW(sun);  target: YELLOW(orange)
    labelled "causal" — attribute-as-relation — was allowed at 86/100 by the
    old gate. In this kernel, YELLOW is an ATTRIBUTE (arity 1); a causal
    mapping claim between attributes is structurally inadmissible.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable, Optional

__all__ = [
    "Alignment",
    "CandidateInference",
    "Entity",
    "Predicate",
    "PredicateKind",
    "Structure",
    "build_structure",
    "candidate_inferences",
    "gmap",
    "local_match_hypotheses",
    "parallel_connectivity",
    "structural_consistency",
    "systematicity",
    "tier_match",
]


class PredicateKind:
    ATTRIBUTE = "attribute"   # arity 1: describes one entity
    RELATION = "relation"     # arity >= 2: binds entities
    HIGHER_ORDER = "higher_order"  # a relation whose args are relations


_TIER_WEIGHTS = {
    PredicateKind.ATTRIBUTE: 1.0,
    PredicateKind.RELATION: 2.0,
    PredicateKind.HIGHER_ORDER: 3.0,
}


@dataclass(frozen=True)
class Entity:
    name: str


@dataclass(frozen=True)
class Predicate:
    name: str
    kind: str
    args: tuple[str, ...]  # entity names (arity 1 = attribute)
    # higher-order: args are predicate names instead
    predicate_args: bool = False

    @property
    def arity(self) -> int:
        return len(self.args)

    def key(self) -> tuple:
        return (self.name, self.kind, self.args)


@dataclass(frozen=True)
class Structure:
    """A predicate structure: entities + the predicates that hold over them."""
    name: str
    entities: tuple[Entity, ...]
    predicates: tuple[Predicate, ...]

    def predicate_by_name(self, pname: str) -> Optional[Predicate]:
        for p in self.predicates:
            if p.name == pname:
                return p
        return None


def build_structure(
    name: str,
    entities: Iterable[str],
    attributes: Iterable[tuple[str, str]] = (),
    relations: Iterable[tuple[str, tuple[str, ...]]] = (),
    higher_order: Iterable[tuple[str, tuple[str, ...]]] = (),
) -> Structure:
    """Build a Structure from declarative lists.

    attributes:  (pred_name, entity)
    relations:   (pred_name, (e1, e2, ...))  arity >= 2
    higher_order: (pred_name, (pred1, pred2, ...)) args are predicate names
    """
    ents = tuple(Entity(e) for e in entities)
    preds = (
        tuple(Predicate(n, PredicateKind.ATTRIBUTE, (e,)) for n, e in attributes)
        + tuple(Predicate(n, PredicateKind.RELATION, args) for n, args in relations)
        + tuple(
            Predicate(n, PredicateKind.HIGHER_ORDER, args, predicate_args=True)
            for n, args in higher_order
        )
    )
    return Structure(name, ents, preds)


@dataclass(frozen=True)
class MatchHypothesis:
    """SME stage 1: one candidate correspondence (entity or predicate)."""
    kind: str            # "entity" | "predicate"
    base_item: str
    target_item: str
    tier: str            # identical | synonymous | different
    weight: float        # tier weight * kind weight

    @property
    def score(self) -> float:
        return self.weight


SYNONYMS: dict[str, frozenset[str]] = {}


def tier_match(base_name: str, target_name: str) -> str:
    """Predicate identity tiering (Gentner's tiered identicality).

    identical: same name. synonymous: different names mapped via the
    SYNONYMS table. different: no match.
    """
    if base_name == target_name:
        return "identical"
    if base_name in SYNONYMS and target_name in SYNONYMS[base_name]:
        return "synonymous"
    return "different"


def local_match_hypotheses(base: Structure, target: Structure) -> list[MatchHypothesis]:
    """SME stage 1 — local match construction.

    Entity pairs: (a) identical names, and (b) entities occupying the same
    argument slot of a matched (identical/synonymous) predicate pair — the
    classic SME move that lets solar-system ↔ atom align with zero shared
    names. Predicate pairs: tiered identity with the relation-over-attribute
    depth weighting (higher-order > relation > attribute).
    Attribute-to-relation and attribute-to-higher-order pairs are
    structurally inadmissible (the audit's falsification probe).
    """
    out: list[MatchHypothesis] = []
    entity_links: dict[tuple[str, str], float] = {}
    base_names = {e.name for e in base.entities}
    target_names = {e.name for e in target.entities}
    name_pairs: set[tuple[str, str]] = set()
    for bn in sorted(base_names & target_names):
        out.append(MatchHypothesis("entity", bn, bn, "identical", 1.0))
        entity_links[(bn, bn)] = 1.0
        name_pairs.add((bn, bn))
    base_preds = {p.name: p for p in base.predicates}
    target_preds = {p.name: p for p in target.predicates}
    for bn, bp in base_preds.items():
        for tn, tp in target_preds.items():
            if bp.kind != tp.kind or bp.arity != tp.arity:
                continue  # structure mismatch: never a match hypothesis
            tier = tier_match(bn, tn)
            if tier == "different":
                continue
            tier_factor = 1.0 if tier == "identical" else 0.75
            weight = _TIER_WEIGHTS[bp.kind] * tier_factor
            out.append(MatchHypothesis("predicate", bn, tn, tier, weight))
            if bp.predicate_args:
                continue  # higher-order args are predicate names, not entities
            # shared argument slots: entities at the same position become
            # match hypotheses weighted by the predicate's depth
            for b_arg, t_arg in zip(bp.args, tp.args):
                if b_arg in base_names and t_arg in target_names:
                    cur = entity_links.get((b_arg, t_arg), 0.0)
                    if weight > cur:
                        entity_links[(b_arg, t_arg)] = weight
    for (b_ent, t_ent), w in entity_links.items():
        if (b_ent, t_ent) not in name_pairs:
            out.append(MatchHypothesis("entity", b_ent, t_ent, "synonymous", w))
    return sorted(out, key=lambda m: (-m.score, m.base_item, m.target_item))


@dataclass(frozen=True)
class Alignment:
    """A mapping between two structures: entity map + predicate map."""
    entity_map: dict[str, str] = field(default_factory=dict)
    predicate_map: dict[str, str] = field(default_factory=dict)
    matched: tuple[MatchHypothesis, ...] = ()

    @property
    def size(self) -> int:
        return len(self.entity_map) + len(self.predicate_map)

    @property
    def score(self) -> float:
        return sum(m.score for m in self.matched)


def parallel_connectivity(base: Structure, target: Structure, a: Alignment) -> bool:
    """G2 — parallel connectivity: if predicates map, their ARGS must map.

    For every matched predicate pair, each base argument entity must be
    mapped to the target argument entity at the same position. Higher-order
    predicate args (predicate names) must themselves be matched.
    """
    for bn, tn in a.predicate_map.items():
        bp = base.predicate_by_name(bn)
        tp = target.predicate_by_name(tn)
        if bp is None or tp is None:
            return False
        for b_arg, t_arg in zip(bp.args, tp.args):
            if bp.predicate_args:
                if a.predicate_map.get(b_arg) != t_arg:
                    return False
            else:
                if a.entity_map.get(b_arg) != t_arg:
                    return False
    return True


def structural_consistency(base: Structure, target: Structure, a: Alignment) -> bool:
    """G2 — a mapping is structurally consistent iff it is 1-1 in BOTH
    directions (no two base items share a target) and parallel-connectivity
    holds. One-to-many and many-to-one both fail — the audit's complaint
    about the old gate's anti-Gentner over-mapping band."""
    if len(set(a.entity_map.values())) != len(a.entity_map):
        return False
    if len(set(a.predicate_map.values())) != len(a.predicate_map):
        return False
    return parallel_connectivity(base, target, a)


def systematicity(base: Structure, target: Structure, a: Alignment) -> float:
    """G2 — systematicity: size of the largest connected substructure the
    mapping keeps consistent (a 0-1 connectedness measure, not a coverage
    percentage)."""
    if not structural_consistency(base, target, a):
        return 0.0
    # connectivity graph over matched predicates: edge when they share an entity
    pnames = list(a.predicate_map)
    if not pnames:
        return 0.0
    parent = {p: p for p in pnames}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    by_entity: dict[str, list[str]] = {}
    for bn in pnames:
        bp = base.predicate_by_name(bn)
        if bp is None or bp.predicate_args:
            continue
        for e in bp.args:
            by_entity.setdefault(e, []).append(bn)
    for group in by_entity.values():
        for x, y in zip(group, group[1:]):
            union(x, y)
    largest = max(
        (sum(1 for q in pnames if find(q) == find(p)) for p in pnames),
        default=0,
    )
    return float(largest)


def gmap(
    base: Structure,
    target: Structure,
    hypotheses: Optional[list[MatchHypothesis]] = None,
    max_size: int = 100,
) -> Alignment:
    """SME stage 2 — GMap greedy merge.

    Greedily add the highest-scoring hypothesis that keeps the mapping
    structurally consistent (1-1 + parallel connectivity). This is the
    systematicity-tilted merge: an entity match pays its way only while it
    keeps matched predicates connected."""
    hyps = hypotheses if hypotheses is not None else local_match_hypotheses(base, target)
    chosen: list[MatchHypothesis] = []
    a = Alignment()
    # Greedy merge with REVISITS: a relation hypothesis is rejected while its
    # argument entities are unmapped, but becomes admissible once the entity
    # matches land. Repeat passes until a full pass adds nothing.
    changed = True
    passes = 0
    while changed and passes < len(hyps) + 1:
        changed = False
        passes += 1
        for m in hyps:
            if len(chosen) >= max_size:
                break
            trial = Alignment(
                dict(a.entity_map), dict(a.predicate_map), tuple(chosen) + (m,)
            )
            if m.kind == "entity":
                if m.base_item in a.entity_map or m.target_item in a.entity_map.values():
                    continue
                trial.entity_map[m.base_item] = m.target_item
            else:
                if m.base_item in a.predicate_map or m.target_item in a.predicate_map.values():
                    continue
                trial.predicate_map[m.base_item] = m.target_item
            if not structural_consistency(base, target, trial):
                continue
            a = trial
            chosen.append(m)
            changed = True
    return a


@dataclass(frozen=True)
class CandidateInference:
    """SME stage 4 — unmapped base structure PROJECTED onto the target."""
    base_predicate: str
    target_predicate_name: str
    kind: str
    args: tuple[str, ...]
    via: str  # e.g. "entity_map", "predicate_map", "mixed"


def candidate_inferences(base: Structure, target: Structure, a: Alignment) -> list[CandidateInference]:
    """Project every unmapped base predicate whose arguments are all mapped.

    Gentner: unmapped base structure is the SOURCE of candidate inferences —
    never 'orthogonal leftovers'. A projected predicate is a proposal about
    the target, not a suspicion about the mapping."""
    out: list[CandidateInference] = []
    for bp in base.predicates:
        if bp.name in a.predicate_map:
            continue
        if bp.predicate_args:
            mapped = [a.predicate_map.get(arg) for arg in bp.args]
            if any(m is None for m in mapped):
                continue
            out.append(CandidateInference(
                bp.name, bp.name, bp.kind, tuple(mapped), "predicate_map"))
        else:
            mapped = [a.entity_map.get(arg) for arg in bp.args]
            if any(m is None for m in mapped):
                continue
            out.append(CandidateInference(
                bp.name, bp.name, bp.kind, tuple(mapped), "entity_map"))
    return out
