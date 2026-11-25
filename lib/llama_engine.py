#!/usr/bin/env python3
"""
llama_engine.py -- Local LLM inference engine for GRIP.

Mirrors EmbeddingEngine pattern: lazy model loading, GGUF model,
single connect() entry point. Uses llama-cpp-python.

Model: Llama 3.2 3B Instruct (Q4_K_M quantization, ~2GB).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path.home() / ".claude"
MODEL_DIR = CLAUDE_DIR / "models"
DEFAULT_MODEL = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
DEFAULT_MODEL_PATH = MODEL_DIR / DEFAULT_MODEL
DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.1  # Low temp for scoring (near-deterministic)


class LlamaEngine:
    """Local LLM inference engine for GRIP meta-evaluation.

    Handles:
        - GGUF model loading (Llama 3.2 3B Instruct)
        - Text completion with chat format
        - JSON-mode generation for structured scoring
        - Resource management (lazy load, explicit unload)

    Mirrors EmbeddingEngine (lib/embeddings/engine.py) lifecycle:
        engine = LlamaEngine()
        engine.connect()       # Lazy -- loads model on first use
        result = engine.generate("Score this code")
        engine.close()         # Free RAM
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        n_ctx: int = 2048,
        n_gpu_layers: int = -1,
    ):
        """Initialise engine with model path and inference parameters.

        Args:
            model_path: Path to GGUF model file. Defaults to models/Llama-3.2-3B-Instruct-Q4_K_M.gguf.
            n_ctx: Context window size (tokens). 2048 sufficient for scoring prompts.
            n_gpu_layers: Layers to offload to GPU. -1 = all (Metal on macOS, CUDA on Linux).
        """
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self._model = None
        self._n_ctx = n_ctx
        self._n_gpu_layers = n_gpu_layers

    def connect(self):
        """Load model if not already loaded (lazy singleton).

        Returns:
            The llama_cpp.Llama instance.

        Raises:
            FileNotFoundError: If model file does not exist.
            ImportError: If llama-cpp-python is not installed.
        """
        if self._model is not None:
            return self._model

        if not self.model_path.exists():
            raise FileNotFoundError(
                f"Llama model not found: {self.model_path}. "
                f"Run: bash ~/.claude/scripts/bootstrap-llama.sh"
            )

        try:
            import llama_cpp
        except ImportError:
            raise ImportError(
                "llama-cpp-python not installed. "
                "Run: pip install llama-cpp-python"
            )

        logger.info("Loading Llama model: %s", self.model_path.name)
        self._model = llama_cpp.Llama(
            model_path=str(self.model_path),
            n_ctx=self._n_ctx,
            n_gpu_layers=self._n_gpu_layers,
            verbose=False,
        )
        logger.info("Llama model loaded successfully")
        return self._model

    def generate(
        self,
        prompt: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        json_mode: bool = False,
    ) -> str:
        """Generate text completion via chat format.

        Args:
            prompt: User message content.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0 = greedy, 1.0 = creative).
            json_mode: If True, constrain output to valid JSON.

        Returns:
            Generated text string.

        Raises:
            FileNotFoundError: If model not found (via connect()).
            RuntimeError: If generation fails.
        """
        model = self.connect()

        kwargs = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = model.create_chat_completion(**kwargs)
            content = response["choices"][0]["message"]["content"]
            return content if content else ""
        except Exception as e:
            raise RuntimeError(f"Llama generation failed: {e}") from e

    def close(self):
        """Unload model and free memory."""
        if self._model is not None:
            del self._model
            self._model = None
            logger.info("Llama model unloaded")

    @property
    def loaded(self) -> bool:
        """Whether the model is currently loaded in memory."""
        return self._model is not None

    def __del__(self):
        """Cleanup on garbage collection."""
        self.close()

    def __repr__(self) -> str:
        status = "loaded" if self.loaded else "unloaded"
        return f"LlamaEngine(model={self.model_path.name}, {status})"
