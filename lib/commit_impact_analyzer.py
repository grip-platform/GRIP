"""Commit Impact Analyzer -- predict the blast radius of staged or recent changes.

Parses git diff output to identify changed files, builds a reverse dependency
graph via AST-based import extraction, scores risk using logarithmic scaling,
detects test coverage gaps, and suggests reviewers from git log.

Usage:
    python3 lib/commit_impact_analyzer.py              # staged changes
    python3 lib/commit_impact_analyzer.py --ref HEAD~3  # specific ref range
    python3 lib/commit_impact_analyzer.py --json         # JSON output
    python3 lib/commit_impact_analyzer.py --file lib/converge.py  # specific file
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileChange:
    """A single file changed in a commit or staging area."""
    path: str
    additions: int = 0
    deletions: int = 0
    change_type: str = "modified"  # added | modified | deleted | renamed


@dataclass
class ReverseDependency:
    """A file that imports a changed file."""
    importer_path: str
    import_name: str
    line_number: int = 0


@dataclass
class FileImpact:
    """Impact analysis for a single changed file."""
    path: str
    additions: int = 0
    deletions: int = 0
    lines_changed: int = 0
    change_type: str = "modified"
    reverse_deps: List[ReverseDependency] = field(default_factory=list)
    has_test: bool = False
    test_file: str = ""
    criticality: float = 1.0
    criticality_label: str = "normal"
    risk_score: float = 0.0
    risk_label: str = "LOW"
    suggested_reviewers: List[str] = field(default_factory=list)


@dataclass
class ImpactReport:
    """Complete impact analysis report."""
    files: List[FileImpact] = field(default_factory=list)
    overall_risk: float = 0.0
    overall_label: str = "LOW"
    total_additions: int = 0
    total_deletions: int = 0
    total_reverse_deps: int = 0
    untested_files: List[str] = field(default_factory=list)
    top_reviewers: List[Tuple[str, int]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRITICALITY_WEIGHTS: Dict[str, Tuple[float, str]] = {
    # Pattern -> (weight, label)
    # Core infrastructure
    r"^lib/": (2.0, "core-lib"),
    r"^src/": (2.0, "core-src"),
    r"^core/": (2.5, "core"),
    # Configuration
    r"(^|\/)settings": (2.5, "config"),
    r"(^|\/)config": (2.0, "config"),
    r"\.ya?ml$": (1.5, "config-yaml"),
    r"\.toml$": (1.5, "config-toml"),
    r"\.json$": (1.2, "config-json"),
    # CI/CD
    r"\.github/": (3.0, "ci-cd"),
    r"Dockerfile": (2.0, "infrastructure"),
    r"docker-compose": (2.0, "infrastructure"),
    # Tests
    r"tests?/": (0.5, "test"),
    r"test_": (0.5, "test"),
    r"_test\.py$": (0.5, "test"),
    r"\.test\.(js|ts)$": (0.5, "test"),
    r"spec/": (0.5, "test"),
    # Docs
    r"\.md$": (0.3, "docs"),
    r"docs?/": (0.3, "docs"),
    r"README": (0.5, "docs"),
    # Init files
    r"__init__\.py$": (1.8, "init-module"),
}

RISK_THRESHOLDS = {
    "LOW": (0, 3),
    "MEDIUM": (3, 7),
    "HIGH": (7, 12),
    "CRITICAL": (12, float("inf")),
}

# Patterns for test file discovery
TEST_FILE_PATTERNS = [
    "tests/test_{stem}.py",
    "tests/{stem}_test.py",
    "test/test_{stem}.py",
    "test/{stem}_test.py",
    "tests/{parent}/test_{stem}.py",
    "{parent}/tests/test_{stem}.py",
    "{parent}/test_{stem}.py",
]


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(*args: str, cwd: Optional[str] = None) -> str:
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def get_repo_root(cwd: Optional[str] = None) -> str:
    """Return the git repository root directory."""
    return _run_git("rev-parse", "--show-toplevel", cwd=cwd)


def get_changed_files_staged(cwd: Optional[str] = None) -> List[FileChange]:
    """Parse git diff --staged --numstat for staged changes."""
    raw = _run_git("diff", "--staged", "--numstat", cwd=cwd)
    return _parse_numstat(raw)


def get_changed_files_ref(ref: str, cwd: Optional[str] = None) -> List[FileChange]:
    """Parse git diff <ref> --numstat for a ref range."""
    raw = _run_git("diff", ref, "--numstat", cwd=cwd)
    return _parse_numstat(raw)


def get_changed_files_for_file(filepath: str, cwd: Optional[str] = None) -> List[FileChange]:
    """Create a synthetic FileChange for a specific file."""
    # Get the diff stats for this file against HEAD
    raw = _run_git("diff", "HEAD", "--numstat", "--", filepath, cwd=cwd)
    changes = _parse_numstat(raw)
    if not changes:
        # File might be staged
        raw = _run_git("diff", "--staged", "--numstat", "--", filepath, cwd=cwd)
        changes = _parse_numstat(raw)
    if not changes:
        # File exists but has no diff -- treat as analysis target
        if os.path.exists(os.path.join(cwd or ".", filepath)):
            changes = [FileChange(path=filepath, additions=0, deletions=0, change_type="analyzed")]
    return changes


def _parse_numstat(raw: str) -> List[FileChange]:
    """Parse git diff --numstat output into FileChange objects."""
    changes = []
    if not raw:
        return changes
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        adds_str, dels_str, path = parts[0], parts[1], parts[2]
        # Binary files show '-' for numstat
        adds = int(adds_str) if adds_str != "-" else 0
        dels = int(dels_str) if dels_str != "-" else 0
        change_type = "modified"
        if adds > 0 and dels == 0:
            change_type = "added"
        elif adds == 0 and dels > 0:
            change_type = "deleted"
        # Handle renames (path contains ' => ')
        if " => " in path:
            change_type = "renamed"
            # Extract destination path from rename syntax
            # e.g., "{old => new}/file.py" or "old.py => new.py"
            match = re.search(r"=> (.+?)(?:\}|$)", path)
            if match:
                path = match.group(1).strip()
                # Handle brace syntax: "dir/{old => new}.py"
                brace_match = re.match(r"(.+)\{.+ => (.+)\}(.+)", line.split("\t")[2])
                if brace_match:
                    path = brace_match.group(1) + brace_match.group(2) + brace_match.group(3)
        changes.append(FileChange(path=path, additions=adds, deletions=dels, change_type=change_type))
    return changes


def get_file_contributors(filepath: str, top_n: int = 5, cwd: Optional[str] = None) -> List[Tuple[str, int]]:
    """Get top contributors for a file from git log."""
    raw = _run_git("log", "--format=%an", "--follow", "--", filepath, cwd=cwd)
    if not raw:
        return []
    counter = Counter(raw.splitlines())
    return counter.most_common(top_n)


# ---------------------------------------------------------------------------
# AST-based import extraction
# ---------------------------------------------------------------------------

def extract_imports_from_source(source: str) -> List[Tuple[str, int]]:
    """Extract all import names from Python source code using AST.

    Returns list of (module_name, line_number) tuples.
    Does NOT execute any code.
    """
    imports = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append((node.module, node.lineno))
    return imports


def module_name_to_path(module_name: str, repo_root: str) -> Optional[str]:
    """Convert a Python module name to a file path relative to repo root.

    Tries both direct file and package __init__.py.
    """
    parts = module_name.split(".")
    # Try as direct .py file
    rel_path = os.path.join(*parts) + ".py" if len(parts) > 1 else parts[0] + ".py"
    if os.path.exists(os.path.join(repo_root, rel_path)):
        return rel_path
    # Try as package __init__.py
    pkg_path = os.path.join(*parts, "__init__.py")
    if os.path.exists(os.path.join(repo_root, pkg_path)):
        return pkg_path
    # Try just the first component (common for local imports like 'from mylib import x')
    if len(parts) > 1:
        first = parts[0] + ".py"
        if os.path.exists(os.path.join(repo_root, first)):
            return first
    return None


def path_to_module_names(filepath: str) -> Set[str]:
    """Convert a file path to the set of module names that could import it.

    For 'lib/foo.py', returns {'lib.foo', 'foo', 'lib'}.
    For 'lib/__init__.py', returns {'lib'}.
    """
    names = set()
    p = Path(filepath)

    # Strip .py extension
    if p.suffix == ".py":
        stem = str(p.with_suffix(""))
    else:
        return names

    # Handle __init__.py -> package name
    if p.stem == "__init__":
        pkg = str(p.parent)
        names.add(pkg.replace(os.sep, "."))
        return names

    # Full dotted path: lib/foo.py -> lib.foo
    dotted = stem.replace(os.sep, ".").replace("/", ".")
    names.add(dotted)

    # Just the stem: foo
    names.add(p.stem)

    # Parent.stem: lib.foo but also intermediate paths
    parts = dotted.split(".")
    for i in range(len(parts)):
        names.add(".".join(parts[i:]))

    return names


def build_reverse_dependency_map(
    changed_paths: List[str],
    repo_root: str,
    scan_dirs: Optional[List[str]] = None,
) -> Dict[str, List[ReverseDependency]]:
    """Build reverse dependency map: for each changed file, find all importers.

    Scans Python files in the repo for imports that match any of the changed files.
    Uses AST parsing -- never executes code.
    """
    # Build the set of module names we're looking for
    target_modules: Dict[str, str] = {}  # module_name -> changed_file_path
    for changed_path in changed_paths:
        if not changed_path.endswith(".py"):
            continue
        for mod_name in path_to_module_names(changed_path):
            target_modules[mod_name] = changed_path

    if not target_modules:
        return {p: [] for p in changed_paths}

    # Scan Python files for imports
    reverse_deps: Dict[str, List[ReverseDependency]] = {p: [] for p in changed_paths}

    dirs_to_scan = scan_dirs or [repo_root]
    for scan_dir in dirs_to_scan:
        abs_scan = os.path.join(repo_root, scan_dir) if not os.path.isabs(scan_dir) else scan_dir
        if not os.path.isdir(abs_scan):
            continue
        for root, dirs, files in os.walk(abs_scan):
            # Skip dependency/build directories
            dirs[:] = [
                d for d in dirs
                if d not in {
                    "node_modules", ".next", "dist", "build", "__pycache__",
                    "venv", ".venv", "target", "vendor", "Pods", ".git",
                    ".tox", ".mypy_cache", ".pytest_cache", "egg-info",
                }
            ]
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                abs_file = os.path.join(root, filename)
                rel_file = os.path.relpath(abs_file, repo_root)
                # Skip scanning changed files themselves
                if rel_file in changed_paths:
                    continue
                try:
                    source = Path(abs_file).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                imports = extract_imports_from_source(source)
                for mod_name, lineno in imports:
                    if mod_name in target_modules:
                        changed_file = target_modules[mod_name]
                        reverse_deps[changed_file].append(
                            ReverseDependency(
                                importer_path=rel_file,
                                import_name=mod_name,
                                line_number=lineno,
                            )
                        )
    return reverse_deps


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

def get_criticality(filepath: str) -> Tuple[float, str]:
    """Determine criticality weight and label for a file path.

    Matches against CRITICALITY_WEIGHTS patterns. First match wins,
    patterns are checked in order. Default: (1.0, 'normal').
    """
    for pattern, (weight, label) in CRITICALITY_WEIGHTS.items():
        if re.search(pattern, filepath):
            return weight, label
    return 1.0, "normal"


def compute_risk_score(
    dependents_count: int,
    criticality_weight: float,
    lines_changed: int,
) -> float:
    """Compute risk score using logarithmic scaling.

    Formula: risk = log2(dependents + 1) * criticality_weight * log2(lines_changed + 1)
    """
    dep_factor = math.log2(dependents_count + 1)
    change_factor = math.log2(lines_changed + 1)
    return dep_factor * criticality_weight * change_factor


def risk_label(score: float) -> str:
    """Convert a numeric risk score to a label."""
    for label, (low, high) in RISK_THRESHOLDS.items():
        if low <= score < high:
            return label
    return "CRITICAL"


# ---------------------------------------------------------------------------
# Test coverage gap detection
# ---------------------------------------------------------------------------

def find_test_file(filepath: str, repo_root: str) -> Optional[str]:
    """Find the test file corresponding to a source file.

    Checks multiple common patterns for test file location.
    """
    p = Path(filepath)
    if not p.suffix == ".py":
        return None
    # Don't look for tests of test files
    if p.stem.startswith("test_") or p.stem.endswith("_test"):
        return filepath  # It IS a test file

    stem = p.stem
    parent = str(p.parent) if str(p.parent) != "." else ""

    for pattern in TEST_FILE_PATTERNS:
        candidate = pattern.format(stem=stem, parent=parent)
        # Normalize path
        candidate = candidate.lstrip("/").replace("//", "/")
        abs_candidate = os.path.join(repo_root, candidate)
        if os.path.exists(abs_candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Analysis orchestration
# ---------------------------------------------------------------------------

def analyze(
    changes: List[FileChange],
    repo_root: str,
    scan_dirs: Optional[List[str]] = None,
) -> ImpactReport:
    """Run full impact analysis on a list of file changes.

    Returns an ImpactReport with per-file and aggregate risk data.
    """
    if not changes:
        return ImpactReport()

    changed_paths = [c.path for c in changes]

    # Build reverse dependency map for Python files
    reverse_deps = build_reverse_dependency_map(changed_paths, repo_root, scan_dirs)

    # Analyze each file
    file_impacts = []
    total_adds = 0
    total_dels = 0
    total_rdeps = 0
    untested = []
    all_reviewers: Counter = Counter()

    for change in changes:
        lines_changed = change.additions + change.deletions
        total_adds += change.additions
        total_dels += change.deletions

        # Criticality
        crit_weight, crit_label = get_criticality(change.path)

        # Reverse deps
        rdeps = reverse_deps.get(change.path, [])
        total_rdeps += len(rdeps)

        # Risk score
        score = compute_risk_score(len(rdeps), crit_weight, lines_changed)
        label = risk_label(score)

        # Test coverage
        test_file = find_test_file(change.path, repo_root)
        has_test = test_file is not None
        if not has_test and change.path.endswith(".py"):
            # Only flag Python source files (not configs, docs)
            is_test = change.path.startswith("test") or "/test" in change.path
            if not is_test:
                untested.append(change.path)

        # Reviewers
        contributors = get_file_contributors(change.path, top_n=3, cwd=repo_root)
        for name, count in contributors:
            all_reviewers[name] += count

        file_impacts.append(FileImpact(
            path=change.path,
            additions=change.additions,
            deletions=change.deletions,
            lines_changed=lines_changed,
            change_type=change.change_type,
            reverse_deps=rdeps,
            has_test=has_test,
            test_file=test_file or "",
            criticality=crit_weight,
            criticality_label=crit_label,
            risk_score=round(score, 2),
            risk_label=label,
            suggested_reviewers=[name for name, _ in contributors],
        ))

    # Sort by risk score descending
    file_impacts.sort(key=lambda f: f.risk_score, reverse=True)

    # Overall risk: max of individual scores (blast radius = worst case)
    overall_score = max((f.risk_score for f in file_impacts), default=0.0)
    overall_label = risk_label(overall_score)

    # Top reviewers across all files
    top_reviewers = all_reviewers.most_common(5)

    return ImpactReport(
        files=file_impacts,
        overall_risk=round(overall_score, 2),
        overall_label=overall_label,
        total_additions=total_adds,
        total_deletions=total_dels,
        total_reverse_deps=total_rdeps,
        untested_files=untested,
        top_reviewers=top_reviewers,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

RISK_GAUGE = {
    "LOW": "[====                ] LOW",
    "MEDIUM": "[=========           ] MEDIUM",
    "HIGH": "[===============     ] HIGH",
    "CRITICAL": "[====================] CRITICAL",
}

RISK_COLORS = {
    "LOW": "\033[32m",       # green
    "MEDIUM": "\033[33m",    # yellow
    "HIGH": "\033[31m",      # red
    "CRITICAL": "\033[1;31m",  # bold red
}
RESET = "\033[0m"


def _colored(text: str, risk: str) -> str:
    """Apply ANSI color based on risk level (no-op if not a TTY)."""
    if not sys.stdout.isatty():
        return text
    return f"{RISK_COLORS.get(risk, '')}{text}{RESET}"


def format_bordered_report(report: ImpactReport) -> str:
    """Format the report as a bordered text report with risk gauge."""
    lines = []
    w = 72
    border_top = "+" + "=" * (w - 2) + "+"
    border_mid = "+" + "-" * (w - 2) + "+"
    border_bot = "+" + "=" * (w - 2) + "+"

    def pad(text: str) -> str:
        """Pad text to fit within border."""
        # Strip ANSI for length calculation
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        padding = max(0, w - 4 - len(clean))
        return f"| {text}{' ' * padding} |"

    lines.append(border_top)
    lines.append(pad("COMMIT IMPACT ANALYSIS"))
    lines.append(border_mid)

    # Risk gauge
    gauge = RISK_GAUGE.get(report.overall_label, RISK_GAUGE["LOW"])
    lines.append(pad(f"Risk: {_colored(gauge, report.overall_label)}"))
    lines.append(pad(f"Score: {report.overall_risk}"))
    lines.append(pad(f"Files changed: {len(report.files)}  |  "
                      f"+{report.total_additions} -{report.total_deletions}  |  "
                      f"Reverse deps: {report.total_reverse_deps}"))
    lines.append(border_mid)

    # Per-file details
    lines.append(pad("AFFECTED FILES"))
    lines.append(pad(""))
    for fi in report.files:
        risk_str = _colored(f"[{fi.risk_label}]", fi.risk_label)
        lines.append(pad(f"  {risk_str} {fi.path}"))
        lines.append(pad(f"    +{fi.additions} -{fi.deletions} | "
                          f"criticality: {fi.criticality_label} ({fi.criticality}x) | "
                          f"risk: {fi.risk_score}"))
        if fi.reverse_deps:
            lines.append(pad(f"    reverse deps ({len(fi.reverse_deps)}):"))
            for rd in fi.reverse_deps[:5]:
                lines.append(pad(f"      <- {rd.importer_path} (L{rd.line_number})"))
            if len(fi.reverse_deps) > 5:
                lines.append(pad(f"      ... and {len(fi.reverse_deps) - 5} more"))
        if fi.has_test:
            lines.append(pad(f"    test: {fi.test_file}"))
        elif fi.path.endswith(".py") and fi.criticality_label != "test":
            lines.append(pad(f"    test: MISSING"))
        lines.append(pad(""))

    # Test coverage gaps
    if report.untested_files:
        lines.append(border_mid)
        lines.append(pad("TEST COVERAGE GAPS"))
        lines.append(pad(""))
        for uf in report.untested_files:
            lines.append(pad(f"  ! {uf}"))
        lines.append(pad(""))

    # Suggested reviewers
    if report.top_reviewers:
        lines.append(border_mid)
        lines.append(pad("SUGGESTED REVIEWERS"))
        lines.append(pad(""))
        for name, count in report.top_reviewers:
            lines.append(pad(f"  {name} ({count} commits)"))
        lines.append(pad(""))

    lines.append(border_bot)
    return "\n".join(lines)


def format_json(report: ImpactReport) -> str:
    """Format the report as JSON."""
    data = {
        "overall_risk": report.overall_risk,
        "overall_label": report.overall_label,
        "total_additions": report.total_additions,
        "total_deletions": report.total_deletions,
        "total_reverse_deps": report.total_reverse_deps,
        "untested_files": report.untested_files,
        "top_reviewers": [{"name": n, "commits": c} for n, c in report.top_reviewers],
        "files": [
            {
                "path": f.path,
                "additions": f.additions,
                "deletions": f.deletions,
                "lines_changed": f.lines_changed,
                "change_type": f.change_type,
                "criticality": f.criticality,
                "criticality_label": f.criticality_label,
                "risk_score": f.risk_score,
                "risk_label": f.risk_label,
                "has_test": f.has_test,
                "test_file": f.test_file,
                "reverse_deps": [
                    {
                        "importer": rd.importer_path,
                        "import_name": rd.import_name,
                        "line": rd.line_number,
                    }
                    for rd in f.reverse_deps
                ],
                "suggested_reviewers": f.suggested_reviewers,
            }
            for f in report.files
        ],
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Predict the blast radius of staged or recent changes.",
    )
    parser.add_argument(
        "--ref",
        help="Git ref to diff against (e.g. HEAD~3, main). Default: staged changes.",
    )
    parser.add_argument(
        "--file",
        help="Analyze impact of a specific file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON instead of bordered report.",
    )
    parser.add_argument(
        "--scan-dir",
        action="append",
        help="Additional directories to scan for reverse deps (can be repeated).",
    )
    parser.add_argument(
        "--repo",
        help="Repository root (auto-detected if not specified).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Determine repo root
    repo_root = args.repo or get_repo_root()
    if not repo_root:
        print("Error: not inside a git repository.", file=sys.stderr)
        return 1

    # Get changes
    if args.file:
        changes = get_changed_files_for_file(args.file, cwd=repo_root)
        if not changes:
            print(f"No changes detected for {args.file}", file=sys.stderr)
            return 1
    elif args.ref:
        changes = get_changed_files_ref(args.ref, cwd=repo_root)
    else:
        changes = get_changed_files_staged(cwd=repo_root)

    if not changes:
        print("No changes to analyze.", file=sys.stderr)
        return 0

    # Run analysis
    report = analyze(changes, repo_root, scan_dirs=args.scan_dir)

    # Output
    if args.json_output:
        print(format_json(report))
    else:
        print(format_bordered_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
