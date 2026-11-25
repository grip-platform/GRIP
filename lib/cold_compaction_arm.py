"""Cold-compaction arm — compose the auto-compact POLICY (#3372, the WHEN) with the
ColdContextPager MECHANISM (#3386, the HOW). Deliverable B step 3.

The seam: when the auto-compact policy returns ``CHECKPOINT_THEN_COMPACT`` (opt-in,
autonomous, at/above threshold), the caller must checkpoint state LOSSLESSLY before
the harness compacts. This arm makes that checkpoint TOKEN-CHEAP by offloading the
cold (about-to-be-compacted) context slices to the ColdStore ring, so they can be
PAGED BACK after compaction instead of re-derived. The policy stays the WHEN; the
pager is the HOW; this module is only the wiring between them.

Correct-by-construction (each invariant is a test in
``tests/test_cold_compaction_arm.py``):

  * ADDITIVE — imports :func:`auto_compact_policy.consult` + the
    ``CHECKPOINT_THEN_COMPACT`` constant; edits NEITHER that pure decision module
    NOR :mod:`cold_context_pager`. The decision (HALT / CONTINUE /
    CHECKPOINT_THEN_COMPACT) is returned verbatim and is NEVER altered by the
    presence, absence, or outcome of an offload. The WHEN stays authoritative.

  * DEFAULT-OFF — an offload is attempted ONLY on a ``CHECKPOINT_THEN_COMPACT``
    decision, which is itself only reachable when an operator opted in (config
    ``enabled:true``) AND the session is autonomous AND context is at/above the
    threshold. ``HALT`` / ``CONTINUE`` ⇒ zero offload; the pager is never touched.

  * OPTIONAL-SUBSTRATE — no hard dependency on the cold tier. The pager is
    DIP-injected; with ``pager=None`` (the tier not installed / not wired) the arm
    returns the decision with an empty offload set. The auto-generated
    ``requirements.txt`` is untouched (the tier ships in prompt-cache-keepalive —
    see :mod:`cold_context_pager`). The pager type is referenced only under
    ``TYPE_CHECKING``, so importing this module never imports the cold tier.

  * FAIL-OPEN for compaction — compaction is the relief valve and MUST proceed. A
    per-slice infra failure surfaces from the pager as ``None`` (best-effort), which
    just leaves that slice HOT (re-derived post-compact); it NEVER blocks the plan.

  * SECURITY (PARAMOUNT) — a denied SCOPE PROPAGATES (``PermissionError``). The arm
    NEVER swallows a scope deny: persisting client / financial content to the on-disk
    ring is forbidden (``rules/no-sensitive-info-in-grip.md``), and a deny
    signals a misconfigured (non-safe-scoped) pager that must surface loudly rather
    than be silently skipped. In correct usage — a default ``"session"``-scoped
    pager — it never fires.

DIP: the pager and the policy config are injected, so tests need no real disk, no
real cold tier, and no real client data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

from auto_compact_policy import CHECKPOINT_THEN_COMPACT, consult

if TYPE_CHECKING:  # no runtime dependency on the optional cold tier
    from cold_context_pager import ColdContextPager
    from prompt_cache_keepalive.coldstore import ColdRef

#: Default upper bound on slices offloaded in one compaction pass. Bounds ring churn
#: and keeps the synchronous offload step from delaying the compaction relief valve if
#: a MISCONFIGURED caller passes an unbounded slice set (the slices are GRIP-internal,
#: not an untrusted-input boundary — defense-in-depth, not an adversarial gate). Any
#: overflow beyond the cap stays HOT (re-derived post-compact) — fail-open, never an
#: error. Pass ``max_slices=None`` to opt out of the cap explicitly.
DEFAULT_MAX_SLICES = 256


@dataclass(frozen=True)
class ColdSlice:
    """One cold context slice eligible for offload before compaction.

    ``rederivation_tokens`` is what it would COST to re-derive this slice after a
    compaction; the pager's economics gate uses it to decide whether offloading
    (which bills a ~1.25x cache write on page-back) is net-positive vs just letting
    the slice be re-derived.
    """

    key: str
    content: str
    rederivation_tokens: float


@dataclass(frozen=True)
class CompactionPlan:
    """The composed decision + what (if anything) was offloaded.

    ``decision`` is the UNCHANGED :mod:`auto_compact_policy` verdict (a member of its
    ``DECISIONS`` set). ``offloaded`` maps slice-key → ColdRef for every slice that
    actually reached the cold ring — empty unless the decision was
    ``CHECKPOINT_THEN_COMPACT``, a pager was injected, and the slice was both
    net-positive and successfully stored.
    """

    decision: str
    offloaded: dict[str, "ColdRef"] = field(default_factory=dict)

    @property
    def should_compact(self) -> bool:
        """True iff the harness should checkpoint + compact now."""
        return self.decision == CHECKPOINT_THEN_COMPACT


def offload_slices(
    pager: "ColdContextPager",
    slices: Sequence[ColdSlice],
    *,
    max_slices: Optional[int] = DEFAULT_MAX_SLICES,
) -> dict[str, "ColdRef"]:
    """Best-effort offload of up to ``max_slices`` slices; returns ``{key: ColdRef}``
    for those actually stored.

    Per the pager contract (:meth:`cold_context_pager.ColdContextPager.offload`):
    ``None`` ⇒ skipped (economics HOT, or an OSError infra degrade) — the slice stays
    HOT this round. A stored slice ⇒ its ``ColdRef``. ``PermissionError`` (a denied
    scope) PROPAGATES — the arm never swallows a security deny.

    ``max_slices`` bounds the loop (default :data:`DEFAULT_MAX_SLICES`): at most that
    many slices (the first, in caller order) are offloaded; any beyond stay HOT
    (re-derived post-compact) — fail-open, never an error. This caps ring churn and
    keeps a misconfigured caller's oversized slice set from delaying the compaction
    relief valve. ``max_slices=None`` disables the cap.

    Later slices use the SAME key only if the caller supplied duplicates; a duplicate
    key overwrites the earlier ref in the returned map (last-write-wins), which is
    harmless because both refs page back to their own stored content.
    """
    if max_slices is not None and max_slices < 0:
        # #3391: a negative cap previously tripped the n >= max_slices break at
        # n=0 — silently skipping EVERY slice. Loud beats surprising.
        raise ValueError(f"max_slices must be >= 0 or None, got {max_slices}")
    offloaded: dict[str, "ColdRef"] = {}
    for n, s in enumerate(slices):
        if max_slices is not None and n >= max_slices:
            break  # cap reached — remaining slices stay HOT (fail-open, no error)
        # No per-slice try/except BY DESIGN: the pager already degrades a transient
        # OSError to None internally (that slice stays HOT, the loop continues), a
        # denied scope MUST abort the batch (PermissionError — security: never offload
        # more after a scope violation), and any other exception is a real bug that
        # must surface. Wrapping this call would mask one of those three.
        ref = pager.offload(s.key, s.content, rederivation_tokens=s.rederivation_tokens)
        if ref is not None:
            offloaded[s.key] = ref
    return offloaded


def plan_compaction(
    context_pct: float,
    *,
    is_autonomous: bool,
    pager: Optional["ColdContextPager"] = None,
    slices: Sequence[ColdSlice] = (),
    config: dict[str, Any] | None = None,
    config_loader: Callable[[], dict[str, Any]] | None = None,
    max_slices: Optional[int] = DEFAULT_MAX_SLICES,
) -> CompactionPlan:
    """Compose the WHEN (:func:`auto_compact_policy.consult`) with the HOW (offload).

    Resolution (first match wins):

      * ``decision = consult(...)`` — the unchanged, authoritative policy verdict.
      * ``decision != CHECKPOINT_THEN_COMPACT``        → ``CompactionPlan(decision, {})``
        (no offload on HALT / CONTINUE; the pager is never touched).
      * ``decision == CHECKPOINT_THEN_COMPACT`` and ``pager is None``
        → ``CompactionPlan(decision, {})`` (decision unchanged; the cold tier is simply
        not available / not injected — optional substrate. The caller checkpoints the
        existing way).
      * ``decision == CHECKPOINT_THEN_COMPACT`` and ``pager`` present
        → offload the slices (best-effort) and return their refs for post-compaction
        page-back.

    The decision is NEVER altered by the presence / absence / outcome of an offload —
    the WHEN is authoritative; offload is a pure additive optimisation of the
    checkpoint step. A denied scope still PROPAGATES (security; see
    :func:`offload_slices`).

    Args:
        context_pct: Current context usage percentage (forwarded to ``consult``).
        is_autonomous: Whether an autonomous loop is the actor (forwarded).
        pager: An injected :class:`cold_context_pager.ColdContextPager`, or ``None``
            when the cold tier is unavailable / unwired (optional substrate).
        slices: The cold context slices eligible for offload (default: none).
        config: Pre-loaded policy config dict (forwarded to ``consult``). Mutually
            exclusive with ``config_loader`` (see Raises).
        config_loader: Zero-arg config loader (forwarded to ``consult``). Mutually
            exclusive with ``config``.
        max_slices: Upper bound on slices offloaded this pass (forwarded to
            :func:`offload_slices`; default :data:`DEFAULT_MAX_SLICES`, ``None`` = no cap).

    Returns:
        A :class:`CompactionPlan` carrying the verbatim decision and the offload map.

    Raises:
        ValueError: if BOTH ``config`` and ``config_loader`` are provided. ``consult``
            itself tolerates both (``config`` wins), but the composition layer rejects
            the ambiguous call so a caller who passed a loader expecting it to be used
            is not silently overridden. Passing neither is fine (the default loader is
            used).
    """
    if config is not None and config_loader is not None:
        raise ValueError(
            "provide at most one of `config` / `config_loader` — passing both is "
            "ambiguous (consult would silently prefer `config`); pass neither to use "
            "the default loader"
        )
    decision = consult(
        context_pct,
        is_autonomous=is_autonomous,
        config=config,
        config_loader=config_loader,
    )
    if decision != CHECKPOINT_THEN_COMPACT or pager is None:
        return CompactionPlan(decision=decision, offloaded={})
    return CompactionPlan(
        decision=decision,
        offloaded=offload_slices(pager, slices, max_slices=max_slices),
    )


__all__ = [
    "DEFAULT_MAX_SLICES",
    "ColdSlice",
    "CompactionPlan",
    "offload_slices",
    "plan_compaction",
]
