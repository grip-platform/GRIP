#!/usr/bin/env python3
"""Benchmark liteparse vs PyMuPDF on a synthetic digital PDF (no client data).

Tests hypothesis H867: ``LiteParse(ocr_enabled=False)`` parses a ~450-page
digital PDF in under 2s and recovers text comparable to PyMuPDF (char ratio
0.9-1.1), validating the claimed "450p / 0.7s" order-of-magnitude.

A SYNTHETIC fixture is used deliberately — real client legal PDFs must never
enter the GRIP repo (rules/no-sensitive-info-in-grip.md). This measures the
parser, not a client document.

Usage:
    python3 scripts/benchmark_liteparse_vs_pymupdf.py [--pages 450] [--runs 1]
"""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from pathlib import Path

# Public, legal-shaped filler — NOT a real contract, NOT client data.
_CLAUSE = (
    "Clause. The party of the first part shall, subject to the terms herein, "
    "indemnify and hold harmless the party of the second part against any and "
    "all claims arising from the performance or non-performance of the "
    "obligations set out in this agreement, provided that notice is given "
    "within thirty (30) days of the event giving rise to such claim. "
)


def build_fixture(pages: int, path: Path) -> Path:
    """Build a `pages`-page digital PDF of known text via PyMuPDF."""
    import fitz

    doc = fitz.open()
    body = "PAGE {n} OF {tot}\n\n" + "\n\n".join(_CLAUSE for _ in range(5))
    # insert_textbox WRAPS text inside the rect, so every char lands on-page.
    # insert_text does NOT wrap → long lines run off-canvas and parsers then
    # disagree on what's "on the page", making char-recall a fixture artefact.
    rect = fitz.Rect(54, 54, 558, 788)
    for i in range(pages):
        page = doc.new_page()
        page.insert_textbox(rect, body.format(n=i + 1, tot=pages), fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


def time_liteparse(path: Path) -> tuple[float, int, int]:
    """Return (seconds, char_count, num_pages) for LiteParse digital fast path."""
    from liteparse import LiteParse

    parser = LiteParse(ocr_enabled=False, quiet=True)
    t0 = time.perf_counter()
    result = parser.parse(str(path))
    elapsed = time.perf_counter() - t0
    return elapsed, len(result.text), result.num_pages


def time_pymupdf(path: Path) -> tuple[float, int, int]:
    """Return (seconds, char_count, num_pages) for the PyMuPDF text path."""
    import fitz

    t0 = time.perf_counter()
    doc = fitz.open(str(path))
    text = "".join(page.get_text() for page in doc)
    num_pages = doc.page_count
    elapsed = time.perf_counter() - t0
    doc.close()
    return elapsed, len(text), num_pages


def _median_run(fn, path: Path, runs: int) -> tuple[float, int, int]:
    samples = [fn(path) for _ in range(runs)]
    secs = statistics.median(s[0] for s in samples)
    return secs, samples[0][1], samples[0][2]


def run(pages: int, runs: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = build_fixture(pages, Path(tmp) / "synthetic.pdf")
        lp_secs, lp_chars, lp_pages = _median_run(time_liteparse, fixture, runs)
        mu_secs, mu_chars, mu_pages = _median_run(time_pymupdf, fixture, runs)

    char_ratio = lp_chars / mu_chars if mu_chars else 0.0
    speedup = mu_secs / lp_secs if lp_secs else 0.0
    return {
        "pages": pages,
        "runs": runs,
        "liteparse_seconds": round(lp_secs, 4),
        "pymupdf_seconds": round(mu_secs, 4),
        "liteparse_chars": lp_chars,
        "pymupdf_chars": mu_chars,
        "liteparse_pages": lp_pages,
        "pymupdf_pages": mu_pages,
        "char_ratio_lp_over_mu": round(char_ratio, 4),
        "pymupdf_speedup_vs_liteparse": round(speedup, 2),
        # H867 prediction: liteparse < 2s AND 0.9 <= char_ratio <= 1.1
        "h867_confirmed": bool(lp_secs < 2.0 and 0.9 <= char_ratio <= 1.1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=int, default=450)
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args(argv)

    r = run(args.pages, args.runs)
    print(f"=== liteparse vs PyMuPDF — {r['pages']}p synthetic, median of {r['runs']} ===")
    print(f"  liteparse(ocr=False): {r['liteparse_seconds']}s  "
          f"({r['liteparse_chars']} chars, {r['liteparse_pages']} pages)")
    print(f"  PyMuPDF text path   : {r['pymupdf_seconds']}s  "
          f"({r['pymupdf_chars']} chars, {r['pymupdf_pages']} pages)")
    print(f"  char ratio (lp/mu)  : {r['char_ratio_lp_over_mu']}")
    print(f"  PyMuPDF speedup     : {r['pymupdf_speedup_vs_liteparse']}x")
    print(f"  H867 (<2s & 0.9-1.1 char ratio): "
          f"{'CONFIRMED' if r['h867_confirmed'] else 'FALSIFIED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
