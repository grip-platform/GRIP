"""
MoE Provider Registry
=====================

Provider abstraction for multi-model PR review scoring.
Each provider implements a common interface: health check + score.

Providers:
    - llama-local: Local Llama 3.2 3B via llama-cpp-python
    - claude-haiku: Anthropic Haiku API
    - gemini-a2a: Google Gemini via A2A bridge (future)

Usage:
    from lib.moe.provider_registry import ProviderRegistry, Provider

    registry = ProviderRegistry()
    registry.register(Provider(
        name="llama-local",
        health_check=check_llama,
        score_fn=score_with_llama,
        weight=1.0,
    ))

    available = registry.available_providers()
    results = registry.score_all(diff, title, pr_number)
"""

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ScoreResult:
    """Standardised scoring result from any provider."""
    provider: str
    composite_score: int = 0
    dimension_scores: dict = field(default_factory=dict)
    passed: bool = False
    suggestions: list = field(default_factory=list)
    error: str = ""


@dataclass
class Provider:
    """A scoring provider with health check and score function.

    Attributes:
        name: Unique provider identifier
        health_check: Callable returning True if provider is available
        score_fn: Callable(diff, title, pr_number, verbose) -> ScoreResult
        weight: Provider weight in council voting (0.0-1.0)
        tier: Priority tier (lower = tried first in cascade)
    """
    name: str
    health_check: Callable[[], bool]
    score_fn: Callable[..., ScoreResult]
    weight: float = 1.0
    tier: int = 1


class ProviderRegistry:
    """Registry of scoring providers with health-aware routing."""

    def __init__(self):
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider):
        """Register a scoring provider."""
        self._providers[provider.name] = provider

    def unregister(self, name: str):
        """Remove a provider by name."""
        self._providers.pop(name, None)

    def get(self, name: str) -> Optional[Provider]:
        """Look up a provider by name."""
        return self._providers.get(name)

    @property
    def all_providers(self) -> list[Provider]:
        """All registered providers, sorted by tier."""
        return sorted(self._providers.values(), key=lambda p: p.tier)

    def available_providers(self) -> list[Provider]:
        """Return only healthy/available providers, sorted by tier."""
        available = []
        for provider in self.all_providers:
            try:
                if provider.health_check():
                    available.append(provider)
            except Exception:
                continue
        return available

    def score_all(self, diff: str, title: str, pr_number: str = "0",
                  verbose: bool = False) -> list[ScoreResult]:
        """Score with all available providers.

        Returns a list of ScoreResult from each provider that
        successfully completed scoring.
        """
        results = []
        for provider in self.available_providers():
            try:
                result = provider.score_fn(diff, title, pr_number, verbose)
                results.append(result)
            except Exception as e:
                results.append(ScoreResult(
                    provider=provider.name,
                    error=str(e),
                ))
        return results

    def score_first(self, diff: str, title: str, pr_number: str = "0",
                    verbose: bool = False) -> Optional[ScoreResult]:
        """Score with the first available provider (cascade).

        Returns the first successful ScoreResult, or None if all fail.
        """
        for provider in self.available_providers():
            try:
                result = provider.score_fn(diff, title, pr_number, verbose)
                if not result.error:
                    return result
            except Exception:
                continue
        return None

    @property
    def size(self) -> int:
        """Number of registered providers."""
        return len(self._providers)

    def __contains__(self, name: str) -> bool:
        return name in self._providers
