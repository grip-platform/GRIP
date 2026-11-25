"""HAL — Harness Abstraction Layer.

The middle layer between GRIP and any LLM runtime. Supports any model,
any provider, any combination — local or cloud, subscription or API key.

Usage:
    from lib.hal import get_registry

    registry = get_registry()
    response = registry.chat("groq/llama-3.3-70b", "Explain SOLID principles")

    providers = registry.list_providers()
    models = registry.list_models("groq")
"""
from lib.hal.registry import ProviderRegistry, get_registry
from lib.hal.types import (
    ChatMessage,
    ChatResponse,
    CostSnapshot,
    ModelInfo,
    ProviderConfig,
    ProviderInfo,
    ProviderType,
    RoutingStrategy,
)

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "CostSnapshot",
    "ModelInfo",
    "ProviderConfig",
    "ProviderInfo",
    "ProviderRegistry",
    "ProviderType",
    "RoutingStrategy",
    "get_registry",
]
