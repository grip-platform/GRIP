#!/usr/bin/env python3
"""
pdf_batch_processor.py -- Batch PDF extraction controller for GRIP.

Controller (GRASP): orchestrates the extraction use case by dispatching
to the appropriate method (vision-llm, tesseract, or API) and recording
benchmarks. Does not own PDF or vision logic -- delegates to experts.

Data flow:
    PDF -> pdf_extractor.extract_pages() -> method dispatcher -> ExtractionResult
                                                               -> pdf_benchmark.record_benchmark()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lib.pdf_benchmark import (
    BenchmarkEntry,
    BenchmarkTimer,
    record_benchmark,
    estimate_api_cost,
    DEFAULT_BENCHMARK_FILE,
)
from lib.pdf_extractor import extract_pages, get_page_count, PageImage
from lib.hal_model_resolver import resolve as _resolve_model

logger = logging.getLogger(__name__)

VALID_METHODS = ("vision-llm", "tesseract", "claude-haiku", "claude-sonnet")


@dataclass
class ExtractionResult:
    """Result from extracting text from a single PDF page."""
    page_number: int
    text: str
    method: str
    elapsed_seconds: float
    cost_usd: float = 0.0
    error: Optional[str] = None


@dataclass
class FileResult:
    """Aggregated result from processing an entire PDF file."""
    file_path: Path
    method: str
    pages: list[ExtractionResult] = field(default_factory=list)
    total_time_seconds: float = 0.0
    total_cost_usd: float = 0.0

    @property
    def full_text(self) -> str:
        """Concatenate all page texts with page breaks."""
        return "\n\n--- Page Break ---\n\n".join(
            r.text for r in self.pages if r.text
        )

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def error_count(self) -> int:
        return sum(1 for p in self.pages if p.error)


class BatchProcessor:
    """Orchestrates PDF text extraction across multiple methods.

    Dispatches each page to the selected extraction method,
    records benchmarks, and aggregates results.

    Usage:
        processor = BatchProcessor(method="vision-llm")
        result = processor.process_file(Path("document.pdf"))
        print(result.full_text)
    """

    def __init__(
        self,
        method: str = "vision-llm",
        dpi: int = 200,
        max_pages: Optional[int] = None,
        benchmark_file: Path = DEFAULT_BENCHMARK_FILE,
        vision_engine=None,
    ):
        """Initialise the batch processor.

        Args:
            method: Extraction method (vision-llm, tesseract, claude-haiku, claude-sonnet).
            dpi: PDF render resolution for page images.
            max_pages: Maximum pages to process per file.
            benchmark_file: Path to JSONL benchmark file.
            vision_engine: Pre-initialised VisionEngine (lazy-created if None).
        """
        if method not in VALID_METHODS:
            raise ValueError(f"Unknown method: {method}. Valid: {VALID_METHODS}")

        self.method = method
        self.dpi = dpi
        self.max_pages = max_pages
        self.benchmark_file = benchmark_file
        self._vision_engine = vision_engine

    def process_file(
        self,
        pdf_path: Path,
        record_benchmarks: bool = True,
    ) -> FileResult:
        """Extract text from all pages of a PDF file.

        Args:
            pdf_path: Path to the PDF file.
            record_benchmarks: Whether to record timing/cost data.

        Returns:
            FileResult with extracted text and metrics.
        """
        pdf_path = Path(pdf_path)
        result = FileResult(file_path=pdf_path, method=self.method)

        for page_image in extract_pages(pdf_path, dpi=self.dpi, max_pages=self.max_pages):
            extraction = self._extract_page(page_image)
            result.pages.append(extraction)
            result.total_time_seconds += extraction.elapsed_seconds
            result.total_cost_usd += extraction.cost_usd

            if record_benchmarks:
                record_benchmark(
                    BenchmarkEntry(
                        method=self.method,
                        file_name=pdf_path.name,
                        page_number=extraction.page_number,
                        elapsed_seconds=extraction.elapsed_seconds,
                        char_count=len(extraction.text),
                        cost_usd=extraction.cost_usd,
                        error=extraction.error,
                    ),
                    self.benchmark_file,
                )

            logger.info(
                "Page %d: %d chars in %.2fs (%s)",
                extraction.page_number,
                len(extraction.text),
                extraction.elapsed_seconds,
                self.method,
            )

        return result

    def process_directory(
        self,
        dir_path: Path,
        pattern: str = "*.pdf",
        record_benchmarks: bool = True,
    ) -> list[FileResult]:
        """Process all PDF files in a directory.

        Args:
            dir_path: Directory containing PDF files.
            pattern: Glob pattern for PDF files.
            record_benchmarks: Whether to record timing/cost data.

        Returns:
            List of FileResult objects.
        """
        dir_path = Path(dir_path)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {dir_path}")

        pdf_files = sorted(dir_path.glob(pattern))
        if not pdf_files:
            logger.warning("No PDF files found in %s matching %s", dir_path, pattern)
            return []

        results = []
        for pdf_file in pdf_files:
            logger.info("Processing: %s", pdf_file.name)
            result = self.process_file(pdf_file, record_benchmarks=record_benchmarks)
            results.append(result)

        return results

    def _extract_page(self, page_image: PageImage) -> ExtractionResult:
        """Dispatch a single page to the configured extraction method."""
        if self.method == "vision-llm":
            return self._extract_with_vision(page_image)
        elif self.method == "tesseract":
            return self._extract_with_tesseract(page_image)
        elif self.method.startswith("claude-"):
            return self._extract_with_api(page_image)
        else:
            return ExtractionResult(
                page_number=page_image.page_number,
                text="",
                method=self.method,
                elapsed_seconds=0.0,
                error=f"Unknown method: {self.method}",
            )

    def _extract_with_vision(self, page_image: PageImage) -> ExtractionResult:
        """Extract text using local Llama 3.2 Vision 11B."""
        if self._vision_engine is None:
            from lib.vision_engine import VisionEngine
            self._vision_engine = VisionEngine()

        with BenchmarkTimer() as timer:
            try:
                text = self._vision_engine.describe_image(page_image.image_data)
            except Exception as e:
                return ExtractionResult(
                    page_number=page_image.page_number,
                    text="",
                    method="vision-llm",
                    elapsed_seconds=timer.elapsed,
                    error=str(e),
                )

        return ExtractionResult(
            page_number=page_image.page_number,
            text=text,
            method="vision-llm",
            elapsed_seconds=timer.elapsed,
            cost_usd=0.0,  # Local = free
        )

    def _extract_with_tesseract(self, page_image: PageImage) -> ExtractionResult:
        """Extract text using Tesseract OCR."""
        with BenchmarkTimer() as timer:
            try:
                import pytesseract
                from PIL import Image
                import io

                image = Image.open(io.BytesIO(page_image.image_data))
                text = pytesseract.image_to_string(image)
            except Exception as e:
                return ExtractionResult(
                    page_number=page_image.page_number,
                    text="",
                    method="tesseract",
                    elapsed_seconds=timer.elapsed,
                    error=str(e),
                )

        return ExtractionResult(
            page_number=page_image.page_number,
            text=text,
            method="tesseract",
            elapsed_seconds=timer.elapsed,
            cost_usd=0.0,
        )

    def _extract_with_api(self, page_image: PageImage) -> ExtractionResult:
        """Extract text using Claude API (Haiku or Sonnet)."""
        import base64

        # Resolve tier ids from HAL's canonical catalogue — the pinned
        # -20251001 / -20260515 date suffixes are the fastest-rotting form
        # of baked literal. Fail-safe to a current bare id, never raises.
        model_map = {
            "claude-haiku": _resolve_model(
                "anthropic/claude-haiku-", "claude-haiku-4-5"
            ),
            "claude-sonnet": _resolve_model(
                "anthropic/claude-sonnet-", "claude-sonnet-4-6"
            ),
        }
        model_id = model_map.get(
            self.method,
            _resolve_model("anthropic/claude-haiku-", "claude-haiku-4-5"),
        )

        with BenchmarkTimer() as timer:
            try:
                import anthropic

                client = anthropic.Anthropic()
                b64_image = base64.b64encode(page_image.image_data).decode("utf-8")

                message = client.messages.create(
                    model=model_id,
                    max_tokens=2048,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": b64_image,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": "Extract all text from this image. Preserve the original structure, paragraphs, and formatting as closely as possible.",
                                },
                            ],
                        }
                    ],
                )
                text = message.content[0].text
                cost = estimate_api_cost(self.method, 1)
            except Exception as e:
                return ExtractionResult(
                    page_number=page_image.page_number,
                    text="",
                    method=self.method,
                    elapsed_seconds=timer.elapsed,
                    error=str(e),
                )

        return ExtractionResult(
            page_number=page_image.page_number,
            text=text,
            method=self.method,
            elapsed_seconds=timer.elapsed,
            cost_usd=cost,
        )

    def close(self):
        """Release resources (vision engine, etc.)."""
        if self._vision_engine is not None:
            self._vision_engine.close()
            self._vision_engine = None


if __name__ == "__main__":
    import argparse
    import sys

    from lib.pdf_benchmark import compare_methods, format_comparison

    parser = argparse.ArgumentParser(
        description="PDF text extraction pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m lib.pdf_batch_processor --method vision-llm --input doc.pdf
  python3 -m lib.pdf_batch_processor --method tesseract --input corpus/
  python3 -m lib.pdf_batch_processor --benchmark
        """,
    )
    parser.add_argument("--method", choices=VALID_METHODS, default="vision-llm")
    parser.add_argument("--input", type=Path, help="PDF file or directory")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--benchmark", action="store_true", help="Show benchmark comparison")
    parser.add_argument("--output", type=Path, help="Write extracted text to file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.benchmark:
        summary = compare_methods()
        print(format_comparison(summary))
        sys.exit(0)

    if not args.input:
        parser.error("--input required (PDF file or directory)")

    processor = BatchProcessor(
        method=args.method,
        dpi=args.dpi,
        max_pages=args.max_pages,
    )

    try:
        if args.input.is_dir():
            results = processor.process_directory(args.input)
            for r in results:
                print(f"\n=== {r.file_path.name} ({r.page_count} pages, {r.total_time_seconds:.1f}s) ===")
                print(r.full_text[:500] + "..." if len(r.full_text) > 500 else r.full_text)
        else:
            result = processor.process_file(args.input)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(result.full_text)
                print(f"Wrote {len(result.full_text)} chars to {args.output}")
            else:
                print(result.full_text)
            print(f"\n--- {result.page_count} pages, {result.total_time_seconds:.1f}s, ${result.total_cost_usd:.4f} ---")
    finally:
        processor.close()
