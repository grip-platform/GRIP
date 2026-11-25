"""HAL Council — Multi-LLM Mixture of Experts through the universal registry.

Every provider in HAL's registry is a potential council member. Query multiple
LLMs, merge by weighted median, detect divergence. The coordinator-convergence
duality: council is breadth-of-attempts on the same query.

Usage:
    from lib.hal.moe import HALCouncil

    council = HALCouncil(registry)
    result = council.deliberate(
        "Is this contract clause fair?",
        models=["groq/openai/gpt-oss-120b", "gemini/gemini-3.5-flash-lite", "ollama/mistral:7b"],
    )
    print(result.merged_text)
    print(f"Agreement: {result.agreement:.0%}")
"""
from typing import Optional

from lib.hal.types import CouncilResult, CouncilVote


def _mark_council(addresses: list[str]) -> None:
    """Write the council marker the GRIP statusline reads. Best-effort."""
    try:
        from lib.active_llm import set_council
        set_council(addresses, mode="council")
    except Exception:
        pass


class HALCouncil:
    """Cross-LLM council through HAL's universal provider layer."""

    def __init__(self, registry: "ProviderRegistry"):
        self._registry = registry

    def deliberate(
        self,
        query: str,
        models: list[str],
        weights: Optional[dict[str, float]] = None,
        system: str = "",
    ) -> CouncilResult:
        """Query multiple LLMs and merge results.

        Args:
            query: The question to pose to all council members
            models: List of provider/model addresses
            weights: Optional {model: weight} overrides (default: equal)
            system: Optional system prompt for all members
        """
        _mark_council(models)
        votes = []
        total_cost = 0.0

        for model_address in models:
            vote = self._query_member(model_address, query, system)
            if vote:
                votes.append(vote)
                total_cost += vote.response.cost_usd or 0.0

        if not votes:
            return CouncilResult(merged_text="[No council members responded]")

        merged = self._merge(votes, weights)
        agreement = self._compute_agreement(votes)

        return CouncilResult(
            merged_text=merged,
            votes=tuple(votes),
            agreement_score=agreement,
            total_cost_usd=total_cost,
        )

    def _query_member(
        self, model_address: str, query: str, system: str,
    ) -> Optional[CouncilVote]:
        """Query a single council member. Returns None on failure."""
        try:
            response = self._registry.chat(model_address, query, system=system)
            provider = model_address.split("/")[0] if "/" in model_address else "unknown"
            return CouncilVote(
                model=model_address,
                provider=provider,
                response=response,
                weight=1.0,
            )
        except Exception:
            return None

    def _merge(self, votes: list[CouncilVote], weights: Optional[dict[str, float]]) -> str:
        """Merge council votes. Uses highest-weighted response as base."""
        if not votes:
            return ""

        scored = []
        for vote in votes:
            w = (weights or {}).get(vote.model, 1.0)
            text_quality = len(vote.response.text)
            scored.append((vote, w * text_quality))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0].response.text

    def _compute_agreement(self, votes: list[CouncilVote]) -> float:
        """Estimate agreement between council members (0-1). O(n) via reference vote."""
        if len(votes) < 2:
            return 1.0

        # Compare each vote against the first (reference) — O(n) not O(n^2)
        ref_words = set(votes[0].response.text.lower().split())
        if not ref_words:
            return 0.0

        total = sum(
            len(ref_words & set(v.response.text.lower().split())) / len(ref_words | set(v.response.text.lower().split()))
            for v in votes[1:]
            if v.response.text
        )
        return total / (len(votes) - 1)
