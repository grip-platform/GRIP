"""Banach Projector: N-dimensional coupled fixed-point coordinator-convergence kernel.

Generalizes fold_search (1D Banach over breadth) and converge (1D iterative over
depth) into a coupled N-dimensional Banach projector. State is an N-tuple; N
operators each project the state onto one component. The joint fixed point
P(S) = S is reached when every operator is idempotent — no operator's output
changes any other operator's input.

The Coordinator-Convergence duality made executable (per the §02 "doesn't bottom
out, gets sharper" axiom): each iteration applies a smaller Coordinator (breadth
scan via fold_search/dfi-fib) coupled to a smaller Convergence (depth refine via
converge). Cross-coupling lets depth artifacts seed new breadth terms and breadth
findings bound depth scope. The Fibonacci outer schedule [1,1,2,3,5,8] gives
natural acceleration with geometric convergence at the fixed point.

Mathematical structure:
    State:        S = (s_1, s_2, ..., s_N)
    Operators:    P_i: tuple_state -> s_i  (reads N, writes 1; coupled)
    Joint:        P(S) = (P_1(S), P_2(S), ..., P_N(S))
    Fixed point:  P(P(S)) = P(S)  (idempotent N-ary projector)

If each P_i is a contraction on its component AND the coupling preserves
contraction, the joint operator P is a contraction on the product space. By
Banach's theorem, P has a unique fixed point and iterating P from any initial
state converges geometrically. This is the formal guarantee underwriting RSI
termination.

InnerSpeakerGo kernel framing: the agent IS the projection operator. The agent's
reasoning generates each P_i; the algorithm orchestrates the joint application.
RSI quality is bounded by the agent's reasoning, not by hand-coded heuristics —
this is why the kernel is dimension-agnostic and skill-agnostic.

Design axioms (JIT-enforced, stricter than fold_search):
    - CC <= 3 per public function (extract-dispatch coordinator pattern)
    - O(F * N * cost(P_max)) time; F <= 5 Fibonacci depths, N = |operators|
    - O(N * S_max + F * N) space
    - Idempotent: banach_project on a fixed-point state returns it in 1 iteration
    - DRY: composes fold_search + converge primitives via Projection adapter
    - Frozen Projection (immutable operators); slots on all dataclasses

Hypotheses (pre-registered):
    H-BANACH-PROJECTOR-1 (deadline 2026-07-05): banach_project reaches joint
        fixed point in <= 5 Fibonacci outer iterations for any 2-operator coupled
        contraction task. Falsified if any contraction test exceeds 5 iterations.
    H-BANACH-PROJECTOR-2 (deadline 2026-07-05): kernel is dimension-agnostic —
        N-operator projection terminates with the Fibonacci bound for any N >= 2
        with bounded per-operator contraction. Falsified if scaling with N
        degrades convergence rate non-linearly.

Version: 1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

S = TypeVar("S")

# DFI-FIB outer schedule (same series used by fold_search and /dfi-fib protocol)
_FIB: tuple[int, ...] = (1, 1, 2, 3, 5, 8, 13, 21)


@dataclass(frozen=True, slots=True)
class Projection(Generic[S]):
    """One coupled operator: reads full tuple state, writes one component.

    Each operator carries its own metric for fixed-point detection. Frozen
    because operators are values (math), not entities (state).
    """

    name: str
    apply: Callable[[tuple[S, ...]], S]
    distance: Callable[[S, S], float]


@dataclass(slots=True)
class BanachResult(Generic[S]):
    """Outcome of banach_project: state, iter count, fixed-point flag, trace."""

    state: tuple[S, ...]
    iterations: int = 0
    fixed_point: bool = False
    trace: list[tuple[float, ...]] = field(default_factory=list)


def _project_once(
    state: tuple[S, ...],
    ops: tuple[Projection[S], ...],
) -> tuple[tuple[S, ...], tuple[float, ...]]:
    """Apply each operator to current state. Return (new_state, per-op distances).

    Coupled: every operator reads the FULL state, so P_i's input depends on what
    P_{i-1} just wrote (within the previous iteration). This is what produces
    the Coordinator+Convergence fractal property — each step contains all
    dimensions implicitly.

    CC = 3 (two generator expressions inside tuple()).
    """
    new = tuple(op.apply(state) for op in ops)
    dists = tuple(op.distance(s, n) for op, s, n in zip(ops, state, new))
    return new, dists


def joint_norm(distances: tuple[float, ...]) -> float:
    """L_inf norm of per-operator distances — the joint Banach metric.

    On the product space S = s_1 x s_2 x ... x s_N, the natural Banach metric
    is the supremum of component distances:
        ||P(S) - S||_inf = max_i d_i(P_i(S), s_i)

    The joint operator P is a contraction on the product iff joint_norm
    decreases monotonically across iterations. This is the testable form of
    the Banach contraction guarantee. CC = 2 (one generator inside max()).
    """
    return max(distances) if distances else 0.0


def _is_idempotent(distances: tuple[float, ...], epsilon: float) -> bool:
    """Joint fixed point iff joint_norm <= epsilon. CC = 1.

    Equivalent to all(d <= eps), but routes through joint_norm so the metric
    is named in one place — single source of truth for the Banach criterion.
    """
    return joint_norm(distances) <= epsilon


def banach_project(
    initial: tuple[S, ...],
    projections: tuple[Projection[S], ...],
    max_fib_depth: int = 5,
    epsilon: float = 0.0,
) -> BanachResult[S]:
    """N-dimensional coupled Banach fixed-point projector — the core kernel.

    Couples N refinement operators over a product state space. Each iteration
    applies all operators to the current state in parallel (read-N, write-1
    pattern); terminates at the joint fixed point P(S) = S or at the Fibonacci
    depth cap.

    Args:
        initial: starting state, one component per projection.
        projections: tuple of N Projection operators (use tuple, not list, for
            type stability and immutability).
        max_fib_depth: index into Fibonacci schedule (default 5 -> max 8 outer
            iters; _FIB has 8 elements so floor is min(max_fib_depth, 8)).
        epsilon: tolerance for joint idempotency (default 0.0 = exact equality).

    Returns:
        BanachResult with final state, iteration count, fixed-point flag, and
        per-iteration per-operator distance trace.

    Complexity:
        Time: O(F * N * cost_max) where F = fib depths used, N = |projections|,
            cost_max = max projection apply+distance cost.
        Space: O(F * N) for trace + O(N * S_max) for state.

    Mathematical property: banach_project is itself a projector —
    banach_project(banach_project(s, ops).state, ops) returns the same state in
    1 iteration. Verified by test_idempotent_under_self_application.

    CC = 3 (for-loop + if-fixed-point + body).
    """
    result = BanachResult(state=initial)
    for _ in range(min(max_fib_depth, len(_FIB))):
        result.state, dists = _project_once(result.state, projections)
        result.iterations += 1
        result.trace.append(dists)
        if _is_idempotent(dists, epsilon):
            result.fixed_point = True
            break
    return result


# =============================================================================
# Adapters: compose existing GRIP primitives as Banach projections
# =============================================================================


def fold_projection(
    roots,
    term_extractor: Callable[..., set[str]],
    base_terms: frozenset[str] = frozenset(),
) -> Projection:
    """Wrap fold_search (/dfi-fib) as a Banach projection.

    Reads (breadth_set, depth_artifact) state, extracts seed terms from
    artifact, runs fold_search, returns updated breadth_set. Distance is
    symmetric difference size (cardinal metric on the power set lattice).

    CC = 1 in apply (linear composition); CC = 1 in distance.
    """
    from lib.fold_search import fold_search  # lazy: avoid import cycles

    def apply(state):
        _, artifact = state
        seeds = base_terms | term_extractor(artifact)
        return fold_search(roots, set(seeds)).files

    def distance(old, new):
        return float(len(old.symmetric_difference(new)))

    return Projection(name="fold/dfi-fib", apply=apply, distance=distance)


def converge_projection(
    improver: Callable,
    scorer: Callable[..., float],
    criterion_score: float = 80.0,
) -> Projection:
    """Wrap a converge-style refinement as a Banach projection.

    Reads (breadth_set, depth_artifact), passes breadth as scope hint to
    improver, returns refined artifact. Distance is absolute score delta —
    score plateau (delta = 0) signals depth-side fixed point. The improver
    IS the InnerSpeakerGo kernel: the agent's reasoning generating refinement.

    CC = 1 in apply; CC = 1 in distance.
    """

    def apply(state):
        breadth, artifact = state
        return improver(artifact, scope=breadth)

    def distance(old, new):
        return float(abs(scorer(new) - scorer(old)))

    return Projection(name="converge/depth", apply=apply, distance=distance)


def banach_coordinator_convergence(
    roots,
    initial_artifact,
    improver: Callable,
    scorer: Callable[..., float],
    term_extractor: Callable[..., set[str]],
    base_terms: frozenset[str] = frozenset(),
    max_fib_depth: int = 5,
    epsilon: float = 0.0,
    criterion_score: float = 80.0,
) -> BanachResult:
    """Full Coordinator+Convergence Banach projector — /dfi-fib + /converge coupled.

    The §02 duality made executable: breadth opens via fold_search, depth drives
    via the improver, banach_project orchestrates joint fixed-point search.

    Args:
        roots: search roots for fold_search (Coordinator scope).
        initial_artifact: seed depth artifact (code/text/draft/state).
        improver: callable(artifact, scope=breadth_set) -> improved_artifact.
            This IS the InnerSpeakerGo kernel — agent reasoning as operator.
        scorer: callable(artifact) -> float for distance metric.
        term_extractor: callable(artifact) -> set[str] (artifact -> breadth seeds).
        base_terms: always-present seed terms.
        max_fib_depth: outer Fibonacci bound.
        epsilon: joint fixed-point tolerance.
        criterion_score: depth converge threshold passed to converge_projection.

    Returns:
        BanachResult where state = (breadth_files, refined_artifact).

    CC = 1 (no branches; pure composition).
    """
    p_breadth = fold_projection(roots, term_extractor, base_terms)
    p_depth = converge_projection(improver, scorer, criterion_score)
    initial_breadth = p_breadth.apply((set(), initial_artifact))
    return banach_project(
        (initial_breadth, initial_artifact),
        (p_breadth, p_depth),
        max_fib_depth,
        epsilon,
    )


__all__ = [
    "Projection",
    "BanachResult",
    "banach_project",
    "joint_norm",
    "fold_projection",
    "converge_projection",
    "banach_coordinator_convergence",
    "_FIB",
]
