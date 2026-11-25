#!/usr/bin/env python3
"""Context Budget Estimator -- predict token usage for planned file operations.

Estimates how many tokens a set of files will consume when read into a Claude
context window, helping operators stay within budget before they start reading.

Usage:
    python3 -m lib.context_budget_estimator --files "lib/*.py" --profile opus-200k
    python3 -m lib.context_budget_estimator --dir lib --budget 70 --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Sequence


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Category -> overhead multiplier.  Code is tighter (fewer tokens per char);
# markdown has more whitespace and formatting tokens.
OVERHEAD_FACTORS: dict[str, float] = {
    "code": 1.1,
    "config": 1.1,
    "docs": 1.2,
    "other": 1.15,
}

CATEGORY_EXTENSIONS: dict[str, set[str]] = {
    "code": {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java", ".c",
        ".cpp", ".h", ".hpp", ".rb", ".swift", ".kt", ".cs", ".sh", ".bash",
        ".zsh", ".fish", ".pl", ".lua", ".r", ".R", ".m", ".mm", ".scala",
        ".zig", ".nim", ".ex", ".exs", ".erl", ".hs", ".ml", ".mli",
        ".clj", ".cljs", ".cljc", ".lisp", ".el", ".vim", ".sql",
        ".php", ".dart",
    },
    "config": {
        ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf",
        ".env", ".properties", ".xml", ".plist", ".editorconfig",
        ".prettierrc", ".eslintrc", ".babelrc",
    },
    "docs": {
        ".md", ".rst", ".txt", ".adoc", ".org", ".tex", ".latex",
        ".rdoc", ".wiki",
    },
}

# Directories to always skip.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "node_modules", ".next", "dist", "build", "__pycache__", "venv",
    ".venv", "target", "vendor", "Pods", ".git", ".hg", ".svn",
    "env", ".tox", ".mypy_cache", ".pytest_cache", ".eggs",
    "site-packages", "bower_components",
})

# File extensions that are almost certainly binary.
BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac",
    ".zip", ".gz", ".tar", ".bz2", ".xz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".lib",
    ".pyc", ".pyo", ".class", ".wasm",
    ".db", ".sqlite", ".sqlite3",
})

# Model profiles: name -> context window size in tokens.
MODEL_PROFILES: dict[str, int] = {
    "opus-1m": 1_000_000,
    "sonnet-1m": 1_000_000,     # Sonnet 4.6 1M GA (March 2026)
    "opus-200k": 200_000,       # Legacy
    "sonnet": 200_000,          # Legacy alias
}

DEFAULT_PROFILE = "opus-1m"
DEFAULT_BUDGET_PCT = 80


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TokenEstimate:
    """Estimate for a single file."""

    path: str
    chars: int
    estimated_tokens: int
    category: str

    @property
    def tokens_per_char(self) -> float:
        if self.chars == 0:
            return 0.0
        return self.estimated_tokens / self.chars


@dataclass
class BudgetReport:
    """Aggregated budget report across all matched files."""

    total_tokens: int
    budget_limit: int
    utilization_pct: float
    files: List[TokenEstimate]
    warnings: List[str] = field(default_factory=list)

    @property
    def files_that_fit(self) -> List[TokenEstimate]:
        """Return files in ranked order that fit within budget."""
        cumulative = 0
        result: list[TokenEstimate] = []
        for f in sorted(self.files, key=lambda x: x.estimated_tokens):
            cumulative += f.estimated_tokens
            if cumulative <= self.budget_limit:
                result.append(f)
            else:
                break
        return result

    @property
    def overflow_files(self) -> List[TokenEstimate]:
        """Files that would NOT fit after budget is exhausted (greedy smallest-first)."""
        fit_paths = {f.path for f in self.files_that_fit}
        return [f for f in self.files if f.path not in fit_paths]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["files_that_fit"] = [asdict(f) for f in self.files_that_fit]
        d["overflow_files"] = [asdict(f) for f in self.overflow_files]
        return d


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def classify_file(path: Path) -> str:
    """Return category string for *path* based on its extension."""
    ext = path.suffix.lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if ext in extensions:
            return category
    return "other"


def is_binary(path: Path) -> bool:
    """Heuristic: return True if *path* is likely a binary file."""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    # Peek at first 8 KB for null bytes.
    try:
        chunk = path.read_bytes()[:8192]
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True


def estimate_tokens_for_file(path: Path) -> Optional[TokenEstimate]:
    """Estimate tokens for a single file.  Returns None for binary/unreadable."""
    if is_binary(path):
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    chars = len(content)
    category = classify_file(path)
    overhead = OVERHEAD_FACTORS.get(category, 1.15)
    raw_tokens = chars / 4.0
    estimated = int(raw_tokens * overhead)
    return TokenEstimate(
        path=str(path),
        chars=chars,
        estimated_tokens=estimated,
        category=category,
    )


def _should_exclude(path: Path, exclude_dirs: frozenset[str]) -> bool:
    """Return True if any component of *path* is in the exclusion set."""
    parts = set(path.parts)
    return bool(parts & exclude_dirs)


def discover_files(
    patterns: Sequence[str],
    base_dir: Path,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> list[Path]:
    """Expand glob *patterns* relative to *base_dir*, honouring exclusions."""
    seen: set[Path] = set()
    results: list[Path] = []
    for pattern in patterns:
        for match in sorted(base_dir.glob(pattern)):
            resolved = match.resolve()
            if resolved in seen:
                continue
            if not resolved.is_file():
                continue
            if _should_exclude(resolved, exclude_dirs):
                continue
            seen.add(resolved)
            results.append(resolved)
    return results


def scan_directory(
    base_dir: Path,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
) -> list[Path]:
    """Recursively list all files under *base_dir*, skipping exclusions."""
    results: list[Path] = []
    for root, dirs, files in os.walk(base_dir):
        root_path = Path(root)
        # Prune excluded directories in-place so os.walk skips them.
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for fname in sorted(files):
            fpath = root_path / fname
            if not _should_exclude(fpath, exclude_dirs):
                results.append(fpath.resolve())
    return results


def estimate_budget(
    files: Sequence[Path],
    profile: str = DEFAULT_PROFILE,
    budget_pct: int = DEFAULT_BUDGET_PCT,
) -> BudgetReport:
    """Build a BudgetReport for the given file list."""
    if profile not in MODEL_PROFILES:
        raise ValueError(
            f"Unknown profile {profile!r}. Choose from: {', '.join(MODEL_PROFILES)}"
        )

    budget_limit = int(MODEL_PROFILES[profile] * budget_pct / 100)
    estimates: list[TokenEstimate] = []
    warnings: list[str] = []

    for path in files:
        est = estimate_tokens_for_file(path)
        if est is None:
            warnings.append(f"Skipped (binary/unreadable): {path}")
            continue
        estimates.append(est)

    # Sort descending by token cost (most expensive first).
    estimates.sort(key=lambda e: e.estimated_tokens, reverse=True)

    total = sum(e.estimated_tokens for e in estimates)
    utilization = (total / budget_limit * 100) if budget_limit > 0 else 0.0

    if utilization > 100:
        warnings.append(
            f"OVER BUDGET: {total:,} tokens estimated vs {budget_limit:,} limit "
            f"({utilization:.1f}%)"
        )
    elif utilization > 90:
        warnings.append(
            f"Near budget: {total:,} tokens = {utilization:.1f}% of {budget_limit:,} limit"
        )

    return BudgetReport(
        total_tokens=total,
        budget_limit=budget_limit,
        utilization_pct=round(utilization, 2),
        files=estimates,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_table(report: BudgetReport) -> str:
    """Human-readable table output."""
    lines: list[str] = []

    # Header
    lines.append(f"Context Budget Report")
    lines.append(f"{'=' * 72}")
    lines.append(
        f"Budget: {report.budget_limit:,} tokens "
        f"(profile limit: {report.total_tokens:,} estimated)"
    )
    lines.append(f"Utilization: {report.utilization_pct:.1f}%")
    lines.append("")

    # File table
    if report.files:
        header = f"{'Tokens':>10}  {'Chars':>10}  {'Category':<8}  Path"
        lines.append(header)
        lines.append("-" * len(header) + "-" * 40)
        for est in report.files:
            lines.append(
                f"{est.estimated_tokens:>10,}  {est.chars:>10,}  "
                f"{est.category:<8}  {est.path}"
            )
        lines.append("")

    # Summary
    fit = report.files_that_fit
    overflow = report.overflow_files
    lines.append(f"Files that fit: {len(fit)}")
    lines.append(f"Overflow files: {len(overflow)}")

    if overflow:
        lines.append("")
        lines.append("Overflow (would not fit):")
        for f in overflow:
            lines.append(f"  - {f.path} ({f.estimated_tokens:,} tokens)")

    # Warnings
    if report.warnings:
        lines.append("")
        for w in report.warnings:
            lines.append(f"WARNING: {w}")

    return "\n".join(lines)


def format_json(report: BudgetReport) -> str:
    """JSON output."""
    return json.dumps(report.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context-budget-estimator",
        description="Estimate token usage for planned file operations.",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        metavar="PATTERN",
        help="Glob patterns for files to estimate (e.g. 'lib/*.py' '*.md').",
    )
    parser.add_argument(
        "--dir",
        metavar="PATH",
        help="Recursively scan directory instead of using patterns.",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        choices=sorted(MODEL_PROFILES.keys()),
        help=f"Model profile (default: {DEFAULT_PROFILE}).",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET_PCT,
        metavar="PCT",
        help=f"Budget threshold percentage (default: {DEFAULT_BUDGET_PCT}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output in JSON format.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        metavar="N",
        help="Show only the top N most expensive files.",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        metavar="DIR",
        help="Additional directory names to exclude.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.files and not args.dir:
        parser.error("Provide --files PATTERN or --dir PATH")

    exclude = DEFAULT_EXCLUDE_DIRS
    if args.exclude:
        exclude = exclude | frozenset(args.exclude)

    base = Path(args.dir) if args.dir else Path.cwd()

    if args.dir:
        files = scan_directory(Path(args.dir), exclude_dirs=exclude)
    else:
        files = discover_files(args.files, base_dir=base, exclude_dirs=exclude)

    if not files:
        print("No files matched.", file=sys.stderr)
        return 1

    report = estimate_budget(files, profile=args.profile, budget_pct=args.budget)

    if args.top > 0:
        report.files = report.files[: args.top]

    if args.json_output:
        print(format_json(report))
    else:
        print(format_table(report))

    # Exit 2 if over budget, 0 otherwise.
    return 2 if report.utilization_pct > 100 else 0


if __name__ == "__main__":
    sys.exit(main())
