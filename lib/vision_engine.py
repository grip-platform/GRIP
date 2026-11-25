#!/usr/bin/env python3
"""
vision_engine.py -- Multimodal vision LLM engine for GRIP.

Mirrors LlamaEngine lifecycle pattern for image understanding.
Uses mlx-vlm on Apple Silicon for native Metal acceleration with
Llama 3.2 Vision models (mllama architecture).

Model: Llama 3.2 11B Vision Instruct (4-bit quantised via MLX).

Architecture note: VisionEngine is deliberately separate from LlamaEngine
(SRP). Vision models need a processor for image tokenisation and use
a fundamentally different inference pipeline from text-only models.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default HuggingFace model ID for Llama 3.2 Vision (4-bit MLX)
DEFAULT_MODEL_ID = "mlx-community/Llama-3.2-11B-Vision-Instruct-4bit"

DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.1


class VisionEngine:
    """Multimodal vision LLM engine for image understanding.

    Handles:
        - MLX model loading from HuggingFace (auto-downloads on first use)
        - Image-to-text extraction via mlx-vlm generate
        - Temporary file management for image bytes
        - Resource management (lazy load, explicit unload)

    Lifecycle mirrors LlamaEngine:
        engine = VisionEngine()
        engine.connect()
        text = engine.describe_image(image_bytes)
        engine.close()
    """

    def __init__(
        self,
        model_id: Optional[str] = None,
    ):
        """Initialise engine with model identifier.

        Args:
            model_id: HuggingFace model ID or local path.
                      Defaults to mlx-community/Llama-3.2-11B-Vision-Instruct-4bit.
        """
        self.model_id = model_id or DEFAULT_MODEL_ID
        self._model = None
        self._processor = None
        self._config = None

    def connect(self):
        """Load vision model if not already loaded (lazy singleton).

        Downloads from HuggingFace on first use (~6GB). Subsequent
        calls use the cached model.

        Returns:
            Tuple of (model, processor).

        Raises:
            ImportError: If mlx-vlm is not installed.
        """
        if self._model is not None:
            return self._model

        try:
            from mlx_vlm import load
            from mlx_vlm.utils import load_config
        except ImportError:
            raise ImportError(
                "mlx-vlm not installed. "
                "Run: pip install mlx-vlm"
            )

        logger.info("Loading vision model: %s", self.model_id)
        self._model, self._processor = load(self.model_id)
        self._config = load_config(self.model_id)
        logger.info("Vision model loaded successfully")
        return self._model

    def describe_image(
        self,
        image_data: bytes,
        prompt: str = "Extract all text from this image. Preserve the original structure, paragraphs, and formatting as closely as possible.",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        image_format: str = "png",
    ) -> str:
        """Extract text or describe an image using the vision model.

        Writes image bytes to a temp file (mlx-vlm expects file paths),
        runs inference, then cleans up.

        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.).
            prompt: Instruction for the model. Defaults to text extraction.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            image_format: File extension (png, jpeg, etc.).

        Returns:
            Extracted text or image description.

        Raises:
            ImportError: If mlx-vlm not installed (via connect()).
            RuntimeError: If inference fails.
        """
        self.connect()

        try:
            from mlx_vlm import generate
            from mlx_vlm.prompt_utils import apply_chat_template
        except ImportError:
            raise ImportError("mlx-vlm not installed. Run: pip install mlx-vlm")

        # mlx-vlm expects image file paths, not raw bytes
        with tempfile.NamedTemporaryFile(
            suffix=f".{image_format}", delete=True
        ) as tmp:
            tmp.write(image_data)
            tmp.flush()

            try:
                formatted_prompt = apply_chat_template(
                    self._processor, self._config, prompt, num_images=1
                )
                result = generate(
                    self._model,
                    self._processor,
                    formatted_prompt,
                    image=[tmp.name],
                    max_tokens=max_tokens,
                    temperature=temperature,
                    verbose=False,
                )
                # result is a GenerationResult with .text attribute
                text = result.text if hasattr(result, "text") else str(result)
                return text if text else ""
            except Exception as e:
                raise RuntimeError(f"Vision inference failed: {e}") from e

    def close(self):
        """Unload model and free memory."""
        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None
        if self._config is not None:
            self._config = None
            logger.info("Vision model unloaded")

    @property
    def loaded(self) -> bool:
        """Whether the vision model is currently loaded in memory."""
        return self._model is not None

    def __del__(self):
        """Cleanup on garbage collection."""
        self.close()

    def __repr__(self) -> str:
        status = "loaded" if self.loaded else "unloaded"
        return f"VisionEngine(model={self.model_id}, {status})"
