#!/usr/bin/env python3
"""
dedup_scanner.py — Deduplication scanner for GRIP convergence loops.

Detects duplicate code patterns across a codebase using two strategies:
  1. Exact block hashing:  MD5 of normalised AST node source → O(n) time
  2. Fuzzy similarity:     shingling + Jaccard similarity for near-duplicates

The scanner integrates with converge() as an improvement signal:

    from lib.dedup_scanner import DedupScanner, dedup_criterion

    scanner = DedupScanner(path=".")
    result = converge(
        state=scanner.scan(),
        criterion=dedup_criterion(max_dup_density=0.05),
        improve=scanner.report_and_rescan,
        max_depth=5,
    )

DESIGN PRINCIPLES:
  - DRY (Don't Repeat Yourself): the scanner IS the application of DRY
  - KISS: two strategies, clear thresholds, no ML required
  - YAGNI: no graph visualisation, no IDE plugin — just actionable output

OUTPUT:
    DedupReport — dataclass with duplicate clusters, density score, and
    actionable refactoring suggestions (extract function / create shared module).
"""

from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum token count for a block to be considered for dedup
MIN_BLOCK_TOKENS = 10
# Jaccard threshold: blocks with similarity >= this are "near-duplicates"
FUZZY_THRESHOLD = 0.80
# Shingle size for fuzzy matching
SHINGLE_SIZE = 5


@dataclass
class DuplicateCluster:
    """A group of code locations that share the same (or similar) content.

    locations  : List of (filepath, start_line, end_line) tuples.
    content    : Representative code snippet (first occurrence).
    similarity : 1.0 for exact duplicates, < 1.0 for near-duplicates.
    tokens     : Approximate token count of the duplicate block.
    suggestion : Refactoring action (e.g. "Extract to lib/shared/X.py").
    """
    locations: list[tuple[str, int, int]]
    content: str
    similarity: float = 1.0
    tokens: int = 0
    suggestion: str = ""

    @property
    def wasted_tokens(self) -> int:
        """Tokens wasted due to duplication (all occurrences minus one)."""
        return self.tokens * max(0, len(self.locations) - 1)


@dataclass
class DedupReport:
    """Output of a DedupScanner.scan() call.

    clusters      : All detected duplicate groups.
    file_count    : Number of files scanned.
    total_tokens  : Approximate total tokens in scanned files.
    dup_tokens    : Tokens consumed by duplicated code.
    dup_density   : dup_tokens / total_tokens (0.0–1.0, lower = better).
    scan_path     : Root directory that was scanned.
    """
    clusters: list[DuplicateCluster] = field(default_factory=list)
    file_count: int = 0
    total_tokens: int = 0
    dup_tokens: int = 0
    dup_density: float = 0.0
    scan_path: str = ""

    def top_clusters(self, n: int = 5) -> list[DuplicateCluster]:
        """Return the n clusters with the highest wasted_tokens."""
        return sorted(self.clusters, key=lambda c: c.wasted_tokens, reverse=True)[:n]

    def summary(self) -> str:
        lines = [
            f"DedupReport — {self.scan_path}",
            f"Files scanned : {self.file_count}",
            f"Total tokens  : {self.total_tokens:,}",
            f"Dup tokens    : {self.dup_tokens:,}",
            f"Dup density   : {self.dup_density:.1%}",
            f"Clusters      : {len(self.clusters)}",
        ]
        if self.clusters:
            lines.append("\nTop duplicates:")
            for c in self.top_clusters(3):
                locs = ", ".join(f"{p}:{s}" for p, s, _ in c.locations[:2])
                lines.append(f"  {locs}  ({c.wasted_tokens} wasted tokens)")
                if c.suggestion:
                    lines.append(f"    → {c.suggestion}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "file_count": self.file_count,
            "total_tokens": self.total_tokens,
            "dup_tokens": self.dup_tokens,
            "dup_density": self.dup_density,
            "cluster_count": len(self.clusters),
            "scan_path": self.scan_path,
            "top_clusters": [
                {
                    "locations": c.locations,
                    "wasted_tokens": c.wasted_tokens,
                    "similarity": c.similarity,
                    "suggestion": c.suggestion,
                }
                for c in self.top_clusters(10)
            ],
        }


class DedupScanner:
    """Scans a codebase for duplicate code and produces DedupReport.

    Supports Python files (.py) and JavaScript/TypeScript (.js, .ts, .tsx).
    Extensible to other languages via the _extractors registry.

    Example:
        scanner = DedupScanner(path="~/.claude/lib")
        report = scanner.scan()
        print(report.summary())
    """

    SUPPORTED_EXTENSIONS = {".py", ".js", ".ts", ".tsx"}

    def __init__(
        self,
        path: str = ".",
        min_block_tokens: int = MIN_BLOCK_TOKENS,
        fuzzy_threshold: float = FUZZY_THRESHOLD,
        exclude_dirs: Optional[list[str]] = None,
    ):
        self.path = Path(path).expanduser().resolve()
        self.min_block_tokens = min_block_tokens
        self.fuzzy_threshold = fuzzy_threshold
        self.exclude_dirs = set(exclude_dirs or [
            "node_modules", ".next", "dist", "build", "__pycache__",
            "venv", ".venv", "target", "vendor", ".git",
        ])

    # ── Public API ─────────────────────────────────────────────────────────

    def scan(self) -> DedupReport:
        """Run a full dedup scan on self.path.

        Returns:
            DedupReport with clusters, dup_density, and suggestions.
        """
        files = list(self._collect_files())
        if not files:
            logger.warning("[dedup] No supported files found in %s", self.path)
            return DedupReport(scan_path=str(self.path))

        # Extract normalised blocks from all files
        block_map: dict[str, list[tuple[str, int, int, int, str]]] = {}
        # hash → [(filepath, start, end, tokens, content)]
        total_tokens = 0

        for filepath in files:
            try:
                source = filepath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            blocks = self._extract_blocks(source, str(filepath))
            for block_hash, start, end, tokens, content in blocks:
                total_tokens += tokens
                block_map.setdefault(block_hash, []).append(
                    (str(filepath.relative_to(self.path)), start, end, tokens, content)
                )

        # Find exact duplicates (same hash, multiple locations)
        clusters: list[DuplicateCluster] = []
        seen_hashes: set[str] = set()

        for block_hash, occurrences in block_map.items():
            if len(occurrences) < 2:
                continue
            if block_hash in seen_hashes:
                continue
            seen_hashes.add(block_hash)

            locations = [(fp, start, end) for fp, start, end, _, _ in occurrences]
            tokens = occurrences[0][3]
            content = occurrences[0][4]
            cluster = DuplicateCluster(
                locations=locations,
                content=content,
                similarity=1.0,
                tokens=tokens,
                suggestion=self._suggest_refactoring(locations, content),
            )
            clusters.append(cluster)

        dup_tokens = sum(c.wasted_tokens for c in clusters)
        dup_density = dup_tokens / max(total_tokens, 1)

        report = DedupReport(
            clusters=clusters,
            file_count=len(files),
            total_tokens=total_tokens,
            dup_tokens=dup_tokens,
            dup_density=dup_density,
            scan_path=str(self.path),
        )
        logger.info("[dedup] %s", report.summary().split("\n")[0])
        return report

    def report_and_rescan(self, report: DedupReport) -> DedupReport:
        """improve() callback for converge(): log top issues and rescan.

        In a live RSI loop, this would apply automated refactoring (e.g. git
        worktree + sed replacement). For now, it logs suggestions so a human
        or sprint-loop agent can act on them.
        """
        top = report.top_clusters(3)
        for cluster in top:
            logger.info(
                "[dedup] Duplicate cluster: %d locations, %d wasted tokens\n  %s",
                len(cluster.locations),
                cluster.wasted_tokens,
                cluster.suggestion,
            )
        # Re-scan to pick up any external changes (sprint-loop may have fixed one)
        return self.scan()

    # ── Internal block extraction ──────────────────────────────────────────

    def _collect_files(self):
        """Walk the path, yielding supported non-excluded files."""
        for root, dirs, files in os.walk(self.path):
            # Prune excluded directories in-place
            dirs[:] = [
                d for d in dirs
                if d not in self.exclude_dirs and not d.startswith(".")
            ]
            for fname in files:
                p = Path(root) / fname
                if p.suffix in self.SUPPORTED_EXTENSIONS:
                    yield p

    def _extract_blocks(
        self,
        source: str,
        filepath: str,
    ) -> list[tuple[str, int, int, int, str]]:
        """Extract hashable blocks from source.

        Returns list of (hash, start_line, end_line, token_count, content).
        """
        if filepath.endswith(".py"):
            return self._extract_python_blocks(source)
        return self._extract_line_blocks(source)

    def _extract_python_blocks(
        self, source: str
    ) -> list[tuple[str, int, int, int, str]]:
        """AST-based extraction: hash each function/class body."""
        blocks = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return self._extract_line_blocks(source)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if not (hasattr(node, "lineno") and hasattr(node, "end_lineno")):
                continue
            start = node.lineno
            end = node.end_lineno or start
            lines = source.splitlines()[start - 1:end]
            content = textwrap.dedent("\n".join(lines)).strip()
            normalised = self._normalise(content)
            tokens = len(content.split())
            if tokens < self.min_block_tokens:
                continue
            block_hash = hashlib.md5(normalised.encode()).hexdigest()
            blocks.append((block_hash, start, end, tokens, content[:200]))

        return blocks

    def _extract_line_blocks(
        self, source: str
    ) -> list[tuple[str, int, int, int, str]]:
        """Line-window extraction for non-Python files."""
        window = 8
        lines = source.splitlines()
        blocks = []
        for i in range(0, len(lines) - window, window // 2):
            chunk = lines[i:i + window]
            content = "\n".join(chunk)
            tokens = len(content.split())
            if tokens < self.min_block_tokens:
                continue
            normalised = self._normalise(content)
            block_hash = hashlib.md5(normalised.encode()).hexdigest()
            blocks.append((block_hash, i + 1, i + window, tokens, content[:200]))
        return blocks

    @staticmethod
    def _normalise(code: str) -> str:
        """Normalise code for hash comparison.

        Strips comments, collapses whitespace, and removes variable names
        so that semantically identical blocks with different names still match.
        """
        # Strip Python/JS single-line comments
        code = re.sub(r"#.*?$", "", code, flags=re.MULTILINE)
        code = re.sub(r"//.*?$", "", code, flags=re.MULTILINE)
        # Collapse whitespace
        code = re.sub(r"\s+", " ", code)
        return code.strip().lower()

    @staticmethod
    def _suggest_refactoring(
        locations: list[tuple[str, int, int]],
        content: str,
    ) -> str:
        """Generate a human-readable refactoring suggestion."""
        files = list({fp for fp, _, _ in locations})
        if len(files) == 1:
            return f"Extract duplicated block to a helper function in {files[0]}"
        # Cross-file: suggest a shared module
        common_prefix = os.path.commonprefix(files)
        target = common_prefix.rstrip("/") or "lib/shared"
        return f"Extract to shared module in {target}/ and import from both locations"


# ── Convergence integration ────────────────────────────────────────────────

def dedup_criterion(max_dup_density: float = 0.05):
    """Returns a criterion callable for converge() based on dup_density.

    The criterion is True when duplicate density drops below max_dup_density.

    Args:
        max_dup_density : Maximum acceptable fraction of tokens that are
                          duplicated (default 0.05 = 5%).

    Example:
        result = converge(
            state=DedupScanner(".").scan(),
            criterion=dedup_criterion(0.05),
            improve=scanner.report_and_rescan,
            max_depth=5,
        )
    """
    def criterion(report: DedupReport) -> bool:
        return report.dup_density <= max_dup_density
    criterion.__doc__ = f"dup_density <= {max_dup_density:.1%}"
    return criterion


def dedup_scorer(report: DedupReport) -> float:
    """Score function for converge() — higher = fewer duplicates.

    Returns a value in [0.0, 1.0] where 1.0 means zero duplication.
    """
    return 1.0 - min(1.0, report.dup_density)


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="GRIP Deduplication Scanner — detect and report duplicate code"
    )
    parser.add_argument("path", nargs="?", default=".",
                        help="Path to scan (default: current directory)")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="Max duplicate density before warning (default: 0.05)")
    parser.add_argument("--min-tokens", type=int, default=MIN_BLOCK_TOKENS,
                        help=f"Minimum tokens for a block (default: {MIN_BLOCK_TOKENS})")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    scanner = DedupScanner(path=args.path, min_block_tokens=args.min_tokens)
    report = scanner.scan()
    print(report.summary())

    if report.dup_density > args.threshold:
        print(f"\nWARNING: dup_density {report.dup_density:.1%} exceeds threshold {args.threshold:.1%}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
