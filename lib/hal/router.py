"""HAL Router — routing strategies for multi-provider LLM dispatch.

Three strategies:
- Fixed: User selects model explicitly (provider/model addressing)
- Fallback: Ordered chain, try next on failure/rate-limit
- Quality-Cost: Triage simple->cheap, complex->premium

Usage:
    from lib.hal.router import Router

    router = Router(registry)
    response = router.route("Explain SOLID", strategy="fallback")
"""
from typing import Optional

from lib.hal.providers.openai_compat import OpenAICompatError
from lib.hal.types import ChatResponse, RoutingStrategy


def _mark_active(address: str) -> None:
    """Write the active-LLM marker that the GRIP statusline reads.

    Best-effort: missing `lib.active_llm` (e.g. HAL running fully standalone
    outside GRIP) MUST NOT break the route. Any exception is swallowed.
    """
    try:
        from lib.active_llm import set_active
        set_active(address)
    except (Exception, SystemExit):
        # set_active() raises SystemExit on an address absent from the /llm
        # catalogue (a deliberate CLI hard-error, #2932). On the routing hot
        # path the marker is telemetry: it MUST NOT abort the route — least of
        # all for an uncatalogued fallback model. SystemExit subclasses
        # BaseException, so the prior bare `except Exception` leaked it and
        # crashed routing instead of degrading the marker gracefully.
        pass


class Router:
    """Routes queries across providers using configurable strategies."""

    def __init__(self, registry: "ProviderRegistry") -> None:
        self._registry = registry

    def route(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        strategy: Optional[RoutingStrategy] = None,
        **kwargs,
    ) -> ChatResponse:
        """Route a query using the specified or default strategy."""
        routing = self._registry.routing
        strat = strategy or routing.default_strategy

        dispatch = {
            RoutingStrategy.FIXED: self._route_fixed,
            RoutingStrategy.FALLBACK: self._route_fallback,
            RoutingStrategy.QUALITY_COST: self._route_quality_cost,
        }
        handler = dispatch.get(strat)
        if handler is None:
            raise NotImplementedError(f"Unsupported routing strategy: '{strat}'")
        return handler(prompt, model=model, **kwargs)

    def _chat(self, address: str, prompt: str, **kwargs) -> ChatResponse:
        """Single chokepoint for provider dispatch — marks active LLM, then calls registry."""
        _mark_active(address)
        return self._registry.chat(address, prompt, **kwargs)

    def _route_fixed(self, prompt: str, *, model: Optional[str] = None, **kwargs) -> ChatResponse:
        """Fixed routing — direct dispatch to specified model."""
        address = model or self._registry.routing.default_model
        if not address:
            raise RuntimeError("No model specified and no default configured")
        return self._chat(address, prompt, **kwargs)

    def _route_fallback(self, prompt: str, *, model: Optional[str] = None, **kwargs) -> ChatResponse:
        """Fallback routing — try chain in order, skip failures."""
        chain = self._registry.routing.fallback_chain
        if not chain:
            return self._route_fixed(prompt, model=model, **kwargs)

        errors = []
        for address in chain:
            try:
                return self._chat(address, prompt, **kwargs)
            except (OpenAICompatError, KeyError) as e:
                errors.append(f"{address}: {e}")
                continue

        raise RuntimeError(
            f"All providers in fallback chain failed: {'; '.join(errors)}"
        )

    def _route_quality_cost(
        self, prompt: str, *, model: Optional[str] = None, **kwargs,
    ) -> ChatResponse:
        """Quality-cost routing — PRECOG IDR override OR triage by complexity.

        W4 wiring: when ``is_precog_enabled()`` returns True AND a current
        precog-decision IDR exists, the IDR's ``decision.provider_addresses[0]``
        wins over the ``_classify_complexity`` heuristic. Falls back to the
        heuristic on any miss (IDR absent, no providers, dispatch error) —
        fail-open is the safety invariant.
        """
        precog_address = _resolve_precog_address()
        if precog_address:
            try:
                return self._chat(precog_address, prompt, **kwargs)
            except OpenAICompatError:
                # Fail-open: drop back to heuristic-tier dispatch
                pass

        tiers = self._registry.routing.quality_cost_tiers
        if not tiers:
            return self._route_fixed(prompt, model=model, **kwargs)

        tier = _classify_complexity(prompt)
        address = tiers.get(tier, tiers.get("standard", ""))
        if not address:
            return self._route_fixed(prompt, model=model, **kwargs)

        try:
            return self._chat(address, prompt, **kwargs)
        except OpenAICompatError:
            # Fall back to standard tier on failure
            fallback = tiers.get("standard", "")
            if fallback and fallback != address:
                return self._chat(fallback, prompt, **kwargs)
            raise


def _resolve_precog_address() -> Optional[str]:
    """Return the first provider address from the current precog IDR, or None.

    Returns None if:
      - PRECOG is disabled (env var / no fresh flag window)
      - No current-IDR pointer file exists
      - The pointer file is corrupt / has no decision.provider_addresses
      - ``lib.precog.state`` is unavailable (HAL running standalone)

    Never raises — the catalogue check is advisory, not load-bearing.
    """
    try:
        from lib.precog.state import is_precog_enabled, read_current_idr
    except Exception:
        return None
    if not is_precog_enabled():
        return None
    idr = read_current_idr()
    if not isinstance(idr, dict):
        return None
    decision = idr.get("decision")
    if not isinstance(decision, dict):
        return None
    addresses = decision.get("provider_addresses")
    if not isinstance(addresses, list) or not addresses:
        return None
    first = addresses[0]
    if not isinstance(first, str) or not first:
        return None
    return first


def _classify_complexity(prompt: str) -> str:
    """Simple heuristic complexity classification.

    Returns: "simple", "standard", or "complex"
    """
    length = len(prompt)
    word_count = len(prompt.split())

    complexity_signals = sum([
        word_count > 200,
        length > 2000,
        "explain" in prompt.lower() and "step" in prompt.lower(),
        "analyse" in prompt.lower() or "analyze" in prompt.lower(),
        "compare" in prompt.lower() and "contrast" in prompt.lower(),
        prompt.count("```") >= 2,
        "refactor" in prompt.lower(),
        "architecture" in prompt.lower(),
    ])

    if complexity_signals >= 3:
        return "complex"
    if complexity_signals >= 1 or word_count > 50:
        return "standard"
    return "simple"
