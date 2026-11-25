#!/usr/bin/env python3
"""
pdf_extractor.py -- PDF page-to-image extraction for GRIP vision pipeline.

Information Expert: owns PDF-to-image conversion because it has the PDF data.
Uses PyMuPDF (fitz) to render each page as a PNG at configurable DPI.

Generator pattern: extract_pages() yields one PageImage at a time for O(1)
memory per page. Critical for processing 1GB+ PDF corpora without OOM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

DEFAULT_DPI = 200  # Balance between quality and speed


@dataclass
class PageImage:
    """Single rendered page from a PDF.

    Attributes:
        page_number: 1-based page number.
        image_data: Raw PNG bytes.
        width: Image width in pixels.
        height: Image height in pixels.
        source_file: Path to the source PDF.
    """
    page_number: int
    image_data: bytes
    width: int
    height: int
    source_file: Path = field(default_factory=lambda: Path())


def extract_pages(
    pdf_path: Path,
    dpi: int = DEFAULT_DPI,
    max_pages: Optional[int] = None,
    start_page: int = 1,
) -> Generator[PageImage, None, None]:
    """Yield page images from a PDF file.

    Renders each page as a PNG at the specified DPI using PyMuPDF.
    Generator pattern ensures O(1) memory per page -- only one
    page's image data is in memory at a time.

    Args:
        pdf_path: Path to the PDF file.
        dpi: Render resolution. 200 DPI is good for OCR/vision.
        max_pages: Maximum pages to extract (None = all).
        start_page: 1-based starting page number.

    Yields:
        PageImage for each rendered page.

    Raises:
        FileNotFoundError: If PDF file does not exist.
        ImportError: If PyMuPDF is not installed.
        RuntimeError: If PDF cannot be opened or page cannot be rendered.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF not installed. Run: pip install PyMuPDF"
        )

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        raise RuntimeError(f"Cannot open PDF: {pdf_path}: {e}") from e

    total_pages = len(doc)
    start_idx = max(0, start_page - 1)
    end_idx = total_pages if max_pages is None else min(total_pages, start_idx + max_pages)

    logger.info(
        "Extracting pages %d-%d from %s (%d total, %d DPI)",
        start_idx + 1, end_idx, pdf_path.name, total_pages, dpi,
    )

    zoom = dpi / 72.0  # PDF default is 72 DPI
    matrix = fitz.Matrix(zoom, zoom)

    try:
        for page_idx in range(start_idx, end_idx):
            page = doc[page_idx]
            pixmap = page.get_pixmap(matrix=matrix)
            image_data = pixmap.tobytes("png")

            yield PageImage(
                page_number=page_idx + 1,
                image_data=image_data,
                width=pixmap.width,
                height=pixmap.height,
                source_file=pdf_path,
            )
    finally:
        doc.close()


def get_page_count(pdf_path: Path) -> int:
    """Return the total number of pages in a PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Number of pages.

    Raises:
        FileNotFoundError: If PDF does not exist.
        ImportError: If PyMuPDF is not installed.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import fitz
    except ImportError:
        raise ImportError("PyMuPDF not installed. Run: pip install PyMuPDF")

    doc = fitz.open(str(pdf_path))
    count = len(doc)
    doc.close()
    return count
