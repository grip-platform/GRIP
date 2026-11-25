"""Embedding model registry — config-driven, refuse-on-load on dim mismatch.

Shim seam between callers (which use ``get_engine()``) and concrete
backends (``OllamaEmbeddingBackend`` etc.). Future model swaps touch this
file + ``config/embeddings.yaml``, never call sites.

PARAMOUNT invariants:
    1. Default behaviour byte-identical to pre-shim when no
       ``config/embeddings.yaml`` is present.
    2. Dim mismatch between the requested model and the current
       ``VECTOR_DIM`` is rejected at load (raises
       :class:`EmbeddingDimensionMismatchError`), never silently.
    3. Unknown model_id raises :class:`UnknownEmbeddingModelError` with
       the full list of registered ids.

No new dependencies — PyYAML import is try/except with a graceful
fallback to compiled-in defaults if yaml or the config file is absent.

Hypothesis: H-EMBED-SHIM-1 (deadline 2026-06-04).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import EmbeddingEngine

# Path-portability shim (rules/path-portability.md): CLAUDE_DIR resolves to
# $CLAUDE_DIR env (when set) or $HOME/.claude. lib/embeddings/ -> lib/.
_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
CONFIG_PATH = CLAUDE_DIR / "config" / "embeddings.yaml"
DEFAULT_MODEL_ID = "nomic-embed-text"

# Module-level cache: path_str -> (mtime_ns_or_float, parsed_dict).
# Invalidated when ``Path.stat().st_mtime`` differs from the cached mtime.
# Process-local; not shared across processes. ``_clear_config_cache()``
# resets state for tests.
_CONFIG_CACHE: Dict[str, Tuple[float, Dict]] = {}


def _clear_config_cache() -> None:
    """Drop the module-level YAML config cache. Test-only helper."""
    _CONFIG_CACHE.clear()


class UnknownEmbeddingModelError(KeyError):
    """Raised when ``get_engine_for(model_id)`` receives an unregistered id."""


class EmbeddingDimensionMismatchError(RuntimeError):
    """Raised when a registered model's declared dimension differs from the
    canonical ``VECTOR_DIM`` of the live embeddings database."""


def _ollama_factory(db_path: Optional[Path] = None) -> "EmbeddingEngine":
    from .ollama_backend import OllamaEmbeddingBackend
    return OllamaEmbeddingBackend(db_path=db_path)


# Registry shape: model_id -> {dimension, factory, backend_name}.
# Factories return :class:`EmbeddingEngine` subclass instances.
# Only ``nomic-embed-text`` is wired; other entries declare the shape so the
# refuse-on-load + unknown-model paths have something to deny against (and
# they document the planned migration targets from ADR-0007).
_REGISTRY: Dict[str, Dict] = {
    "nomic-embed-text": {
        "dimension": 768,
        "factory": _ollama_factory,
        "backend": "ollama",
    },
    "all-MiniLM-L6-v2": {
        "dimension": 384,
        "factory": None,  # not wired — dim mismatch with current DB anyway
        "backend": "sentence-transformers",
    },
    "bge-small-en-v1.5": {
        "dimension": 384,
        "factory": None,
        "backend": "sentence-transformers",
    },
    "all-mpnet-base-v2": {
        "dimension": 768,
        "factory": None,
        "backend": "sentence-transformers",
    },
}


def _load_yaml_config() -> Dict:
    """Read ``config/embeddings.yaml`` if present and parseable. Stdlib-only
    fallback: returns ``{}`` if PyYAML is absent or the file is missing.

    Cached at module level keyed by path, invalidated when ``st_mtime``
    changes. A missing file returns ``{}`` without caching so a later
    file creation is detected on the next call. Parse errors / missing
    PyYAML also return ``{}`` without caching for the same reason.
    """
    path_str = str(CONFIG_PATH)
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        # File absent (FileNotFoundError) or unreadable. Drop any stale
        # cache entry so a later creation is detected.
        _CONFIG_CACHE.pop(path_str, None)
        return {}

    cached = _CONFIG_CACHE.get(path_str)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    _CONFIG_CACHE[path_str] = (mtime, data)
    return data


def default_model_id() -> str:
    """Resolve the default model id: ``config/embeddings.yaml`` ->
    ``default_model`` key, else compiled-in :data:`DEFAULT_MODEL_ID`."""
    cfg = _load_yaml_config()
    cfg_default = cfg.get("default_model") if isinstance(cfg, dict) else None
    return cfg_default if isinstance(cfg_default, str) and cfg_default else DEFAULT_MODEL_ID


def registered_models() -> Dict[str, Dict]:
    """Return a shallow copy of the registry (test/inspection surface)."""
    return {k: dict(v) for k, v in _REGISTRY.items()}


def validate_dimension(model_id: str, expected_dim: int) -> None:
    """Assert that ``model_id`` declares dimension == ``expected_dim``.

    Pure registry lookup — does NOT instantiate the backend factory.
    Use this from tooling that needs to check dim compatibility before
    committing to an engine instance (e.g. migration planners, doctor
    diagnostics, multi-model dispatch).

    Raises:
        UnknownEmbeddingModelError: if ``model_id`` is not registered.
        EmbeddingDimensionMismatchError: if the declared dim differs from
            ``expected_dim``.
    """
    if model_id not in _REGISTRY:
        raise UnknownEmbeddingModelError(
            f"Unknown embedding model_id={model_id!r}; "
            f"registered: {sorted(_REGISTRY)}"
        )
    declared = _REGISTRY[model_id]["dimension"]
    if declared != expected_dim:
        raise EmbeddingDimensionMismatchError(
            f"Model {model_id!r} declares dimension={declared} but "
            f"current VECTOR_DIM={expected_dim}. A dimension change requires "
            f"a DB migration (out of scope for the registry shim)."
        )


def get_engine_for(
    model_id: Optional[str] = None,
    *,
    expected_dim: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> "EmbeddingEngine":
    """Return an engine for ``model_id`` (default: :func:`default_model_id`).

    Refuses if model_id is unknown OR if ``expected_dim`` (when provided) does
    not match the registry's declared dimension. ``expected_dim`` defaults to
    the live ``VECTOR_DIM`` from ``engine.py`` so dim drift is caught before
    any byte hits the DB. Dim check delegates to :func:`validate_dimension`.

    ``db_path`` (optional) is threaded to the backend factory so callers (and
    :func:`engine.get_engine`) can target a non-default embeddings DB. ``None``
    keeps the backend's canonical ``DB_PATH``.
    """
    mid = model_id or default_model_id()
    if expected_dim is None:
        from .engine import VECTOR_DIM
        expected_dim = VECTOR_DIM
    validate_dimension(mid, expected_dim)
    entry = _REGISTRY[mid]
    factory: Optional[Callable[..., "EmbeddingEngine"]] = entry.get("factory")
    if factory is None:
        raise UnknownEmbeddingModelError(
            f"Model {mid!r} is registered (shape known) but its backend "
            f"({entry.get('backend')!r}) is not yet wired. "
            f"See docs/adr/0007-embedding-model-pluggability.md."
        )
    # Keyword call (not positional) so factories with keyword-only signatures
    # (def f(*, db_path=None)) also work — AGORA #3334 forward-hardening.
    return factory(db_path=db_path)
