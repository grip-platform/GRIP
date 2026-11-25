"""HAL Local Auto-Discovery — probe known ports for local LLM servers.

Checks Ollama, vLLM, llama.cpp, LM Studio on their default ports.
Registers anything that responds with the provider registry.

Usage:
    from lib.hal.discovery import discover_local_providers

    discovered = discover_local_providers(registry)
"""
import json
import urllib.error
import urllib.request

from lib.hal.types import AuthMethod, ProviderConfig, ProviderType

# (name, port, health_path, model_list_path)
_LOCAL_SERVERS = [
    ("ollama", 11434, "/api/version", "/api/tags"),
    ("vllm", 8000, "/v1/models", "/v1/models"),
    ("lmstudio", 1234, "/v1/models", "/v1/models"),
    ("llamacpp", 8080, "/v1/models", "/v1/models"),
]

_PROBE_TIMEOUT_S = 2.0


def discover_local_providers(
    registry: "ProviderRegistry",
    timeout_s: float = _PROBE_TIMEOUT_S,
) -> list[str]:
    """Probe local ports and register discovered servers.

    Returns list of discovered provider names.
    """
    discovered = []
    for name, port, health_path, model_path in _LOCAL_SERVERS:
        if registry.get(name) is not None:
            continue

        base_url = f"http://localhost:{port}"
        if _probe(base_url, health_path, timeout_s):
            config = _build_local_config(name, base_url, model_path)
            registry.register(config)
            discovered.append(name)

    return discovered


def _probe(base_url: str, path: str, timeout_s: float) -> bool:
    """Check if a local server is running at the given URL."""
    try:
        req = urllib.request.Request(f"{base_url}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _build_local_config(name: str, base_url: str, model_path: str) -> ProviderConfig:
    """Build a ProviderConfig for a discovered local server."""
    oai_base = f"{base_url}/v1" if name == "ollama" else base_url
    return ProviderConfig(
        name=name,
        type=ProviderType.LOCAL,
        base_url=oai_base,
        auth_method=AuthMethod.NONE,
        auto_discover_models=True,
        auto_detect=True,
        models=_fetch_models(base_url, model_path, name),
    )


def _fetch_models(base_url: str, path: str, name: str) -> list[str]:
    """Fetch model list from a local server."""
    try:
        req = urllib.request.Request(f"{base_url}{path}", method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    if name == "ollama":
        return [m["name"] for m in data.get("models", []) if m.get("name")]

    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
