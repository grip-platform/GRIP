#!/usr/bin/env python3
"""Ollama-backed Embedding Backend.

Drop-in subclass of :class:`EmbeddingEngine` that produces embeddings via
the local ollama daemon (`/api/embeddings`) instead of in-process lembed.

Same on-disk schema, same vector dimension (768, nomic-embed-text), same
downstream API (store_embedding / search_hybrid / get_stats / etc.).

Trade-offs vs lembed:
- Process isolation: ollama failures return HTTP 500, never SIGSEGV.
- Latency: ~50ms p50, ~1s p95 for long texts (W1 H213 evidence).
- Cache: namespaced under "ol:" prefix so prompt_cache stays compatible
  with parallel lembed entries during transition.
"""
import json
import struct
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import apsw
import sqlite_vec

from .engine import EmbeddingEngine, VECTOR_DIM

OLLAMA_ENDPOINT = "http://localhost:11434"
OLLAMA_MODEL = "nomic-embed-text"
OLLAMA_TIMEOUT_S = 10
CACHE_PREFIX = "ol:"  # disambiguates ollama-vs-lembed cache entries


class EmbeddingServerUnreachable(RuntimeError):
    """No embedding server answered at the endpoint. Semantic memory is optional,
    so scripts/embed-concepts.py reports this in one line and skips instead of
    failing. A RuntimeError, so every existing handler still catches it."""


class OllamaEmbeddingBackend(EmbeddingEngine):
    """Subclass overriding model loading and embedding generation.

    Inherits all storage/search/classification methods from EmbeddingEngine.
    Only the model layer (connect / _ensure_model / embed_text) is replaced.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        endpoint: str = OLLAMA_ENDPOINT,
        model: str = OLLAMA_MODEL,
    ):
        super().__init__(db_path=db_path)
        self.endpoint = endpoint.rstrip("/")
        self.model = model                  # caller's requested model (never mutated)
        self._resolved_model: str = model   # actually-pulled tag (set by _ensure_model)
        self._ollama_healthy: Optional[bool] = None

    def connect(self) -> apsw.Connection:
        """Load only sqlite-vec; lembed not required for ollama backend."""
        if self.conn is not None:
            return self.conn

        self.conn = apsw.Connection(str(self.db_path))
        # embeddings.db is multi-process (swarm sessions, relay, heartbeat all
        # read/write it); wait for locks instead of erroring instantly.
        self.conn.setbusytimeout(10000)
        self.conn.enableloadextension(True)
        self.conn.loadextension(sqlite_vec.loadable_path())
        self._lembed_available = False
        return self.conn

    def _ensure_model(self):
        """Probe ollama once; cache health for subsequent calls."""
        if self._ollama_healthy is True:
            return
        try:
            req = urllib.request.Request(
                f"{self.endpoint}/api/tags", method="GET"
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                tags = json.loads(resp.read())
        except (urllib.error.URLError, OSError) as e:
            raise EmbeddingServerUnreachable(
                f"Ollama daemon unreachable at {self.endpoint}: {e}"
            ) from e

        # Resolve to an ACTUALLY-PULLED tag, deterministically, WITHOUT mutating
        # the caller's requested self.model. Ollama maps an untagged name (e.g.
        # "nomic-embed-text") to ":latest", but installs commonly pull a specific
        # tag (":v1.5"), so a bare name 404s at /api/embeddings ("model not
        # found"). When the exact request is not itself pulled, adopt a pulled tag
        # of the same base: prefer ":latest", else the lexicographically-first tag
        # (stable across daemon restarts / tags-list ordering). The chosen tag is
        # cached in self._resolved_model (used by _ollama_embed); self.model is
        # left untouched so callers/logs still see what was requested.
        # Regression: tests/test_ollama_backend.py.
        pulled = [m.get("name", "") for m in tags.get("models", []) if m.get("name")]
        if self.model in pulled:
            self._resolved_model = self.model
        else:
            req_base = self.model.split(":")[0]
            matches = sorted(n for n in pulled if n.split(":")[0] == req_base)
            if not matches:
                raise RuntimeError(
                    f"Ollama model '{self.model}' not pulled. "
                    f"Run: ollama pull {self.model}"
                )
            self._resolved_model = next(
                (n for n in matches if n.endswith(":latest")), matches[0]
            )
        self._ollama_healthy = True

    def _ollama_embed(self, text: str) -> list:
        """POST to /api/embeddings; return raw vector list."""
        payload = json.dumps({"model": self._resolved_model, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as resp:
            body = json.loads(resp.read())
        vec = body.get("embedding")
        if not isinstance(vec, list) or len(vec) != VECTOR_DIM:
            shape = len(vec) if isinstance(vec, list) else type(vec).__name__
            raise RuntimeError(
                f"Ollama returned invalid embedding shape: {shape} "
                f"(expected list[{VECTOR_DIM}])"
            )
        return vec

    def embed_text(self, text: str, use_cache: bool = True) -> bytes:
        """Generate embedding via ollama; return as float32 bytes.

        Same return type as :meth:`EmbeddingEngine.embed_text` — raw
        struct-packed bytes compatible with sqlite-vec vec0 storage.
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty or whitespace-only text")

        text_hash = CACHE_PREFIX + self._hash_text(text)[:29]  # 32 chars total

        if use_cache:
            conn = self.connect()
            row = list(conn.execute(
                "SELECT vector FROM prompt_cache WHERE hash = ?",
                (text_hash,),
            ))
            if row:
                return row[0][0]

        self._ensure_model()
        truncated = text[:2000].strip()
        if not truncated:
            raise ValueError("Text empty after truncation")

        vec = self._ollama_embed(truncated)
        vector_bytes = struct.pack(f"{VECTOR_DIM}f", *vec)

        if use_cache:
            try:
                conn = self.connect()
                conn.execute(
                    "INSERT OR REPLACE INTO prompt_cache (hash, vector) VALUES (?, ?)",
                    (text_hash, vector_bytes),
                )
            except Exception:
                pass

        return vector_bytes

    def search_semantic(self, query_text, **kw):
        """Public vector-only semantic search (no FTS5).

        Wraps the inherited private :meth:`_search_similar`. Added because
        the migration plan lists `search_semantic` as part of the public
        interface even though :class:`EmbeddingEngine` only exposes the
        underscored form. Promoted to the parent class in W3.
        """
        return self._search_similar(query_text, **kw)

    def search_similar(self, query_text, **kw):
        """Public vector-only similarity search — interface-parity alias.

        Mirrors the inherited private :meth:`_search_similar` (which delegates
        to :func:`search.search_similar`), exposing it under the public name
        callers expect. Same signature and same ``List[Dict[str, Any]]`` return
        shape — keyword args (``embed_type``, ``limit``, ``exclude_project``,
        ``min_success``) pass straight through. Added because session-mesh
        KONO search (``session_control_server._kono_search``) calls the public
        ``search_similar`` form, but :class:`EmbeddingEngine` only ever exposed
        the underscored one (#3383).
        """
        return self._search_similar(query_text, **kw)
