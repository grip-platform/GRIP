#!/usr/bin/env python3
"""
pdf_benchmark.py -- Benchmark recording and comparison for PDF extraction methods.

Pure Fabrication (GRASP): exists solely to track timing, cost, and quality
metrics across different extraction approaches. No domain knowledge of
PDF processing itself.

Records benchmarks as JSONL for append-only durability and provides
tabular comparison output.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_BENCHMARK_FILE = CLAUDE_DIR / "data" / "pdf-benchmarks.jsonl"

# Cost per input token for API methods (March 2026 pricing)
API_COSTS = {
    "claude-haiku": 0.80 / 1_000_000,    # $0.80/MTok input
    "claude-sonnet": 3.00 / 1_000_000,   # $3.00/MTok input
    "claude-opus": 15.00 / 1_000_000,    # $15.00/MTok input
}

# Average tokens per page image (base64 encoded, ~200 DPI A4)
ESTIMATED_TOKENS_PER_PAGE = 1600


@dataclass
class BenchmarkEntry:
    """Single benchmark measurement for one extraction."""
    method: str                    # vision-llm, tesseract, claude-haiku, etc.
    file_name: str
    page_number: int
    elapsed_seconds: float
    char_count: int                # Characters in extracted text
    cost_usd: float = 0.0         # $0 for local, token-based for API
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: Optional[str] = None


def record_benchmark(
    entry: BenchmarkEntry,
    benchmark_file: Path = DEFAULT_BENCHMARK_FILE,
) -> None:
    """Append a benchmark entry to the JSONL file.

    Args:
        entry: The benchmark measurement to record.
        benchmark_file: Path to the JSONL file.
    """
    benchmark_file.parent.mkdir(parents=True, exist_ok=True)
    with open(benchmark_file, "a") as f:
        f.write(json.dumps(asdict(entry)) + "\n")


def load_benchmarks(
    benchmark_file: Path = DEFAULT_BENCHMARK_FILE,
    method: Optional[str] = None,
) -> list[BenchmarkEntry]:
    """Load benchmark entries from JSONL file.

    Args:
        benchmark_file: Path to the JSONL file.
        method: Optional filter by method name.

    Returns:
        List of BenchmarkEntry objects.
    """
    if not benchmark_file.exists():
        return []

    entries = []
    with open(benchmark_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if method and data.get("method") != method:
                continue
            entries.append(BenchmarkEntry(**data))
    return entries


def compare_methods(
    benchmark_file: Path = DEFAULT_BENCHMARK_FILE,
) -> dict[str, dict]:
    """Aggregate benchmarks by method and produce comparison summary.

    Returns:
        Dict keyed by method name, each containing:
        - total_pages: number of pages processed
        - total_time_s: total elapsed seconds
        - avg_time_per_page_s: average time per page
        - total_cost_usd: total cost
        - avg_cost_per_page_usd: average cost per page
        - avg_chars_per_page: average characters extracted
        - errors: number of failed extractions
        - cost_per_10k_pages_usd: projected cost for 10,000 pages
    """
    entries = load_benchmarks(benchmark_file)
    if not entries:
        return {}

    methods: dict[str, list[BenchmarkEntry]] = {}
    for entry in entries:
        methods.setdefault(entry.method, []).append(entry)

    summary = {}
    for method, method_entries in methods.items():
        successful = [e for e in method_entries if e.error is None]
        errors = len(method_entries) - len(successful)
        total_time = sum(e.elapsed_seconds for e in successful)
        total_cost = sum(e.cost_usd for e in successful)
        total_chars = sum(e.char_count for e in successful)
        n = len(successful) or 1  # Avoid division by zero

        summary[method] = {
            "total_pages": len(successful),
            "total_time_s": round(total_time, 2),
            "avg_time_per_page_s": round(total_time / n, 2),
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_page_usd": round(total_cost / n, 6),
            "avg_chars_per_page": round(total_chars / n),
            "errors": errors,
            "cost_per_10k_pages_usd": round((total_cost / n) * 10_000, 2),
        }

    return summary


def format_comparison(summary: dict[str, dict]) -> str:
    """Format comparison summary as a readable table.

    Args:
        summary: Output from compare_methods().

    Returns:
        Formatted string with comparison table.
    """
    if not summary:
        return "No benchmark data available."

    header = (
        f"{'Method':<20} {'Pages':>6} {'Avg s/pg':>9} "
        f"{'Avg $/pg':>10} {'$/10K pg':>10} {'Avg chars':>10} {'Errors':>7}"
    )
    separator = "-" * len(header)
    lines = [header, separator]

    for method, data in sorted(summary.items()):
        lines.append(
            f"{method:<20} {data['total_pages']:>6} "
            f"{data['avg_time_per_page_s']:>9.2f} "
            f"{data['avg_cost_per_page_usd']:>10.6f} "
            f"{data['cost_per_10k_pages_usd']:>10.2f} "
            f"{data['avg_chars_per_page']:>10} "
            f"{data['errors']:>7}"
        )

    return "\n".join(lines)


def estimate_api_cost(method: str, num_pages: int) -> float:
    """Estimate API cost for a given number of pages.

    Args:
        method: API method name (must be in API_COSTS).
        num_pages: Number of pages to process.

    Returns:
        Estimated cost in USD. Returns 0.0 for local methods.
    """
    cost_per_token = API_COSTS.get(method, 0.0)
    return cost_per_token * ESTIMATED_TOKENS_PER_PAGE * num_pages


class BenchmarkTimer:
    """Context manager for timing extraction operations."""

    def __init__(self):
        self.elapsed: float = 0.0
        self._start: float = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args):
        self.elapsed = time.perf_counter() - self._start


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--compare":
        benchmark_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_BENCHMARK_FILE
        summary = compare_methods(benchmark_path)
        print(format_comparison(summary))
    else:
        print("Usage: python3 lib/pdf_benchmark.py --compare [benchmark_file]")
