"""HAL core types — data classes for the universal LLM abstraction.

All types are frozen dataclasses for immutability and hashability.
No external dependencies — stdlib only.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ProviderType(str, Enum):
    """Provider deployment category."""
    CLOUD = "cloud"
    ENTERPRISE = "enterprise"
    LOCAL = "local"
    GATEWAY = "gateway"


class RoutingStrategy(str, Enum):
    """How to select a provider/model for a query."""
    FIXED = "fixed"
    FALLBACK = "fallback"
    QUALITY_COST = "quality_cost"
    COUNCIL = "council"


class AuthMethod(str, Enum):
    """How to authenticate with a provider."""
    API_KEY = "api_key"
    KEYCHAIN = "keychain"
    AWS_IAM = "aws_iam"
    GCP_TOKEN = "gcp_token"
    AZURE_AD = "azure_ad"
    NONE = "none"


@dataclass(frozen=True)
class ChatMessage:
    """A single message in a conversation."""
    role: str
    content: str
    name: Optional[str] = None
    tool_calls: Optional[tuple] = None
    tool_call_id: Optional[str] = None


@dataclass(frozen=True)
class TokenUsage:
    """Token counts from an LLM response."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True)
class ChatResponse:
    """Normalised response from any LLM provider."""
    text: str
    model: str
    provider: str
    finish_reason: str = "stop"
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: Optional[float] = None
    latency_ms: Optional[float] = None
    raw: Optional[dict] = None


@dataclass(frozen=True)
class CostSnapshot:
    """Accumulated cost for a session or operation."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_usd: float = 0.0
    model: str = ""


@dataclass(frozen=True)
class ModelInfo:
    """Metadata about an available model."""
    id: str
    provider: str
    display_name: str = ""
    context_window: int = 0
    max_output_tokens: int = 0
    supports_tools: bool = False
    supports_json_mode: bool = False
    supports_streaming: bool = True
    supports_vision: bool = False
    cost_per_mtok_input: float = 0.0
    cost_per_mtok_output: float = 0.0


@dataclass(frozen=True)
class ProviderInfo:
    """Runtime info about a registered provider."""
    name: str
    type: ProviderType
    base_url: str
    healthy: bool = True
    models: tuple[str, ...] = ()
    latency_ms: Optional[float] = None


@dataclass
class ProviderConfig:
    """Configuration for a single provider from providers.yaml."""
    name: str
    type: ProviderType
    base_url: str
    auth_method: AuthMethod = AuthMethod.API_KEY
    api_key_source: str = ""
    models: list[str] = field(default_factory=list)
    auto_discover_models: bool = False
    auto_detect: bool = False
    default: bool = False
    enabled: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 120.0
    max_retries: int = 3


@dataclass
class RoutingConfig:
    """Routing strategy configuration."""
    default_strategy: RoutingStrategy = RoutingStrategy.FIXED
    default_model: str = ""
    fallback_chain: list[str] = field(default_factory=list)
    quality_cost_tiers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CouncilVote:
    """A single model's vote in a council decision."""
    model: str
    provider: str
    response: ChatResponse
    weight: float = 1.0


@dataclass(frozen=True)
class CouncilResult:
    """Result of a multi-model council query."""
    merged_text: str
    votes: tuple[CouncilVote, ...] = ()
    agreement_score: float = 0.0
    total_cost_usd: float = 0.0
