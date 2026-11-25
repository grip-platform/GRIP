"""
Embedding engine — abstract base + constants + delegating helpers.

W6 H218 retired the sqlite-lembed concrete implementation. The active backend
is :class:`OllamaEmbeddingBackend` (see ``ollama_backend.py``), surfaced via
:func:`get_engine` per the syscall doctrine.

This module keeps only the parts that are NOT lembed-specific:
    - Constants (CLAUDE_DIR, DB_PATH, MODEL_PATH, VECTOR_DIM, MODEL_NAME).
    - Abstract :class:`EmbeddingEngine` declaring the subclass contract.
    - Generic helpers (``_hash_text``, ``close``).
    - Thin delegators to sibling submodules (storage / search) so concrete
      backends inherit the public surface without duplicating the wiring.

For backward compatibility the parent package's ``__init__.py`` re-exports
``OllamaEmbeddingBackend`` as ``EmbeddingEngine`` so legacy callers using
``EmbeddingEngine()`` directly continue to work without code changes.
Internal sibling modules (ollama_backend.py, cli.py) still import the
abstract :class:`EmbeddingEngine` from this module for class declarations.
"""

import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude. lib/embeddings/ -> lib/.
_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
DB_PATH = CLAUDE_DIR / "embeddings.db"
MODEL_PATH = CLAUDE_DIR / "models" / "nomic-embed-text-v1.5.Q8_0.gguf"
VECTOR_DIM = 768
MODEL_NAME = "nomic"

_log = logging.getLogger(__name__)


class EmbeddingEngine:
    """Abstract embedding engine. Concrete subclasses override the model
    layer (``connect`` / ``_ensure_model`` / ``embed_text``); storage and
    search are inherited via the delegating helpers below."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        """Bind the engine to an embeddings DB.

        ``db_path`` is the optional target database path; ``None`` falls back
        to the canonical :data:`DB_PATH`. This base ``__init__`` is the
        contract the ``GRIP_EMBED_BACKEND=lembed`` path in :func:`get_engine`
        depends on: it accepts AND stores ``db_path`` (subclasses inherit it),
        so constructing the bare base engine with a custom ``db_path`` is
        supported — not a TypeError. The bare base loads no model
        (``conn`` stays ``None``, ``_lembed_available`` stays ``False``).
        """
        self.db_path = db_path or DB_PATH
        self.conn = None
        self._lembed_available = False

    # --- Subclass hooks ---

    def connect(self):
        raise NotImplementedError("Subclass must override connect()")

    def _ensure_model(self) -> None:
        """No-op default. Lembed-era subclasses overrode this to register
        the embedding model into a sqlite virtual table; ollama needs no
        such step (the daemon owns the model)."""
        return None

    def embed_text(self, text: str, use_cache: bool = True) -> bytes:
        raise NotImplementedError("Use OllamaEmbeddingBackend (via get_engine())")

    # --- Generic helpers ---

    def _hash_text(self, text: str) -> str:
        """SHA-256 hex digest of text, truncated to 32 chars."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def _cosine_similarity(self, vec1: bytes, vec2: bytes) -> float:
        """Cosine similarity of two float32 vectors (sqlite-vec BLOBs), in
        [-1, 1]. Delegates the distance to sqlite-vec's ``vec_distance_cosine``
        over the backend connection (the extension is loaded in ``connect()``)
        and returns ``1 - distance`` so identical vectors score 1.0.

        Restored verbatim from the pre-package monolith (``lib/embeddings.py``):
        it was dropped when that module was split into the ``embeddings/``
        package, which silently broke ``calculate_novelty`` (storage.py) and
        ``classify_against_concepts`` (classify.py) — both call this method.
        """
        conn = self.connect()
        result = list(conn.execute(
            "SELECT 1 - vec_distance_cosine(?, ?)",
            (vec1, vec2)
        ))
        return result[0][0]

    def close(self) -> None:
        """Close the database connection if open."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    # --- Schema + storage delegators ---

    def init_schema(self):
        from .storage import init_schema
        return init_schema(self)

    def store_embedding(self, text, embed_type, **kw):
        from .storage import store_embedding as _store
        return _store(self, text, embed_type, **kw)

    # Historical private name (used by lib/embeddings/cli.py); same impl.
    _store_embedding = store_embedding

    def get_stats(self):
        from .storage import get_stats as _stats
        return _stats(self)

    # Historical private name, for parity with _store_embedding/_search_hybrid.
    _get_stats = get_stats

    # Live delegator: learning_detector._compute_novelty calls
    # engine._calculate_novelty(text) on the get_engine() backend. The method
    # alias was dropped in the package split (the impl moved to
    # storage.calculate_novelty as a module function), so that real-time
    # novelty path silently raised AttributeError. Same contract as the
    # monolith method: returns (novelty_score, coherence_metadata).
    def _calculate_novelty(self, text: str, recent_limit: int = 20):
        from .storage import calculate_novelty
        return calculate_novelty(self, text, recent_limit)

    # --- Search delegators ---

    def _search_similar(self, query_text, **kw):
        from .search import search_similar
        return search_similar(self, query_text, **kw)

    def _search_by_vector(self, query_vector, **kw):
        from .search import search_by_vector
        return search_by_vector(self, query_vector, **kw)

    def search_hybrid(self, query_text, **kw):
        from .search import search_hybrid as _hybrid
        return _hybrid(self, query_text, **kw)

    # Historical private name; same implementation.
    _search_hybrid = search_hybrid


def get_engine(db_path: Optional[Path] = None) -> "EmbeddingEngine":
    """Factory for the embedding backend, dispatched on ``GRIP_EMBED_BACKEND``.

    Backend axis (env var, W3/W6 H215/H218 contract):
        - ``lembed`` -> the legacy base :class:`EmbeddingEngine` (explicit
          opt-in fallback; the in-process lembed model layer was retired in
          W6 H218, so this is the bare base class with no model).
        - ``ollama`` / unset / unknown -> the wired backend, selected via the
          config-driven registry shim (:func:`registry.get_engine_for`,
          ADR-0007). An unknown value logs a warning and falls back to the
          default rather than failing the caller.

    ``db_path`` (optional) is threaded to the chosen backend so callers can
    target a non-default embeddings DB (tests, migrations). ``None`` keeps the
    canonical :data:`DB_PATH`.

    With no env var and no ``config/embeddings.yaml`` this returns an
    :class:`OllamaEmbeddingBackend` (byte-identical to the pre-shim default,
    ollama since W6 H218).
    """
    backend = os.environ.get("GRIP_EMBED_BACKEND", "").strip().lower()
    if backend == "lembed":
        # lembed is RETIRED (W6/H218 removed the in-process model): this base class
        # has no model and raises NotImplementedError on embed_text. Warn loudly so
        # an explicit opt-in is never a silent no-op — unset GRIP_EMBED_BACKEND for
        # the working ollama backend. (AGORA #4022 follow-up.)
        _log.warning(
            "GRIP_EMBED_BACKEND=lembed is RETIRED and has no model — embeddings will "
            "fail on use. Unset GRIP_EMBED_BACKEND to use the ollama backend."
        )
        return EmbeddingEngine(db_path=db_path)
    if backend and backend != "ollama":
        _log.warning(
            "Unknown GRIP_EMBED_BACKEND=%r; falling back to ollama default.",
            backend,
        )
    from .registry import get_engine_for
    return get_engine_for(db_path=db_path)
