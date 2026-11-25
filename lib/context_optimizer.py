"""GRIP Context Budget Optimizer -- analyse and optimise context window usage."""

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# Rough token estimation: ~4 chars per token for English text
CHARS_PER_TOKEN = 4


class ContextProfile(Enum):
    OPUS_1M = "opus-1m"       # Default since Opus 4.7 (April 2026); Opus 4.6 also 1M
    SONNET_1M = "sonnet-1m"   # Default since Sonnet 4.6 (March 2026)
    OPUS_200K = "opus-200k"   # Legacy — kept for backward compat
    SONNET_200K = "sonnet-200k"  # Legacy — kept for backward compat


PROFILE_LIMITS = {
    ContextProfile.OPUS_1M: 1_000_000,
    ContextProfile.SONNET_1M: 1_000_000,
    ContextProfile.OPUS_200K: 200_000,    # Legacy
    ContextProfile.SONNET_200K: 200_000,  # Legacy
}


class FileCategory(Enum):
    RULES = "rules"
    SKILLS = "skills"
    MEMORY = "memory"
    CONFIG = "config"
    HOOKS = "hooks"
    LIBRARY = "library"
    TESTS = "tests"
    OTHER = "other"


@dataclass
class FileTokenEstimate:
    path: str
    tokens: int
    chars: int
    lines: int
    category: FileCategory


@dataclass
class ContextBudget:
    profile: ContextProfile
    total_tokens: int
    used_tokens: int
    categories: Dict[str, int]  # category -> tokens
    utilisation: float  # 0.0-1.0


@dataclass
class RedundancyFinding:
    file_a: str
    file_b: str
    overlap_tokens: int
    overlap_lines: List[str]
    suggestion: str


@dataclass
class OptimisationSuggestion:
    type: str  # "consolidate", "trim", "exclude", "split"
    target: str  # file path
    savings_estimate: int  # tokens saved
    description: str
    priority: str  # "high", "medium", "low"


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using chars / CHARS_PER_TOKEN."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def categorise_file(path: str, claude_dir: Path) -> FileCategory:
    """Determine category from file path relative to claude_dir."""
    try:
        rel = Path(path).relative_to(claude_dir)
    except ValueError:
        return FileCategory.OTHER

    parts = rel.parts
    if not parts:
        return FileCategory.OTHER

    first = parts[0].lower()

    if first == "rules" or (len(parts) > 1 and parts[0] == ".claude" and parts[1] == "rules"):
        return FileCategory.RULES
    if first == "skills":
        return FileCategory.SKILLS
    if first == "memory" or "memory" in str(rel).lower():
        return FileCategory.MEMORY
    if first == "config" or rel.suffix in (".yaml", ".yml", ".json", ".toml"):
        return FileCategory.CONFIG
    if first == "hooks":
        return FileCategory.HOOKS
    if first == "lib":
        return FileCategory.LIBRARY
    if first == "tests":
        return FileCategory.TESTS
    if first == "projects":
        # projects/<path>/memory/ files are memory
        if "memory" in str(rel).lower():
            return FileCategory.MEMORY
        return FileCategory.CONFIG

    # Top-level .md files (like CLAUDE.md) are rules
    if rel.suffix == ".md" and len(parts) == 1:
        return FileCategory.RULES
    # .claude/CLAUDE.md is rules
    if parts == (".claude", "CLAUDE.md"):
        return FileCategory.RULES

    return FileCategory.OTHER


def _is_text_file(path: Path) -> bool:
    """Check if a file is likely a text file (not binary)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        # Check for null bytes (binary indicator)
        if b"\x00" in chunk:
            return False
        return True
    except (OSError, IOError):
        return False


def _get_auto_loaded_patterns() -> List[str]:
    """Return patterns for files that Claude auto-loads into context."""
    return [
        "CLAUDE.md",
        ".claude/CLAUDE.md",
        "rules/*.md",
        "projects/*/memory/MEMORY.md",
    ]


def scan_files(claude_dir: Path) -> List[FileTokenEstimate]:
    """Scan all auto-loaded files and estimate their tokens.

    Auto-loaded files include:
    - CLAUDE.md (top-level)
    - .claude/CLAUDE.md
    - rules/*.md
    - projects/*/memory/MEMORY.md
    """
    claude_dir = Path(claude_dir)
    results = []
    seen = set()

    def _process_file(filepath: Path) -> Optional[FileTokenEstimate]:
        filepath = filepath.resolve()
        if filepath in seen:
            return None
        if not filepath.is_file():
            return None
        if filepath.is_symlink():
            # Resolve symlinks but still process
            pass
        if not _is_text_file(filepath):
            return None

        seen.add(filepath)

        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, IOError):
            return None

        tokens = estimate_tokens(text)
        lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        category = categorise_file(str(filepath), claude_dir)

        return FileTokenEstimate(
            path=str(filepath),
            tokens=tokens,
            chars=len(text),
            lines=lines,
            category=category,
        )

    # Top-level CLAUDE.md
    top_claude = claude_dir / "CLAUDE.md"
    est = _process_file(top_claude)
    if est:
        results.append(est)

    # .claude/CLAUDE.md
    nested_claude = claude_dir / ".claude" / "CLAUDE.md"
    est = _process_file(nested_claude)
    if est:
        results.append(est)

    # rules/*.md
    rules_dir = claude_dir / "rules"
    if rules_dir.is_dir():
        for md_file in sorted(rules_dir.glob("*.md")):
            est = _process_file(md_file)
            if est:
                results.append(est)

    # projects/*/memory/MEMORY.md
    projects_dir = claude_dir / "projects"
    if projects_dir.is_dir():
        for memory_file in sorted(projects_dir.glob("*/memory/MEMORY.md")):
            est = _process_file(memory_file)
            if est:
                results.append(est)

    return results


def compute_budget(
    files: List[FileTokenEstimate], profile: ContextProfile
) -> ContextBudget:
    """Compute total usage against profile limit, broken down by category."""
    total_limit = PROFILE_LIMITS[profile]
    categories: Dict[str, int] = {}

    for f in files:
        cat_name = f.category.value
        categories[cat_name] = categories.get(cat_name, 0) + f.tokens

    used = sum(f.tokens for f in files)
    utilisation = used / total_limit if total_limit > 0 else 0.0

    return ContextBudget(
        profile=profile,
        total_tokens=total_limit,
        used_tokens=used,
        categories=categories,
        utilisation=utilisation,
    )


def _normalize_line(line: str) -> str:
    """Normalize a line for comparison (strip whitespace, lowercase)."""
    return line.strip().lower()


def _find_overlapping_blocks(
    lines_a: List[str], lines_b: List[str], min_block_size: int = 3
) -> List[List[str]]:
    """Find blocks of 3+ similar lines between two files."""
    norm_a = [_normalize_line(l) for l in lines_a]
    norm_b = [_normalize_line(l) for l in lines_b]

    overlapping_blocks = []
    i = 0
    while i < len(norm_a):
        # Skip empty/trivial lines
        if not norm_a[i].strip():
            i += 1
            continue

        # Try to find this line in B
        for j in range(len(norm_b)):
            if norm_a[i] == norm_b[j] and norm_a[i].strip():
                # Found a match, try to extend the block
                block = []
                ai, bj = i, j
                while (
                    ai < len(norm_a)
                    and bj < len(norm_b)
                    and norm_a[ai] == norm_b[bj]
                ):
                    block.append(lines_a[ai].rstrip())
                    ai += 1
                    bj += 1

                if len(block) >= min_block_size:
                    # Check we haven't already found this block
                    block_text = "\n".join(block)
                    already_found = any(
                        "\n".join(b) == block_text for b in overlapping_blocks
                    )
                    if not already_found:
                        overlapping_blocks.append(block)
                break
        i += 1

    return overlapping_blocks


def detect_redundancy(
    files: List[FileTokenEstimate], claude_dir: Path
) -> List[RedundancyFinding]:
    """Find content overlap between auto-loaded files."""
    findings = []
    file_contents: Dict[str, List[str]] = {}

    for f in files:
        try:
            text = Path(f.path).read_text(encoding="utf-8", errors="replace")
            file_contents[f.path] = text.splitlines()
        except (OSError, IOError):
            continue

    paths = list(file_contents.keys())
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            path_a, path_b = paths[i], paths[j]
            lines_a, lines_b = file_contents[path_a], file_contents[path_b]

            blocks = _find_overlapping_blocks(lines_a, lines_b)
            if blocks:
                all_overlap_lines = []
                for block in blocks:
                    all_overlap_lines.extend(block)

                overlap_text = "\n".join(all_overlap_lines)
                overlap_tokens = estimate_tokens(overlap_text)

                rel_a = _relative_display(path_a, claude_dir)
                rel_b = _relative_display(path_b, claude_dir)

                findings.append(
                    RedundancyFinding(
                        file_a=path_a,
                        file_b=path_b,
                        overlap_tokens=overlap_tokens,
                        overlap_lines=all_overlap_lines,
                        suggestion=f"Consolidate shared content between {rel_a} and {rel_b} ({overlap_tokens} tokens overlap)",
                    )
                )

    return findings


def _relative_display(path: str, claude_dir: Path) -> str:
    """Get a display-friendly relative path."""
    try:
        return str(Path(path).relative_to(claude_dir))
    except ValueError:
        return path


def suggest_optimisations(
    budget: ContextBudget,
    files: List[FileTokenEstimate],
    redundancies: List[RedundancyFinding],
) -> List[OptimisationSuggestion]:
    """Generate actionable optimisation suggestions."""
    suggestions = []

    # Files over 200 lines -> suggest trimming
    for f in files:
        if f.lines > 200:
            excess_lines = f.lines - 200
            estimated_savings = (f.tokens * excess_lines) // f.lines if f.lines > 0 else 0
            suggestions.append(
                OptimisationSuggestion(
                    type="trim",
                    target=f.path,
                    savings_estimate=estimated_savings,
                    description=f"File has {f.lines} lines (limit 200). Trim {excess_lines} lines to save ~{estimated_savings} tokens.",
                    priority="high",
                )
            )

    # Duplicate content -> suggest consolidation
    for r in redundancies:
        suggestions.append(
            OptimisationSuggestion(
                type="consolidate",
                target=r.file_a,
                savings_estimate=r.overlap_tokens,
                description=r.suggestion,
                priority="medium" if r.overlap_tokens < 500 else "high",
            )
        )

    # Low-value files auto-loaded -> suggest exclusion
    for f in files:
        if f.category == FileCategory.CONFIG and f.tokens > 100:
            suggestions.append(
                OptimisationSuggestion(
                    type="exclude",
                    target=f.path,
                    savings_estimate=f.tokens,
                    description=f"Config file auto-loaded ({f.tokens} tokens). Consider excluding if not needed every session.",
                    priority="low",
                )
            )

    # Categories using >30% of budget -> suggest splitting
    total_used = budget.used_tokens
    if total_used > 0:
        for cat_name, cat_tokens in budget.categories.items():
            cat_pct = cat_tokens / budget.total_tokens
            if cat_pct > 0.30:
                suggestions.append(
                    OptimisationSuggestion(
                        type="split",
                        target=cat_name,
                        savings_estimate=cat_tokens // 3,
                        description=f"Category '{cat_name}' uses {cat_pct:.1%} of total budget ({cat_tokens} tokens). Consider splitting into on-demand and always-loaded.",
                        priority="high",
                    )
                )

    # Sort by priority (high first) then savings (descending)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(
        key=lambda s: (priority_order.get(s.priority, 3), -s.savings_estimate)
    )

    return suggestions


def top_consumers(
    files: List[FileTokenEstimate], limit: int = 10
) -> List[FileTokenEstimate]:
    """Return files sorted by token count descending."""
    return sorted(files, key=lambda f: f.tokens, reverse=True)[:limit]


def _bar(fraction: float, width: int = 12) -> str:
    """Create an ASCII progress bar."""
    filled = int(fraction * width)
    filled = max(0, min(width, filled))
    return "\u2588" * filled + "\u2591" * (width - filled)


def format_budget_report(
    budget: ContextBudget,
    suggestions: List[OptimisationSuggestion],
    files: List[FileTokenEstimate],
    profile: ContextProfile,
) -> str:
    """Format a complete ASCII budget report."""
    lines = []
    lines.append(f"Context Budget Analysis ({profile.value})")
    lines.append("=" * 44)
    lines.append("")
    lines.append("Category Breakdown:")

    # Sort categories by token count descending
    sorted_cats = sorted(budget.categories.items(), key=lambda x: -x[1])
    for cat_name, cat_tokens in sorted_cats:
        pct = (cat_tokens / budget.total_tokens * 100) if budget.total_tokens > 0 else 0
        bar = _bar(cat_tokens / budget.total_tokens if budget.total_tokens > 0 else 0)
        lines.append(f"  {cat_name:<12s} {bar}  {cat_tokens:>6,d} tokens ({pct:.1f}%)")

    lines.append("")
    remaining = budget.total_tokens - budget.used_tokens
    pct_used = budget.utilisation * 100
    lines.append(
        f"Total Auto-Loaded: {budget.used_tokens:,d} / {budget.total_tokens:,d} tokens ({pct_used:.1f}%)"
    )
    lines.append(f"Remaining Budget: {remaining:,d} tokens")
    lines.append("")

    # Top consumers
    top = top_consumers(files, limit=10)
    if top:
        lines.append("Top Consumers:")
        for i, f in enumerate(top, 1):
            try:
                rel = str(Path(f.path).relative_to(Path(f.path).parent.parent.parent))
            except (ValueError, IndexError):
                rel = Path(f.path).name
            lines.append(f"  {i:>2d}. {rel:<35s} {f.tokens:>6,d} tokens")
        lines.append("")

    # Suggestions
    if suggestions:
        lines.append(f"Suggestions ({len(suggestions)}):")
        for s in suggestions:
            tag = s.priority.upper()
            lines.append(f"  [{tag}] {s.description}")
        lines.append("")

    return "\n".join(lines)


def format_redundancy_report(findings: List[RedundancyFinding]) -> str:
    """Show overlap details between files."""
    if not findings:
        return "No redundancy detected between auto-loaded files."

    lines = []
    lines.append(f"Redundancy Report ({len(findings)} finding(s))")
    lines.append("=" * 44)
    lines.append("")

    for i, f in enumerate(findings, 1):
        lines.append(f"Finding {i}: {f.overlap_tokens} tokens overlap")
        lines.append(f"  File A: {f.file_a}")
        lines.append(f"  File B: {f.file_b}")
        lines.append(f"  Suggestion: {f.suggestion}")
        if f.overlap_lines:
            lines.append(f"  Overlapping lines ({len(f.overlap_lines)}):")
            preview = f.overlap_lines[:5]
            for line in preview:
                lines.append(f"    | {line}")
            if len(f.overlap_lines) > 5:
                lines.append(f"    ... and {len(f.overlap_lines) - 5} more lines")
        lines.append("")

    return "\n".join(lines)


def run_audit(claude_dir: Path, profile_name: str) -> str:
    """Run a full audit and return the report."""
    profile = ContextProfile(profile_name)
    files = scan_files(claude_dir)
    budget = compute_budget(files, profile)
    redundancies = detect_redundancy(files, claude_dir)
    suggestions = suggest_optimisations(budget, files, redundancies)
    return format_budget_report(budget, suggestions, files, profile)


def run_top_consumers(claude_dir: Path, limit: int) -> str:
    """Run top consumers report."""
    files = scan_files(claude_dir)
    top = top_consumers(files, limit=limit)
    lines = [f"Top {len(top)} Context Consumers:"]
    lines.append("=" * 44)
    for i, f in enumerate(top, 1):
        rel = _relative_display(f.path, claude_dir)
        lines.append(
            f"  {i:>2d}. {rel:<40s} {f.tokens:>6,d} tokens  ({f.lines} lines, {f.category.value})"
        )
    return "\n".join(lines)


def run_redundancy(claude_dir: Path) -> str:
    """Run redundancy detection and return the report."""
    files = scan_files(claude_dir)
    findings = detect_redundancy(files, claude_dir)
    return format_redundancy_report(findings)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Context Budget Optimizer")
    sub = parser.add_subparsers(dest="command")

    audit_p = sub.add_parser("audit", help="Full context budget audit")
    audit_p.add_argument(
        "--profile",
        default="opus-1m",
        choices=["opus-1m", "sonnet-1m", "opus-200k", "sonnet-200k"],
    )
    audit_p.add_argument("--claude-dir", type=str)

    top_p = sub.add_parser("top-consumers", help="Show top token consumers")
    top_p.add_argument("--limit", type=int, default=10)
    top_p.add_argument("--claude-dir", type=str)

    red_p = sub.add_parser("redundancy", help="Detect content redundancy")
    red_p.add_argument("--claude-dir", type=str)

    args = parser.parse_args()

    if args.command:
        cdir = Path(args.claude_dir) if hasattr(args, "claude_dir") and args.claude_dir else CLAUDE_DIR

    if args.command == "audit":
        print(run_audit(cdir, args.profile))
    elif args.command == "top-consumers":
        print(run_top_consumers(cdir, args.limit))
    elif args.command == "redundancy":
        print(run_redundancy(cdir))
    else:
        parser.print_help()
