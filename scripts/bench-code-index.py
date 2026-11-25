#!/usr/bin/env python3
"""Benchmark the code_index indexer at different project scales.

Usage:
    python3 scripts/bench-code-index.py                    # default: 100, 500, 1000 files
    python3 scripts/bench-code-index.py --scales 50,200    # custom scales
    python3 scripts/bench-code-index.py --json             # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.code_index.index import CodeIndex


def generate_synthetic_project(root: Path, num_files: int) -> dict:
    """Generate a synthetic Python project with ~5 symbols per file.

    Each file contains 1 class with 3 methods + 1 standalone function.
    Subdirectories created per 50 files (realistic structure).
    """
    dirs = [root]
    for i in range(num_files // 50 + 1):
        sub = root / f"pkg_{i}"
        sub.mkdir(exist_ok=True)
        (sub / "__init__.py").write_text("")
        dirs.append(sub)

    for i in range(num_files):
        target_dir = dirs[i % len(dirs)]
        (target_dir / f"mod_{i}.py").write_text(f'''
"""Module {i} docstring."""


class Widget{i}(BaseWidget):
    """Widget number {i}."""

    def process(self, data):
        """Process input data."""
        return data

    def validate(self, value):
        """Validate a value."""
        return bool(value)

    def serialize(self):
        """Serialize to dict."""
        return {{"id": {i}}}


def helper_{i}(x, y=0):
    """Helper function {i}."""
    return x + y
''')

    return {"files": num_files, "est_symbols": num_files * 5}


def run_benchmark(num_files: int) -> dict:
    """Run a single benchmark at the given scale."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        generate_synthetic_project(root, num_files)

        # Full index (cold)
        tracemalloc.start()
        idx = CodeIndex(db_path=":memory:")
        stats = idx.index_project(str(root), project="bench", force=True)
        mem_current, mem_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        timing = stats.get("timing", {})

        # Incremental reindex (warm -- everything skipped)
        t0 = time.perf_counter()
        stats_incr = idx.index_project(str(root), project="bench")
        incr_time = time.perf_counter() - t0

        # Query benchmarks
        query_results = {}

        # Lookup
        t0 = time.perf_counter()
        for i in range(min(100, num_files)):
            idx.lookup(f"Widget{i}", "bench")
        query_results["lookup_100_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # FTS search
        t0 = time.perf_counter()
        for i in range(min(50, num_files)):
            idx.search_fts(f"Widget{i}", "bench")
        query_results["fts_50_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        # Hierarchy
        t0 = time.perf_counter()
        for i in range(min(50, num_files)):
            idx.hierarchy(f"Widget{i}", "bench")
        query_results["hierarchy_50_ms"] = round((time.perf_counter() - t0) * 1000, 1)

        idx.close()

        return {
            "scale": num_files,
            "full_index": {
                "total_s": timing.get("total_s", 0),
                "hashing_s": timing.get("hashing_s", 0),
                "incremental_check_s": timing.get("incremental_check_s", 0),
                "ast_extraction_s": timing.get("ast_extraction_s", 0),
                "db_insert_s": timing.get("db_insert_s", 0),
                "cleanup_s": timing.get("cleanup_s", 0),
                "files_per_sec": timing.get("files_per_sec", 0),
                "symbols_per_sec": timing.get("symbols_per_sec", 0),
            },
            "incremental_reindex_s": round(incr_time, 4),
            "incremental_skipped": stats_incr["files_skipped"],
            "queries": query_results,
            "memory_peak_mb": round(mem_peak / (1024 * 1024), 1),
            "symbols_total": stats["symbols_added"],
        }


def main():
    parser = argparse.ArgumentParser(description="Benchmark code_index")
    parser.add_argument("--scales", default="100,500,1000",
                        help="Comma-separated file counts (default: 100,500,1000)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    scales = [int(s) for s in args.scales.split(",")]
    results = []

    for scale in scales:
        result = run_benchmark(scale)
        results.append(result)

        if not args.json:
            print(f"\n=== {scale} files ({result['symbols_total']} symbols) ===")
            fi = result["full_index"]
            print(f"  Full index:     {fi['total_s']:.3f}s "
                  f"({fi['files_per_sec']:.0f} files/s, "
                  f"{fi['symbols_per_sec']:.0f} sym/s)")
            print(f"    Hashing:      {fi['hashing_s']:.3f}s")
            print(f"    Incr check:   {fi['incremental_check_s']:.3f}s")
            print(f"    AST extract:  {fi['ast_extraction_s']:.3f}s")
            print(f"    DB insert:    {fi['db_insert_s']:.3f}s")
            print(f"    Cleanup:      {fi['cleanup_s']:.3f}s")
            print(f"  Incremental:    {result['incremental_reindex_s']:.3f}s "
                  f"({result['incremental_skipped']} skipped)")
            q = result["queries"]
            print(f"  Lookup x100:    {q['lookup_100_ms']:.1f}ms")
            print(f"  FTS x50:        {q['fts_50_ms']:.1f}ms")
            print(f"  Hierarchy x50:  {q['hierarchy_50_ms']:.1f}ms")
            print(f"  Memory peak:    {result['memory_peak_mb']:.1f}MB")

    if args.json:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
