"""HAL embedding shim — syscall doctrine for embedding generation.

Per rules/syscall-doctrine.md: stable API independent of provider, mockable,
audit-logged, hot-swappable, plain-language errors. Embeddings is the third
GRIP-internal external boundary to be shimmed (after slack_transport and
the chat HAL providers).

Usage:
    from lib.hal.embeddings import embed, health, EmbeddingProviderError

    vector = embed("text")          # list[float], typically 768d
    status = health()               # dict for /grip-doctor

Provider selected by GRIP_EMBED_PROVIDER env var (default 'ollama').
Tests inject a fake via set_provider() — see tests/test_hal_embedding_router.py.

Falsifier: if a future provider's API doesn't fit embed(text)->vec OR if
the audit log entry shape changes between releases, the shim's stable-API
guarantee is broken.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmbeddingProviderError(Exception):
    """Plain-language failure surfacing from the embedding provider boundary."""


def _pick_pulled_tag(pulled: List[str], requested: str) -> Optional[str]:
    """Pick an actually-pulled ollama tag for ``requested``.

    Ollama maps an untagged name (e.g. ``nomic-embed-text``) to ``:latest``,
    but GRIP installs commonly pull a specific tag (``:v1.5``), so a bare name
    404s at ``/api/embeddings`` ("model not found") — the exact failure that
    silently took the semantic-memory engine offline (grip_doctor Embeddings
    RED, 2026-07-14). Resolution order: exact match -> ``:latest`` -> the
    lexicographically-first tag of the same base (stable across daemon
    restarts). Returns None when no tag of the same base is pulled.

    Pure (no I/O) — unit-tested directly. Sibling copy:
    ``lib/embeddings/ollama_backend.py::_ensure_model`` (the store/recall
    path). Two copies were deliberate while the knowledge-maturation threshold
    was N=3; at N=2 (since 2026-10-03) a shared helper is eligible, and its
    first build goes through the functor's promotion gate.
    """
    if requested in pulled:
        return requested
    base = requested.split(":")[0]
    matches = sorted(n for n in pulled if n.split(":")[0] == base)
    if not matches:
        return None
    return next((n for n in matches if n.endswith(":latest")), matches[0])


class OllamaEmbeddingProvider:
    """ollama /api/embeddings provider — single external boundary."""

    name = "ollama"

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        timeout_s: float = 10.0,
        keep_alive: int = -1,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model                          # requested name (never mutated)
        self._resolved_model: Optional[str] = None  # actually-pulled tag, lazily resolved
        self.timeout_s = timeout_s
        # keep_alive=-1 pins the (tiny, ~274MB) embed model resident in ollama so
        # semantic search stays always-warm — no per-call reload after ollama's
        # default 5-min idle unload. Trivial RAM cost; docs/EMBEDDINGS-ARCHITECTURE.md.
        self.keep_alive = keep_alive

    def _resolve_model(self) -> str:
        """Resolve ``self.model`` to an actually-pulled tag (untagged-404 guard).

        Queries ``/api/tags`` once and caches the result for this provider's
        lifetime. Staleness note (AGORA #4022 follow-up): a mid-session
        ``ollama pull`` of a new tag is NOT picked up until a fresh provider is
        constructed — acceptable because re-tagging mid-session is rare and a new
        session/provider resolves afresh. Raises EmbeddingProviderError with
        plain-language remediation on an unreachable daemon or a not-pulled model.
        """
        if self._resolved_model is not None:
            return self._resolved_model
        req = urllib.request.Request(f"{self.endpoint}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise EmbeddingProviderError(
                f"ollama daemon unreachable at {self.endpoint} — start it "
                f"(`brew services start ollama` or `ollama serve`). "
                f"underlying: {exc.__class__.__name__}"
            ) from exc
        pulled = [m.get("name", "") for m in tags.get("models", []) if m.get("name")]
        chosen = _pick_pulled_tag(pulled, self.model)
        if chosen is None:
            raise EmbeddingProviderError(
                f"ollama model '{self.model}' not pulled at {self.endpoint}. "
                f"Run: ollama pull {self.model}"
            )
        self._resolved_model = chosen
        return chosen

    def embed(self, text: str) -> List[float]:
        model = self._resolve_model()
        body = json.dumps(
            {"model": model, "prompt": text, "keep_alive": self.keep_alive}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint}/api/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise EmbeddingProviderError(
                f"ollama embeddings unreachable at {self.endpoint} — start the "
                f"ollama daemon (`brew services start ollama` or `ollama serve`). "
                f"underlying: {exc.__class__.__name__}"
            ) from exc

        vec = payload.get("embedding")
        if not isinstance(vec, list) or not vec:
            raise EmbeddingProviderError(
                f"ollama returned unexpected response from {self.endpoint}: "
                f"missing or empty 'embedding' field. Is the model "
                f"'{model}' installed? Run `ollama pull {self.model}`."
            )
        return vec

    def health(self) -> dict:
        t0 = time.monotonic()
        try:
            vec = self.embed("ping")
        except EmbeddingProviderError as exc:
            return {
                "ok": False,
                "provider": self.name,
                "endpoint": self.endpoint,
                "model": self.model,
                "error": str(exc),
            }
        return {
            "ok": True,
            "provider": self.name,
            "endpoint": self.endpoint,
            "model": self._resolved_model or self.model,
            "dimension": len(vec),
            "latency_ms": round((time.monotonic() - t0) * 1000),
        }


_provider: Optional[OllamaEmbeddingProvider] = None


def set_provider(provider) -> None:
    """Inject or clear the active provider — for tests and hot-swap."""
    global _provider
    _provider = provider


def get_provider():
    """Return the active provider, lazily resolving from GRIP_EMBED_PROVIDER."""
    global _provider
    if _provider is not None:
        return _provider
    choice = os.environ.get("GRIP_EMBED_PROVIDER", "ollama")
    if choice == "ollama":
        _provider = OllamaEmbeddingProvider()
        return _provider
    raise EmbeddingProviderError(
        f"unknown GRIP_EMBED_PROVIDER='{choice}'. "
        f"Valid choices: 'ollama' (default). "
        f"For lembed legacy fallback, set GRIP_EMBED_BACKEND=lembed instead "
        f"(routes via lib/embeddings/__init__.py:get_engine, not this shim)."
    )


def embed(text: str) -> List[float]:
    """Generate an embedding vector via the active provider.

    Audit-logged at INFO: provider, text length, dimension, latency.
    """
    t0 = time.monotonic()
    provider = get_provider()
    vec = provider.embed(text)
    logger.info(
        "embed: provider=%s text_len=%d dim=%d latency_ms=%d",
        provider.name, len(text), len(vec),
        round((time.monotonic() - t0) * 1000),
    )
    return vec


def health() -> dict:
    """Health probe for /grip-doctor and operational dashboards."""
    try:
        return get_provider().health()
    except EmbeddingProviderError as exc:
        return {"ok": False, "error": str(exc)}
