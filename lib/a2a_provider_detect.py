"""A2A Provider Detection — identify which AI provider is calling GRIP."""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    """Detected provider information."""
    name: str
    model: Optional[str] = None
    version: Optional[str] = None


# Known provider patterns
_PROVIDER_PATTERNS = {
    "gemini": ["gemini", "google", "bard", "agy", "antigravity"],
    "codex": ["codex", "openai", "gpt"],
    "copilot": ["copilot", "github"],
    "claude": ["claude", "anthropic"],
}


def detect_provider(
    user_agent: str = "",
    metadata: Optional[dict] = None,
) -> ProviderInfo:
    """Detect the calling provider from request context.

    Detection order:
    1. Explicit metadata.provider field
    2. User-Agent header matching
    3. Fallback to "generic"
    """
    metadata = metadata or {}

    # 1. Explicit provider in metadata
    if "provider" in metadata:
        name = metadata["provider"].lower()
        if name not in _PROVIDER_PATTERNS:
            logger.warning("Unknown explicit provider: %s", name)
        return ProviderInfo(
            name=name,
            model=metadata.get("model"),
            version=metadata.get("version"),
        )

    # 2. User-Agent matching
    # First-match wins — order matters for ambiguous User-Agent strings
    ua_lower = user_agent.lower()
    for provider, patterns in _PROVIDER_PATTERNS.items():
        if any(p in ua_lower for p in patterns):
            return ProviderInfo(name=provider)

    # 3. Fallback
    return ProviderInfo(name="generic")
