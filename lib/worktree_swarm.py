"""Worktree-Isolated Parallel Agent Swarm coordinator (skeleton).

Origin: 2026-05-24, GRIP #3023 Wave 2 of plan ``plans/tender-popping-catmull.md``.
ADR 0008 (``docs/adr/0008-ambitious-workflows-federation.md``) ratifies this
design.

This module ships the **schema and validators** that govern parallel agent
fan-out across worktrees: the manifest dataclasses, disjoint-ownership
validation, and mechanical shape selection. The actual fan-out dispatch is
deliberately NOT in this module — it composes ``bg_dispatch`` +
``grip-worktree.sh`` at the call site (ADR 0008 C5), driven by the validated
manifest these helpers produce.

Design (LOCKED by ADR 0008)
---------------------------

* The manifest is the explicit contract — no implicit fan-out from a
  function argument list. Operators or upstream skills construct one, hand
  it to ``validate_disjoint_ownership`` + ``select_shape``, then either
  dispatch (Shape A) or fall back (B/C).
* Disjoint-ownership is enforced at manifest-validation time, before any
  worktree creation, so a C4 violation cannot reach an Agent dispatch.
* Shape selection (``select_shape``) is mechanical — uses the
  dispatch-economics 84 000-token floor and ≥3× ratio test. No LLM, no
  judgement call. Operator overrides go through a manifest amendment, not
  a ``select_shape`` override.
* Every worktree creation MUST go via ``grip-worktree.sh`` (C1: git-crypt
  blocks native ``isolation: "worktree"``).
* C5: this module does NOT reimplement ``bg_dispatch``, ``grip-worktree.sh``,
  or the hypothesis engine — it composes them.

Hypotheses (registered separately via ``lib/hypothesis_engine.py``)
-------------------------------------------------------------------

* **H-SWARM-MANIFEST-1**: ``validate_disjoint_ownership`` catches every C4
  violation across ≥3 future sprint manifests; falsified if one slips past.
* **H-SWARM-DISPATCH-ECON-1**: ``select_shape`` matches the operator's manual
  shape choice on ≥70 % of ≥10 future dispatches; falsified if override
  rate > 30 %.

See ADR 0008 ``Falsification`` section for the module-level falsifiers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal

__all__ = (
    "DISPATCH_FLOOR_TOKENS",
    "SHAPE_A_RATIO",
    "SHAPE_A_THRESHOLD_TOKENS",
    "SHARED_STATE_PATH_PREFIXES",
    "Shape",
    "WorktreeSwarmError",
    "WorkstreamAtom",
    "WorkstreamManifest",
    "validate_disjoint_ownership",
    "select_shape",
    # Helpers exposed for tests + downstream composability — keep stable.
    "touches_shared_state",
)

# Dispatch-economics canonical floor (``rules/dispatch-economics.md``,
# ~84 000 tokens of ambient context per spawned subagent). Fan-out is
# economical only when ``useful_work >= 3 × floor`` — anything less goes
# Shape B chain or Shape C main.
DISPATCH_FLOOR_TOKENS: int = 84_000

# Ratio of useful-work to ambient-floor that admits Shape A fan-out.
# Below this, the cost of cold-booting an agent exceeds the work it does.
SHAPE_A_RATIO: int = 3

# Threshold above which an atom's useful_work justifies Shape A fan-out.
# Derived from the constants above so the documented "~252 000 tokens"
# value in ADR 0008 cannot drift from the code. ``select_shape`` uses this
# as its default; callers may override via the ``floor`` / ``ratio`` kwargs
# (override paths still recompute to honour the new values).
SHAPE_A_THRESHOLD_TOKENS: int = DISPATCH_FLOOR_TOKENS * SHAPE_A_RATIO

# Path classes that gate Shape selection regardless of economics — any
# write to one of these substrate paths MUST go through a single Shape B
# coordinator (C4: shared-mutable-state). Match is exact-or-prefix-with-slash
# so ``learning/insights.jsonl`` matches but ``learning/insights.jsonl.bak``
# does not (different file).
SHARED_STATE_PATH_PREFIXES: tuple[str, ...] = (
    "learning/insights.jsonl",
    "learning/converge-metrics.jsonl",
    "cache/genome.json",
    "data/genome-shared.json",
    "embeddings.db",
    "rules/HOLISTIC.md",
)

Shape = Literal["A", "B", "C"]


class WorktreeSwarmError(ValueError):
    """Raised by manifest validation when a swarm contract is unsatisfiable.

    Specifically:
    * Empty / missing required fields on an atom or manifest.
    * Duplicate atom ids within one manifest.
    * Two atoms claiming the same path in ``owns_paths`` (C4 violation).

    Callers branch on this exception class — do NOT raise a different
    subclass without updating ``rules/agent-dispatch-protocol.md`` callers.
    """


@dataclass(frozen=True)
class WorkstreamAtom:
    """One atomic deliverable in a swarm manifest.

    ``owns_paths`` is the **writes-only** set. Reads are unrestricted — the
    substrate-isolation rule (C4) is about writes. A second atom may read
    files owned by the first; it just may not write them.
    """

    id: str
    goal: str
    owns_paths: tuple[str, ...]
    hypothesis_id: str | None = None
    useful_work_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise WorktreeSwarmError("atom.id must be non-empty")
        if not self.goal:
            raise WorktreeSwarmError(f"atom {self.id!r}: goal must be non-empty")
        if self.useful_work_tokens < 0:
            raise WorktreeSwarmError(
                f"atom {self.id!r}: useful_work_tokens must be >= 0, "
                f"got {self.useful_work_tokens}"
            )


@dataclass(frozen=True)
class WorkstreamManifest:
    """Top-level swarm contract. Immutable; validate before dispatch."""

    sprint_id: str
    atoms: tuple[WorkstreamAtom, ...]

    def __post_init__(self) -> None:
        if not self.sprint_id:
            raise WorktreeSwarmError("manifest.sprint_id must be non-empty")
        if not self.atoms:
            raise WorktreeSwarmError(
                f"manifest {self.sprint_id!r}: atoms must be non-empty"
            )
        seen_ids: set[str] = set()
        for atom in self.atoms:
            if atom.id in seen_ids:
                raise WorktreeSwarmError(
                    f"manifest {self.sprint_id!r}: duplicate atom id {atom.id!r}"
                )
            seen_ids.add(atom.id)


def _iter_owned_paths(
    manifest: WorkstreamManifest,
) -> Iterator[tuple[str, str]]:
    """Yield ``(atom_id, path)`` for every owned path across all atoms.

    Flattens the (atoms x paths) structure into a single sequence so
    consumers iterate once — O(total_paths), no Cartesian product.
    """
    for atom in manifest.atoms:
        yield from ((atom.id, path) for path in atom.owns_paths)


def validate_disjoint_ownership(manifest: WorkstreamManifest) -> None:
    """Raise ``WorktreeSwarmError`` if any path is owned by >=2 atoms (C4).

    Run this BEFORE any worktree creation or Agent dispatch. A manifest that
    passes this check guarantees no two atoms will write the same path; a
    manifest that fails it cannot be dispatched.

    The error message lists every collision (path + both atom ids) so a
    triage operator can identify all conflicts in one read.

    Single-loop, hash-keyed; O(total_paths). Pairs are flattened upstream
    by ``_iter_owned_paths``.
    """
    owner: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []
    for atom_id, path in _iter_owned_paths(manifest):
        existing = owner.get(path)
        if existing is not None and existing != atom_id:
            collisions.append((path, existing, atom_id))
        else:
            owner[path] = atom_id
    if collisions:
        lines = [
            f"{path!r}: owned by both {first!r} and {second!r}"
            for path, first, second in collisions
        ]
        raise WorktreeSwarmError(
            f"manifest {manifest.sprint_id!r}: ownership collisions:\n  "
            + "\n  ".join(lines)
        )


def _path_matches_shared_substrate(path: str) -> bool:
    """``True`` if ``path`` exactly equals a substrate prefix or sits under
    it via ``prefix/...``.

    Single-loop over the constant-size ``SHARED_STATE_PATH_PREFIXES`` tuple;
    O(len(prefixes)) per call (~6 comparisons).
    """
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in SHARED_STATE_PATH_PREFIXES
    )


def touches_shared_state(atom: WorkstreamAtom) -> bool:
    """``True`` if any of the atom's ``owns_paths`` matches a substrate prefix.

    Composes ``_path_matches_shared_substrate`` over the atom's paths —
    single-loop here, single-loop in the helper. No syntactic nesting.

    Public (in ``__all__``) so callers can use the same substrate check
    that drives ``select_shape``'s C4 enforcement, without relying on a
    private underscore-prefixed name.
    """
    return any(_path_matches_shared_substrate(p) for p in atom.owns_paths)


# Backward-compat alias kept for callsites that imported the private name
# before the public rename. Will be removed in a follow-up once no callers
# remain (search via ``rg _touches_shared_state``).
_touches_shared_state = touches_shared_state


def select_shape(
    manifest: WorkstreamManifest,
    *,
    floor: int = DISPATCH_FLOOR_TOKENS,
    ratio: int = SHAPE_A_RATIO,
) -> Shape:
    """Choose the dispatch shape per dispatch-economics + bg-dispatch rules.

    Returns:
        ``"A"`` — admit parallel fan-out (one ``Agent(run_in_background=True)``
        per atom). All atoms cleared the ratio test AND no atom touches
        shared substrate.

        ``"B"`` — sequential chain via a single coordinator (one Agent OR
        the main thread). Used when any atom touches shared substrate (C4)
        OR when atoms are mixed-cost (some below ratio, some above) OR
        when a sole atom clears the ratio (one agent is worth its floor).

        ``"C"`` — main-thread direct, no agent dispatch. Used when a sole
        atom is below the ratio (sub-floor work is cheaper on the main
        session where the ambient context is already loaded).

    The decision is mechanical — no LLM, no heuristic judgement.
    """
    if any(_touches_shared_state(atom) for atom in manifest.atoms):
        return "B"

    # When floor/ratio match the defaults, prefer the module constant so
    # the documented ADR 0008 value cannot drift; only recompute when the
    # caller overrides one of the kwargs.
    if floor == DISPATCH_FLOOR_TOKENS and ratio == SHAPE_A_RATIO:
        threshold = SHAPE_A_THRESHOLD_TOKENS
    else:
        threshold = floor * ratio

    # Sole-atom manifests cannot fan-out by definition.
    if len(manifest.atoms) == 1:
        sole = manifest.atoms[0]
        return "B" if sole.useful_work_tokens >= threshold else "C"

    # Multi-atom: Shape A only if EVERY atom clears the ratio test.
    if all(atom.useful_work_tokens >= threshold for atom in manifest.atoms):
        return "A"

    # Mixed: at least one atom too cheap for parallel — Shape B coordinator.
    return "B"


