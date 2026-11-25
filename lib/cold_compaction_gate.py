"""Live context-gate integration for the cold-compaction arm (Deliverable B — live-gate wiring).

Wires :func:`cold_compaction_arm.plan_compaction` into the AUTONOMOUS critical-threshold
path of the context gate (``lib/gates/context_orchestrator._observe_critical_threshold``).
That path already ALLOWS and trusts CC native autoCompact for autonomous loops (V>>'s
2026-05-12 mandate) and runs a defensive checkpoint first; this adds an OPT-IN,
FAIL-OPEN cold-slice offload as part of that checkpoint, so GRIP's reconstructible cold
state can page back token-cheap after compaction instead of being re-derived.

PARAMOUNT safety — this NEVER changes any gate decision:

  * **Autonomous-only.** It is invoked solely from the autonomous-observe path. The
    interactive HALT-and-ask branch (``_deny_halt``, Rule 8) is a *different* branch that
    never calls this module — interactive behaviour is byte-for-byte unchanged.
  * **Fail-open.** Every error is caught and reported, never raised. The gate emits its
    allow/observe response unconditionally, independent of the offload outcome — a broken
    cold tier can never block or alter the gate.
  * **Default-OFF.** :func:`cold_compaction_arm.plan_compaction` returns
    ``CHECKPOINT_THEN_COMPACT`` only when the operator opted in
    (``state/auto-compact-config.json`` ``enabled:true``) AND context is at/above the
    threshold (90%). Config absent/disabled ⇒ no offload. AND with no ``slice_provider``
    wired (the default) nothing is offloaded regardless of the decision.
  * **Reversible.** Deleting the config (or never wiring a provider) restores the exact
    prior behaviour.
  * **Security (PARAMOUNT).** Client / financial slices are denied to the on-disk ring by
    the pager's ``deny_unknown`` scope; a denied scope is reported (in the returned dict),
    never silently dropped, and never persisted (``rules/no-sensitive-info-in-grip.md``).

DIP: ``slice_provider``, ``pager``, and ``config_loader`` are injected; the cold tier is an
optional substrate (:func:`build_session_pager` returns ``None`` when prompt-cache-keepalive
is absent). The production ``slice_provider`` — *what* GRIP offloads — is a separate,
deliberately-deferred design decision; until one is wired this is a safe, live, no-op seam.
"""
from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

# Bare-name import (lib/ on sys.path — the established convention for these siblings),
# with a package-root fallback so this resolves under PYTHONPATH=$HOME/.claude too.
try:
    from auto_compact_policy import CHECKPOINT_THEN_COMPACT, consult
    from cold_compaction_arm import DEFAULT_MAX_SLICES, ColdSlice, offload_slices
except ModuleNotFoundError:  # pragma: no cover - package-root import path
    from lib.auto_compact_policy import CHECKPOINT_THEN_COMPACT, consult  # type: ignore
    from lib.cold_compaction_arm import (  # type: ignore
        DEFAULT_MAX_SLICES,
        ColdSlice,
        offload_slices,
    )

if TYPE_CHECKING:  # no runtime dependency on the optional cold tier
    from cold_context_pager import ColdContextPager

#: Default cached-prefix size (tokens) for the session pager's economics, used ONLY when a
#: real slice_provider is wired (the default path never constructs a pager). Matches the
#: cold-tier test fixture; a provider-wiring follow-up tunes it to the real prefix.
DEFAULT_PREFIX_TOKENS = 50_000


def build_session_pager(
    *,
    namespace: str = "session",
    prefix_tokens: float = DEFAULT_PREFIX_TOKENS,
    scope: str = "session",
    cold_dir: Optional[str] = None,
) -> Optional["ColdContextPager"]:
    """Construct a safe session-scoped :class:`ColdContextPager`, or ``None`` if the cold
    tier (prompt-cache-keepalive) is not installed (optional substrate).

    Never raises — any construction failure returns ``None`` so the caller degrades to a
    no-op offload. The ``deny_unknown`` scope policy is the mechanical security backstop
    (client/financial content is refused to the ring).
    """
    try:
        from cold_context_pager import ColdContextPager
        from prompt_cache_keepalive.cold_economics import ColdTierEconomics
        from prompt_cache_keepalive.cold_scope import deny_unknown
        from prompt_cache_keepalive.coldstore import FileRingStore
    except (ImportError, ModuleNotFoundError):
        return None  # cold tier (prompt-cache-keepalive) not installed — optional substrate
    try:
        # FileRingStore expects a Path for cold_dir (it calls .mkdir); coerce a str caller
        # value. None ⇒ let FileRingStore choose its own default cold dir.
        cold_dir_path = Path(cold_dir) if cold_dir is not None else None
        store = FileRingStore(
            cold_dir=cold_dir_path, scope_policy=deny_unknown(), namespace=namespace
        )
        econ = ColdTierEconomics(prefix_tokens=prefix_tokens)
        return ColdContextPager(store, econ, scope=scope)
    except Exception:
        # never-raises contract (an OPTIONAL-substrate constructor): ANY construction failure
        # degrades to None so the caller becomes a no-op. The IMPORT block above stays narrow
        # ((ImportError, ModuleNotFoundError) = tier absent); construction is catch-all because
        # the documented guarantee here is "a pager or None, never an exception" (AGORA #3392 r3).
        return None


def offload_for_autonomous_compaction(
    context_pct: float,
    *,
    slice_provider: Optional[Callable[[], Sequence[ColdSlice]]] = None,
    pager: Optional["ColdContextPager"] = None,
    config_loader: Optional[Callable[[], dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Consult the auto-compact policy and (opt-in) offload cold slices before an
    autonomous compaction. FAIL-OPEN: never raises.

    Resolution:
      * ``decision = plan_compaction(context_pct, is_autonomous=True, …)`` — the unchanged
        policy verdict (the autonomous caller is, by construction, ``is_autonomous=True``).
      * Offload happens only when the decision is ``CHECKPOINT_THEN_COMPACT`` (opt-in +
        at/above threshold) AND both a ``slice_provider`` and a ``pager`` are wired. With
        the defaults (``slice_provider=None``, ``pager=None``) nothing is offloaded — a
        safe, live no-op seam.

    Returns a telemetry dict: ``{"decision": <str>, "offloaded": <int>}`` on the normal
    path, with an added ``"error": <str>`` (and ``decision="ERROR"``) if anything failed.
    The caller (the gate) treats this purely as observability — it NEVER gates the gate's
    allow/observe response on the result.

    Args:
        context_pct: Current context usage percentage (the gate's ``usage_percent``).
        slice_provider: Zero-arg callable returning the cold slices to offload, or ``None``
            (default ⇒ no slices ⇒ no offload).
        pager: An injected :class:`ColdContextPager`, or ``None`` (default ⇒ the arm
            returns an empty offload even on a compact decision — optional substrate).
        config_loader: Zero-arg policy-config loader (forwarded; defaults to the policy's
            own fail-closed loader).

    Returns:
        A telemetry dict (see above) — always; this function does not raise.
    """
    try:
        # Consult the policy FIRST (a cheap, fail-closed config read). The provider is
        # only invoked when an offload will ACTUALLY happen — never on the default /
        # disabled / below-threshold / no-substrate path (AGORA #3392: materialising the
        # slices unconditionally was a perf regression on the common no-op path).
        decision = consult(context_pct, is_autonomous=True, config_loader=config_loader)
        if decision != CHECKPOINT_THEN_COMPACT or pager is None or slice_provider is None:
            return {"decision": decision, "offloaded": 0}
        # Bound MATERIALISATION (not just the offload) to the cap so a misconfigured provider
        # returning a huge or lazily-unbounded sequence can't OOM the autonomous path: islice
        # pulls at most DEFAULT_MAX_SLICES from the provider; offload_slices re-applies the cap
        # as defense-in-depth. (The slices are GRIP-internal, not an untrusted boundary.)
        slices = list(islice(slice_provider(), DEFAULT_MAX_SLICES))
        offloaded = offload_slices(pager, slices, max_slices=DEFAULT_MAX_SLICES)
        return {"decision": decision, "offloaded": len(offloaded)}
    except PermissionError as exc:
        # SECURITY: a denied scope (client/financial content reached the ring). Surface it
        # in telemetry — never silently swallow — but stay fail-open for the gate.
        return {"decision": "DENIED", "offloaded": 0, "error": f"scope denied: {exc}"}
    except Exception as exc:  # fail-open: the gate must never break on offload
        return {"decision": "ERROR", "offloaded": 0, "error": str(exc)}


__all__ = [
    "DEFAULT_PREFIX_TOKENS",
    "build_session_pager",
    "offload_for_autonomous_compaction",
]
