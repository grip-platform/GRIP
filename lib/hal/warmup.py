"""HAL Model Warmup — pre-load local models for low-latency inference.

When the channel bridge detects Discord activity, this module warms
local LLM providers so response latency is minimal. Ollama models get
loaded into GPU memory via keep_alive; other providers get a minimal
completion request.

Usage:
    from lib.hal.warmup import warmup_local_models
    warmed = warmup_local_models()  # -> ['llama3.2:3b']
"""

import json
import os
import urllib.request
import urllib.error

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("GRIP_OLLAMA_MODEL", "llama3.2:3b")
WARMUP_KEEP_ALIVE = "30m"


def _probe_ollama() -> bool:
    """Check if Ollama is reachable. O(1) — single HTTP GET."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _warmup_ollama(model: str = OLLAMA_MODEL) -> str | None:
    """Send keep_alive ping to load model into GPU memory."""
    try:
        payload = json.dumps({
            "model": model,
            "prompt": "",
            "keep_alive": WARMUP_KEEP_ALIVE,
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30):
            return model
    except (urllib.error.URLError, OSError):
        return None


def warmup_local_models() -> list[str]:
    """Discover and warm local LLM providers. Returns list of warmed model names."""
    warmed: list[str] = []

    if _probe_ollama():
        result = _warmup_ollama()
        if result:
            warmed.append(result)

    return warmed


if __name__ == "__main__":
    models = warmup_local_models()
    if models:
        print(f"[hal-warmup] Warmed: {', '.join(models)}")
    else:
        print("[hal-warmup] No local models available")
