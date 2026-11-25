"""
Semantic RL++ Embedding package.

Modules:
    - engine: Abstract base + constants (post W6 H218)
    - ollama_backend: Concrete OllamaEmbeddingBackend (active backend)
    - storage: Database operations, schema, queue processing
    - search: Hybrid search (vector + FTS5 + RRF fusion)
    - classify: Intent/feedback/skill/command classification

Usage:
    from embeddings import get_engine
    engine = get_engine()
    vector = engine.embed_single("hello world")

Backward compat:
    from embeddings import EmbeddingEngine
    engine = EmbeddingEngine()  # resolves to OllamaEmbeddingBackend
"""

from .engine import (
    CLAUDE_DIR,
    DB_PATH,
    MODEL_PATH,
    VECTOR_DIM,
    MODEL_NAME,
    get_engine,
)
from .ollama_backend import OllamaEmbeddingBackend as EmbeddingEngine
from .registry import (
    DEFAULT_MODEL_ID,
    EmbeddingDimensionMismatchError,
    UnknownEmbeddingModelError,
    default_model_id,
    get_engine_for,
    registered_models,
    validate_dimension,
)

__all__ = [
    'EmbeddingEngine',
    'CLAUDE_DIR',
    'DB_PATH',
    'MODEL_PATH',
    'VECTOR_DIM',
    'MODEL_NAME',
    'get_engine',
    # registry shim seam (ADR-0007)
    'get_engine_for',
    'default_model_id',
    'registered_models',
    'DEFAULT_MODEL_ID',
    'UnknownEmbeddingModelError',
    'EmbeddingDimensionMismatchError',
    'validate_dimension',
]
