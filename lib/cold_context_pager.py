#!/usr/bin/env python3
"""Cold-tier demand-paging for GRIP context — the MECHANISM half (Deliverable B).

Deliverable A (PR #2 on prompt-cache-keepalive) shipped the ColdStore primitive +
the Claude Code plugin. This is the GRIP/HAL *core substrate* half: a thin pager
that OFFLOADS a cold (idle / about-to-be-compacted) context slice to the merged
FileRingStore — a zero-token, lossless local ring — and PAGES IT BACK on demand,
instead of re-deriving it. It is the HOW; #3372's lib/auto_compact_policy.py is
the WHEN (the policy decides when to compact/offload; this mechanism executes it).

Three gates, all correct-by-construction:

  1. ECONOMICS — offload only when cold_net_vs_rederivation(rederivation_tokens)
     is net-positive. Page-back bills as a cache write (~1.25x), so a slice cheap
     to re-derive stays HOT and is never offloaded (econ net <= 0 -> no-op). COLD
     is *avoided re-derivation*, not free tokens.

  2. SCOPE (PARAMOUNT security) — the injected FileRingStore carries a
     deny_unknown ColdScopePolicy: an unsafe scope (client / financial / product)
     raises PermissionError "denied COLD persistence (fail-closed)", so
     client/financial context can never reach the on-disk ring
     (rules/no-sensitive-info-in-grip.md). The pager surfaces that raise; it
     never swallows it.

  3. LOSSLESS — page_back returns the exact stored text (verify-canonical:
     round-trip equality, not a status flag).

DIP: FileRingStore + ColdTierEconomics are injected, so tests use a tmp-dir store
and never touch real disk paths or real client data.

See plans/deliverable-b-cold-tier-demand-paging.md for the build/test plan and the
#3372 composition step (next wave). Cross-ref H799 (cache economics confirmed).
"""
from __future__ import annotations

from typing import Optional

# The cold-tier primitives ship in prompt-cache-keepalive (Deliverable A, PR #2).
# Import is OPTIONAL + non-fatal: this module loads in ANY environment, so GRIP
# never hard-depends on the cold tier (avoids forcing a git-dep into the
# auto-generated requirements.txt). Only CONSTRUCTING a pager needs a real
# FileRingStore — and the caller injects it, so the caller already has the package.
# With `from __future__ import annotations` the type hints below are strings and
# never evaluate these names at import time.
try:
    from prompt_cache_keepalive.coldstore import ColdRef, FileRingStore
    from prompt_cache_keepalive.cold_economics import ColdTierEconomics
except ModuleNotFoundError:  # pragma: no cover - cold tier is an optional substrate
    ColdRef = FileRingStore = ColdTierEconomics = None  # type: ignore[assignment,misc]

# GRIP's default offload scope — a SAFE scope under deny_unknown's default set.
# Constructing a pager with any other (unsafe) scope makes the store's policy
# reject the put with PermissionError; that rejection is the security property,
# not a defect.
DEFAULT_OFFLOAD_SCOPE = "session"


class ColdContextPager:
    """Offload a cold context slice to the ring and page it back losslessly."""

    def __init__(
        self,
        store: FileRingStore,
        econ: ColdTierEconomics,
        *,
        scope: str = DEFAULT_OFFLOAD_SCOPE,
    ) -> None:
        if FileRingStore is None:  # optional cold-tier substrate not installed
            raise RuntimeError(
                "cold tier unavailable — install prompt-cache-keepalive "
                "(see plans/deliverable-b-cold-tier-demand-paging.md)"
            )
        self._store = store
        self._econ = econ
        self._scope = scope

    def offload(
        self, key: str, content: str, *, rederivation_tokens: float
    ) -> Optional[ColdRef]:
        """Offload *content* under *key* iff it is economically net-positive.

        Contract (exact): returns the ColdRef when stored; None when re-deriving the
        slice is cheaper than the page-back write (stays HOT) OR a transient infra
        error (OSError: disk/I/O) means the offload was skipped (best-effort — the
        pager is stateless, nothing to roll back). A denied SCOPE PROPAGATES as
        PermissionError (a caller bug — offloading client/financial content;
        fail-closed). Any OTHER exception (a store fault or misuse) ALSO propagates
        by design — only infra (OSError) degrades, so real bugs are never masked.
        """
        if self._econ.cold_net_vs_rederivation(rederivation_tokens) <= 0:
            return None
        try:
            return self._store.put(key, content, scope=self._scope)
        except PermissionError:
            # LOAD-BEARING, not redundant: PermissionError IS a subclass of OSError,
            # so without this earlier branch the `except OSError` below would SWALLOW
            # the security denial. Surface it; never degrade a denied-scope offload.
            raise
        except OSError:
            return None  # best-effort: infra failure -> stay HOT this round

    def page_back(self, ref: ColdRef) -> str:
        """Restore the exact text previously offloaded (lossless)."""
        return self._store.get(ref, scope=self._scope)
