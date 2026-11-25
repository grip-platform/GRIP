#!/usr/bin/env python3
"""relational_shift.py — Relational Shift + Comparison in Learning.

AUDIT gap list items 4 and 6:

  - Alignable differences: compute the DIFFERENCES between two ALIGNED
    structures (predicate present in one, absent in the other, argument-role
    flips) — the audit found the concept alive only as a gate on claimed
    analogies, never as a learning computation.
  - Progressive alignment: pairwise structural pairing with minimal
    support, replacing frequency/Jaccard aggregation in
    learning_detector.consolidate_insights (integration point noted in
    AUDIT §4).
  - Relational shift: an attribute→relational trajectory measure — the
    audit found /evo tracks judgement on a false-flag axis, never
    attribute→relational.

Pure, deterministic, dependency-free.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Iterable, Optional

from .sme import (
    Alignment,
    PredicateKind,
    Structure,
    gmap,
    structural_consistency,
)

__all__ = [
    "AlignableDifference",
    "alignable_differences",
    "progressive_alignment",
    "relational_shift",
    "align",
]


@dataclass(frozen=True)
class AlignableDifference:
    """One difference between two ALIGNED structures (never raw diff noise)."""
    kind: str            # base_only | target_only | role_flip | tier_flip
    predicate: str
    base_args: tuple[str, ...] = ()
    target_args: tuple[str, ...] = ()
    detail: str = ""


def align(base: Structure, target: Structure) -> Alignment:
    a = gmap(base, target)
    if not structural_consistency(base, target, a):
        # never reason over an inconsistent mapping: fall back to entity-only
        ents = {m.base_item: m.target_item for m in gmap(base, target).matched
                if m.kind == "entity"}
        return Alignment(entity_map=ents, predicate_map={})
    return a


def _base_side_differences(base: Structure, target: Structure,
                           mapping: Alignment) -> list[AlignableDifference]:
    """base_only / tier_flip / role_flip over the base predicates whose
    arguments are all mapped. Base arguments are carried into target space
    through the entity map; target arguments are already in target space and
    are read as they are — pushing them through the base→target map a second
    time corrupted every comparison over shared vocabulary."""
    target_preds = {p.name: p for p in target.predicates}
    out: list[AlignableDifference] = []
    for bp in base.predicates:
        mapped_args = tuple(mapping.entity_map.get(e, "?") for e in bp.args)
        if "?" in mapped_args:
            continue  # not alignable: unmapped args
        tp = target_preds.get(bp.name)
        if tp is None:
            out.append(AlignableDifference("base_only", bp.name, mapped_args))
        elif tp.kind != bp.kind:
            out.append(AlignableDifference("tier_flip", bp.name, mapped_args, tp.args,
                                           f"kind {bp.kind} -> {tp.kind}"))
        elif tp.args != mapped_args:
            out.append(AlignableDifference("role_flip", bp.name, mapped_args, tp.args,
                                           "argument roles differ"))
    return out


def _target_only_differences(base: Structure, target: Structure,
                             mapping: Alignment) -> list[AlignableDifference]:
    """Predicates only the target states, over entities that are images of
    mapped base entities; anything else is not alignable."""
    base_names = {p.name for p in base.predicates}
    images = set(mapping.entity_map.values())
    return [
        AlignableDifference("target_only", tp.name, (), tp.args)
        for tp in target.predicates
        if tp.name not in base_names and all(e in images for e in tp.args)
    ]


def alignable_differences(base: Structure, target: Structure,
                          a: Optional[Alignment] = None) -> list[AlignableDifference]:
    """Differences between two aligned structures, expressed over the mapping.

    Comparison pairs predicates by NAME identity (tier-identical) on both
    sides, then expresses every difference over the entity mapping:

      base_only   — predicate exists in base, not in target
      target_only — predicate exists in target, not in base
      role_flip   — same predicate name, same kind, different argument roles
      tier_flip   — same predicate name, different kind (attr vs relation)

    Only predicates whose argument entities are mapped are compared — a
    difference on unmapped structure is not alignable, by definition.
    (Deliberately looser than SME consistency: role-flipped bindings are
    INCONSISTENT for gmap, but exactly the difference a learning loop
    must compute — the audit's alignable-differences gap.)
    """
    mapping = a if a is not None else align(base, target)
    return (_base_side_differences(base, target, mapping)
            + _target_only_differences(base, target, mapping))


def progressive_alignment(
    structures: Iterable[Structure],
    min_support: int = 3,
) -> list[tuple[tuple[str, ...], int]]:
    """Progressive alignment: pairwise structural pairing + abstraction.

    For each pair of structures, compute the aligned predicate pairs; keep
    the pair only when the alignment is structurally consistent. A shared
    relational pattern survives when at least min_support structures
    exhibit it — replacing frequency/Jaccard aggregation with structural
    evidence (AUDIT §4 integration: consolidate_insights).
    """
    items = list(structures)
    support: dict[tuple[str, ...], set[int]] = {}
    for i, j in itertools.combinations(range(len(items)), 2):
        a = align(items[i], items[j])
        pattern = tuple(sorted(a.predicate_map.items()))
        if pattern:
            support.setdefault(pattern, set()).update((i, j))
    out = []
    for pattern, idxs in support.items():
        if len(idxs) >= min_support:
            out.append((pattern, len(idxs)))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def relational_shift(structures: Iterable[Structure]) -> float:
    """Attribute→relational trajectory: share of the corpus's predicate
    weight carried by relations and higher-order relations.

    attribute weight 1.0 each; relation weight = arity (>= 2);
    higher-order weight = 2 + arity. Returns [0, 1]: > 0.5 means the
    corpus has shifted relational."""
    attribute_w = 0.0
    relational_w = 0.0
    for p in itertools.chain.from_iterable(s.predicates for s in structures):
        if p.kind == PredicateKind.ATTRIBUTE:
            attribute_w += 1.0
        elif p.kind == PredicateKind.RELATION:
            relational_w += float(max(p.arity, 2))
        else:
            relational_w += 2.0 + float(len(p.args))
    total = attribute_w + relational_w
    if total == 0:
        return 0.0
    return round(relational_w / total, 6)
