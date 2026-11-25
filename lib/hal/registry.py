"""HAL Provider Registry — config-driven provider management.

Loads providers from config/providers.yaml, manages health state,
and provides model addressing via "provider/model-name" convention.

Usage:
    registry = get_registry()
    registry.load_config()         # Load from providers.yaml
    registry.discover_local()      # Probe local servers

    provider = registry.get("groq")
    response = provider.chat("Hello", model="openai/gpt-oss-120b")

    # Model addressing
    response = registry.chat("groq/openai/gpt-oss-120b", "Hello")
"""
import json
import logging
import os
import threading
import time
from typing import Optional

from lib.hal.providers.openai_compat import OpenAICompatProvider
from lib.hal.types import (
    AuthMethod,
    ModelInfo,
    ProviderConfig,
    ProviderInfo,
    ProviderType,
    RoutingConfig,
    RoutingStrategy,
)

from lib.hal.home import HAL_HOME as _HAL_HOME
_CONFIG_PATH = os.path.join(_HAL_HOME, "config", "providers.yaml")

logger = logging.getLogger(__name__)

# Singleton
_registry: Optional["ProviderRegistry"] = None
_registry_lock = threading.Lock()


def get_registry() -> "ProviderRegistry":
    """Get or create the singleton provider registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ProviderRegistry()
    return _registry


# Static floor of provider prefixes, used ONLY when no live registry is
# available (e.g. parsing before config load). The LIVE registry
# (config/providers.yaml) is the source of truth — see _resolve_known_providers.
# Correctness does NOT depend on this set while the registry is loaded
# (CBC: a registered provider is always routable, with no list to drift).
_STATIC_PROVIDER_FLOOR: frozenset = frozenset({
    "anthropic", "openai", "groq", "gemini", "deepseek", "mistral",
    "qwen", "xai", "moonshot", "zhipu", "soruxgpt", "vulavula",
    "openrouter", "ollama", "vllm", "lmstudio", "mlx_vlm", "lemonade",
    # legacy/optional OpenAI-compat backends (harmless if unconfigured):
    "together", "fireworks", "llamacpp", "localai", "nim",
})


def _resolve_known_providers() -> set:
    """Known provider prefixes = the LIVE registry's registered names (single
    source of truth) unioned with the static floor.

    CBC: any provider configured in config/providers.yaml is ALWAYS routable via
    the address API without editing this module. The prior hardcoded set silently
    mis-routed every newer provider (qwen/xai/moonshot/zhipu/arcee) to the default
    provider. The floor only covers the parse-before-config-load case; it may
    drift without breaking correctness while the registry is loaded.
    """
    names = set(_STATIC_PROVIDER_FLOOR)
    try:
        reg = get_registry()
        names |= set(reg._providers)
        names |= set(reg._configs)
    except Exception as exc:  # fail-safe to the static floor — but no longer silent
        logger.debug("known-provider derive from registry failed; using static floor: %s", exc)
    return names


def parse_model_address(
    address: str, known_providers: Optional[set] = None
) -> tuple[str, str]:
    """Parse 'provider/model-name' into (provider, model).

    ``known_providers`` is the set of names treated as provider prefixes. When
    None it is derived from the LIVE registry (config/providers.yaml) so a newly
    configured provider routes without editing this function (correct-by-
    construction). Callers owning a registry pass ``set(registry._providers)``.

    Examples:
        'groq/openai/gpt-oss-120b' -> ('groq', 'openai/gpt-oss-120b')
        'ollama/llama3:70b'  -> ('ollama', 'llama3:70b')
        'openrouter/deepseek/deepseek-r1' -> ('openrouter', 'deepseek/deepseek-r1')
        'qwen/qwen3.5-flash' -> ('qwen', 'qwen3.5-flash')   # registered -> routable
        'gpt-oss-120b'       -> ('', 'gpt-oss-120b')         # no provider prefix
        'unknown/model'      -> ('', 'unknown/model')        # prefix not a provider
    """
    if "/" not in address:
        return "", address

    first_slash = address.index("/")
    provider = address[:first_slash]
    allow = known_providers if known_providers is not None else _resolve_known_providers()
    if provider in allow:
        return provider, address[first_slash + 1:]

    # Unknown prefix — treat entire string as model (could be openrouter path)
    return "", address


class ProviderRegistry:
    """Manages provider instances, health, and model resolution.

    7 public methods (ISP-compliant):
    register, get, chat, list_providers, list_models, check_health, load_config
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self._providers: dict[str, OpenAICompatProvider] = {}
        self._configs: dict[str, ProviderConfig] = {}
        self._health: dict[str, bool] = {}
        self._last_health_check: dict[str, float] = {}
        self._routing = RoutingConfig()
        self._health_ttl_s = 300.0
        self._lock = threading.Lock()
        if config_path is not None:
            self._load_config(config_path)
        elif os.path.exists(_CONFIG_PATH):
            self._load_config(_CONFIG_PATH)

    def register(self, source) -> None:
        """Register a provider from ProviderConfig or OpenAICompatProvider.

        Thread-safe: uses internal lock to prevent concurrent mutation.
        """
        if isinstance(source, ProviderConfig):
            if not source.enabled:
                return
            provider = OpenAICompatProvider(
                name=source.name,
                base_url=source.base_url,
                api_key_source=source.api_key_source,
                timeout_s=source.timeout_s,
                max_retries=source.max_retries,
                extra_headers=source.extra_headers,
            )
            with self._lock:
                self._providers[source.name] = provider
                self._configs[source.name] = source
                self._health[source.name] = True
        else:
            with self._lock:
                self._providers[source.name] = source
                self._health[source.name] = True

    def get(self, name: str) -> Optional[OpenAICompatProvider]:
        """Get a registered provider by name."""
        return self._providers.get(name)

    def chat(self, model_address: str, prompt: str, **kwargs) -> "ChatResponse":
        """Route a chat request using 'provider/model-name' addressing."""
        # CBC: resolve the prefix against THIS registry's live providers (single
        # source of truth), so any registered provider routes correctly.
        provider_name, model_name = parse_model_address(
            model_address, known_providers=set(self._providers)
        )

        if provider_name:
            provider = self._providers.get(provider_name)
            if not provider:
                raise KeyError(f"Provider '{provider_name}' not registered")
            return provider.chat(prompt, model=model_name, **kwargs)

        default = self._find_default_provider()
        if not default:
            raise RuntimeError("No providers registered")
        return default.chat(prompt, model=model_name, **kwargs)

    def list_providers(self) -> list[ProviderInfo]:
        """List all registered providers with health status."""
        return [
            ProviderInfo(
                name=name,
                type=self._configs[name].type if name in self._configs else ProviderType.CLOUD,
                base_url=provider.base_url,
                healthy=self._health.get(name, True),
                models=tuple(self._configs[name].models) if name in self._configs else (),
            )
            for name, provider in self._providers.items()
        ]

    def list_models(self, provider_name: Optional[str] = None) -> list[ModelInfo]:
        """List models from a specific provider or all providers."""
        if provider_name:
            provider = self._providers.get(provider_name)
            return provider.list_models() if provider else []

        return [
            model
            for provider in self._providers.values()
            for model in provider.list_models()
        ]

    def check_health(self, name: Optional[str] = None) -> dict[str, bool]:
        """Check health for one provider (by name) or all providers.

        Returns dict of {provider_name: healthy}. Uses 5-minute TTL cache.
        """
        targets = [name] if name else list(self._providers)
        result = {}
        now = time.monotonic()

        for pname in targets:
            provider = self._providers.get(pname)
            if not provider:
                result[pname] = False
                continue

            last = self._last_health_check.get(pname, 0)
            if now - last < self._health_ttl_s:
                result[pname] = self._health.get(pname, False)
                continue

            healthy = provider.health_check()
            self._health[pname] = healthy
            self._last_health_check[pname] = now
            result[pname] = healthy

        return result

    def _load_config(self, path: str) -> None:
        """Load provider configuration from YAML or JSON file."""
        if not os.path.exists(path):
            return
        configs, routing = _load_yaml_config(path)
        for config in configs:
            self.register(config)
        if routing:
            self._routing = routing

    @property
    def routing(self) -> RoutingConfig:
        """Current routing configuration."""
        return self._routing

    def _find_default_provider(self) -> Optional[OpenAICompatProvider]:
        """Find the default provider (marked as default, or first registered)."""
        for name, config in self._configs.items():
            if config.default and name in self._providers:
                return self._providers[name]
        if self._providers:
            return next(iter(self._providers.values()))
        return None


def _load_yaml_config(path: str) -> tuple[list[ProviderConfig], Optional[RoutingConfig]]:
    """Parse providers.yaml into ProviderConfig list + RoutingConfig."""
    if path.endswith(".json"):
        with open(path, "r") as f:
            data = json.load(f)
    else:
        data = _load_yaml_document(path)

    configs = [_parse_provider_entry(p) for p in data.get("providers", [])]
    routing = _parse_routing_entry(data.get("routing", {}))
    return configs, routing


def _parse_provider_entry(p: dict) -> ProviderConfig:
    """Parse a single provider dict into ProviderConfig."""
    models = p.get("models", [])
    auto_discover = models == "auto"
    if auto_discover:
        models = []

    return ProviderConfig(
        name=p["name"],
        type=ProviderType(p.get("type", "cloud")),
        base_url=p.get("base_url", ""),
        auth_method=AuthMethod(p.get("auth", p.get("auth_method", "api_key"))),
        api_key_source=p.get("api_key_source", p.get("api_key_env", "")),
        models=models if isinstance(models, list) else [],
        auto_discover_models=auto_discover,
        auto_detect=p.get("detect", False),
        default=p.get("default", False),
        enabled=p.get("enabled", True),
        extra_headers=p.get("extra_headers", {}),
        timeout_s=float(p.get("timeout_s", 120)),
        max_retries=int(p.get("max_retries", 3)),
    )


def _parse_routing_entry(r: dict) -> Optional[RoutingConfig]:
    """Parse routing config dict into RoutingConfig."""
    if not r:
        return None
    return RoutingConfig(
        default_strategy=RoutingStrategy(r.get("default", "fixed")),
        default_model=r.get("default_model", ""),
        fallback_chain=r.get("fallback_chain", []),
        quality_cost_tiers=r.get("quality_cost", {}),
    )


def _load_yaml_document(path: str) -> dict:
    """Parse a YAML document with PyYAML, a declared dependency.

    This replaced a 21-line hand-rolled parser that silently mis-parsed the
    real config: it promoted each provider's nested ``models:`` list to
    TOP-LEVEL ``providers`` entries, turning 20 provider dicts into 32 items of
    which 11 were bare model-id strings. The caller then did ``p["name"]`` on a
    string. Nothing in production hit it -- ProviderRegistry only calls
    _load_config when an explicit config_path is passed, and no shipped caller
    does -- so the only thing exercising it was the regression test that failed.

    There is deliberately no fallback, which is the same call
    lib/settings-merge.sh makes two directories away: "a degraded merge can
    erase mixed-group local hooks... Missing runtime support is safer as a
    visible failure." A parser that returns confidently wrong structure is
    worse than one that is honestly absent.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - declared in requirements.txt
        raise RuntimeError(
            f"PyYAML is required to read {path}. It is a declared dependency "
            "(requirements.txt: pyyaml>=6.0); install it with "
            "`grip-py -m pip install 'pyyaml>=6.0'`."
        ) from exc
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping, got {type(data).__name__}")
    return data
class _YamlParseState:
    """Mutable state for the YAML line processor."""

    def __init__(self) -> None:
        self.result: dict = {}
        self.current_key = ""
        self.current_list: list = []
        self.current_item: dict = {}
        self.current_dict: dict = {}
        self.in_list = False
        self.in_dict = False

    def flush(self) -> None:
        if not self.current_key:
            return
        if self.in_list:
            if self.current_item:
                self.current_list.append(self.current_item)
            self.result[self.current_key] = self.current_list
        elif self.in_dict:
            self.result[self.current_key] = self.current_dict


def _process_yaml_line(state: _YamlParseState, stripped: str, indent: int) -> None:
    """Process a single YAML line, updating parse state."""
    if indent == 0 and ":" in stripped:
        state.flush()
        key, _, val = stripped.partition(":")
        state.current_key = key.strip()
        val = val.strip()
        if val:
            state.result[state.current_key] = _parse_yaml_value(val)
            state.current_key = ""
        else:
            state.current_list = []
            state.current_item = {}
            state.current_dict = {}
            state.in_list = False
            state.in_dict = False
        return

    if stripped.lstrip().startswith("- "):
        state.in_list = True
        state.in_dict = False
        if state.current_item:
            state.current_list.append(state.current_item)
            state.current_item = {}
        rest = stripped.lstrip()[2:].strip()
        if ":" in rest:
            k, _, v = rest.partition(":")
            state.current_item[k.strip()] = _parse_yaml_value(v.strip())
        elif rest:
            state.current_list.append(_parse_yaml_value(rest))
        return

    if ":" in stripped:
        k, _, v = stripped.strip().partition(":")
        v = v.strip()
        if state.in_list:
            state.current_item[k.strip()] = _parse_yaml_value(v)
        else:
            state.in_dict = True
            state.current_dict[k.strip()] = _parse_yaml_value(v)


def _parse_yaml_value(val: str):
    """Parse a YAML scalar value."""
    if not val:
        return ""
    if val in ("true", "True", "yes"):
        return True
    if val in ("false", "False", "no"):
        return False
    if val == "auto":
        return "auto"
    if val.startswith("[") and val.endswith("]"):
        items = val[1:-1].split(",")
        return [item.strip().strip("'\"") for item in items if item.strip()]
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val.strip("'\"")
