"""Code Health Dashboard - unified codebase health analysis.

Runs complexity profiling, documentation coverage, technical debt mapping,
dependency health checks, and contributor diversity analysis. Produces a
single weighted report with grades, trends, and actionable recommendations.

Functional API only. No imports from other GRIP libraries.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class HealthDimension:
    name: str           # "complexity", "doc_coverage", "tech_debt", "dependency_health", "contributor_diversity"
    score: float        # 0-100
    grade: str          # A-F
    summary: str        # One-line summary
    details: list       # Top issues/highlights
    trend: str          # "improving", "stable", "declining"


@dataclass
class CodeHealthReport:
    overall_score: float    # Weighted average 0-100
    overall_grade: str      # A-F
    dimensions: list        # List[HealthDimension]
    recommendations: list   # Top 3 actionable recommendations
    generated_at: str
    repo_path: str


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIMENSION_WEIGHTS = {
    "complexity": 0.25,
    "doc_coverage": 0.20,
    "tech_debt": 0.20,
    "dependency_health": 0.15,
    "contributor_diversity": 0.20,
}

ALL_DIMENSIONS = list(DIMENSION_WEIGHTS.keys())

GRADE_THRESHOLDS = [
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
    (0, "F"),
]

TECH_DEBT_PATTERNS = re.compile(
    r"\b(TODO|FIXME|HACK|XXX|KLUDGE|WORKAROUND)\b", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_to_grade(score: float) -> str:
    """Convert a 0-100 score to a letter grade."""
    clamped = max(0.0, min(100.0, score))
    for threshold, grade in GRADE_THRESHOLDS:
        if clamped >= threshold:
            return grade
    return "F"


def _python_files(path: str) -> list[Path]:
    """Collect all .py files under *path*, skipping common junk dirs."""
    skip = {
        "node_modules", ".next", "dist", "build", "__pycache__",
        "venv", ".venv", "target", "vendor", "Pods", ".git",
        ".tox", ".mypy_cache", ".pytest_cache", "egg-info",
    }
    result: list[Path] = []
    root = Path(path).resolve()
    if root.is_file() and root.suffix == ".py":
        return [root]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.endswith(".egg-info")]
        for fn in filenames:
            if fn.endswith(".py"):
                result.append(Path(dirpath) / fn)
    return result


def _count_lines(filepath: Path) -> int:
    """Count non-blank lines in a file."""
    try:
        text = filepath.read_text(errors="replace")
        return sum(1 for line in text.splitlines() if line.strip())
    except (OSError, UnicodeDecodeError):
        return 0


# ---------------------------------------------------------------------------
# Complexity scoring
# ---------------------------------------------------------------------------

def _nesting_complexity(node: ast.AST, depth: int = 0) -> int:
    """Approximate McCabe-like complexity via nesting depth counting.

    Returns the max nesting depth found inside *node*.  Control-flow nodes
    (if/for/while/with/try/except) each add 1 to the depth.
    """
    max_depth = depth
    control_types = (
        ast.If, ast.For, ast.While, ast.With,
        ast.Try, ast.ExceptHandler,
        ast.AsyncFor, ast.AsyncWith,
    )
    # Python 3.11+ has ast.TryStar
    if hasattr(ast, "TryStar"):
        control_types = (*control_types, ast.TryStar)

    for child in ast.iter_child_nodes(node):
        if isinstance(child, control_types):
            child_depth = _nesting_complexity(child, depth + 1)
        else:
            child_depth = _nesting_complexity(child, depth)
        max_depth = max(max_depth, child_depth)
    return max_depth


def _analyze_complexity(filepath: Path) -> list[dict]:
    """Return list of {name, complexity} for every function/method in file."""
    try:
        source = filepath.read_text(errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cx = _nesting_complexity(node) + 1  # base complexity = 1
            results.append({"name": node.name, "file": str(filepath), "complexity": cx})
    return results


def score_complexity(path: str = ".") -> HealthDimension:
    """Score codebase complexity. 100 = all functions have McCabe <= 10."""
    files = _python_files(path)
    if not files:
        return HealthDimension(
            name="complexity",
            score=100.0,
            grade="A",
            summary="No Python files found",
            details=[],
            trend="stable",
        )

    all_funcs = []
    for f in files:
        all_funcs.extend(_analyze_complexity(f))

    if not all_funcs:
        return HealthDimension(
            name="complexity",
            score=100.0,
            grade="A",
            summary="No functions found",
            details=[],
            trend="stable",
        )

    low = sum(1 for fn in all_funcs if fn["complexity"] <= 10)
    total = len(all_funcs)
    score = (low / total) * 100.0

    hotspots = sorted(all_funcs, key=lambda x: x["complexity"], reverse=True)[:5]
    details = [
        f"{h['name']} (complexity {h['complexity']}) in {h['file']}"
        for h in hotspots if h["complexity"] > 10
    ]
    if not details:
        details = [f"{total} functions analyzed, all within threshold"]

    return HealthDimension(
        name="complexity",
        score=round(score, 1),
        grade=_score_to_grade(score),
        summary=f"{low}/{total} functions have low complexity (McCabe <= 10)",
        details=details,
        trend="stable",
    )


# ---------------------------------------------------------------------------
# Documentation coverage scoring
# ---------------------------------------------------------------------------

def _has_docstring(node: ast.AST) -> bool:
    """Check if a function/class node has a docstring."""
    if not hasattr(node, "body") or not node.body:
        return False
    first = node.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        return isinstance(first.value.value, str)
    return False


def _analyze_doc_coverage(filepath: Path) -> dict:
    """Return {total, documented} counts for functions/classes/methods."""
    try:
        source = filepath.read_text(errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return {"total": 0, "documented": 0, "undocumented": []}

    total = 0
    documented = 0
    undocumented: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            total += 1
            if _has_docstring(node):
                documented += 1
            else:
                undocumented.append(f"{node.name} in {filepath}")

    return {"total": total, "documented": documented, "undocumented": undocumented}


def score_doc_coverage(path: str = ".") -> HealthDimension:
    """Score documentation coverage. % of functions/classes with docstrings."""
    files = _python_files(path)
    if not files:
        return HealthDimension(
            name="doc_coverage",
            score=100.0,
            grade="A",
            summary="No Python files found",
            details=[],
            trend="stable",
        )

    total = 0
    documented = 0
    all_undoc: list[str] = []

    for f in files:
        result = _analyze_doc_coverage(f)
        total += result["total"]
        documented += result["documented"]
        all_undoc.extend(result["undocumented"])

    if total == 0:
        return HealthDimension(
            name="doc_coverage",
            score=100.0,
            grade="A",
            summary="No functions or classes found",
            details=[],
            trend="stable",
        )

    score = (documented / total) * 100.0
    details = all_undoc[:10]  # Top 10 undocumented

    return HealthDimension(
        name="doc_coverage",
        score=round(score, 1),
        grade=_score_to_grade(score),
        summary=f"{documented}/{total} functions/classes have docstrings ({score:.0f}%)",
        details=details,
        trend="stable",
    )


# ---------------------------------------------------------------------------
# Tech debt scoring
# ---------------------------------------------------------------------------

def _count_debt_markers(filepath: Path) -> list[dict]:
    """Find TODO/FIXME/HACK markers in a file."""
    markers = []
    try:
        lines = filepath.read_text(errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    for i, line in enumerate(lines, 1):
        match = TECH_DEBT_PATTERNS.search(line)
        if match:
            markers.append({
                "file": str(filepath),
                "line": i,
                "type": match.group(1).upper(),
                "text": line.strip()[:120],
            })
    return markers


def score_tech_debt(path: str = ".") -> HealthDimension:
    """Score tech debt density. 0 markers per 1K lines = 100, 10+ = 0."""
    root = Path(path).resolve()

    # Scan all text files, not just Python
    skip = {
        "node_modules", ".next", "dist", "build", "__pycache__",
        "venv", ".venv", "target", "vendor", "Pods", ".git",
        ".tox", ".mypy_cache", ".pytest_cache",
    }
    text_extensions = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".rb", ".go",
        ".rs", ".java", ".c", ".cpp", ".h", ".hpp", ".sh",
        ".yaml", ".yml", ".toml", ".cfg", ".ini", ".md",
    }

    total_lines = 0
    all_markers: list[dict] = []

    if root.is_file():
        files_to_scan = [root]
    else:
        files_to_scan = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for fn in filenames:
                fp = Path(dirpath) / fn
                if fp.suffix in text_extensions:
                    files_to_scan.append(fp)

    for fp in files_to_scan:
        total_lines += _count_lines(fp)
        all_markers.extend(_count_debt_markers(fp))

    if total_lines == 0:
        return HealthDimension(
            name="tech_debt",
            score=100.0,
            grade="A",
            summary="No source files found",
            details=[],
            trend="stable",
        )

    density = (len(all_markers) / total_lines) * 1000  # per 1K lines
    # Linear: 0 density = 100, 10 density = 0
    score = max(0.0, min(100.0, 100.0 - (density * 10.0)))

    details = [
        f"{m['type']} at {m['file']}:{m['line']} - {m['text']}"
        for m in all_markers[:10]
    ]

    return HealthDimension(
        name="tech_debt",
        score=round(score, 1),
        grade=_score_to_grade(score),
        summary=f"{len(all_markers)} debt markers in {total_lines} lines ({density:.1f} per 1K lines)",
        details=details,
        trend="stable",
    )


# ---------------------------------------------------------------------------
# Dependency health scoring
# ---------------------------------------------------------------------------

def _parse_requirements_txt(path: Path) -> list[dict]:
    """Parse requirements.txt into list of {name, spec}."""
    deps = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # Handle name==version, name>=version, name~=version, bare name
        match = re.match(r"^([a-zA-Z0-9_.-]+)\s*([><=~!]+\s*[\d.*]+)?", line)
        if match:
            name = match.group(1)
            spec = match.group(2) or ""
            pinned = "==" in spec
            deps.append({"name": name, "spec": spec.strip(), "pinned": pinned})
    return deps


def _parse_package_json(path: Path) -> list[dict]:
    """Parse package.json dependencies."""
    deps = []
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []

    for section in ("dependencies", "devDependencies"):
        for name, version in data.get(section, {}).items():
            pinned = bool(re.match(r"^\d", version))  # starts with digit = pinned
            deps.append({"name": name, "spec": version, "pinned": pinned})
    return deps


def score_dependency_health(path: str = ".") -> HealthDimension:
    """Score dependency health. % of deps with pinned versions."""
    root = Path(path).resolve()
    all_deps: list[dict] = []

    # Check requirements.txt
    req_file = root / "requirements.txt"
    if req_file.exists():
        all_deps.extend(_parse_requirements_txt(req_file))

    # Check package.json
    pkg_file = root / "package.json"
    if pkg_file.exists():
        all_deps.extend(_parse_package_json(pkg_file))

    # Check pyproject.toml (basic)
    pyproject = root / "pyproject.toml"
    if pyproject.exists() and not all_deps:
        try:
            text = pyproject.read_text(errors="replace")
            # Simple regex for dependencies = ["name>=ver", ...]
            dep_matches = re.findall(r'"([a-zA-Z0-9_.-]+)([><=~!]+[\d.*]+)?"', text)
            for name, spec in dep_matches:
                if name and not name.startswith(("python", "requires")):
                    pinned = "==" in spec if spec else False
                    all_deps.append({"name": name, "spec": spec, "pinned": pinned})
        except (OSError, UnicodeDecodeError):
            pass

    if not all_deps:
        return HealthDimension(
            name="dependency_health",
            score=100.0,
            grade="A",
            summary="No dependency files found",
            details=[],
            trend="stable",
        )

    pinned_count = sum(1 for d in all_deps if d["pinned"])
    total = len(all_deps)
    score = (pinned_count / total) * 100.0

    unpinned = [d for d in all_deps if not d["pinned"]]
    details = [
        f"{d['name']} {d['spec']} (not pinned)" for d in unpinned[:10]
    ]

    return HealthDimension(
        name="dependency_health",
        score=round(score, 1),
        grade=_score_to_grade(score),
        summary=f"{pinned_count}/{total} dependencies are version-pinned",
        details=details,
        trend="stable",
    )


# ---------------------------------------------------------------------------
# Contributor diversity scoring
# ---------------------------------------------------------------------------

def _get_git_contributors(path: str) -> list[dict]:
    """Get contributor commit counts via git shortlog."""
    try:
        result = subprocess.run(
            ["git", "shortlog", "-sn", "--all", "--no-merges"],
            capture_output=True,
            text=True,
            cwd=path,
            timeout=30,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []

    contributors = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if match:
            contributors.append({
                "name": match.group(2).strip(),
                "commits": int(match.group(1)),
            })
    return contributors


def _calculate_bus_factor(contributors: list[dict]) -> int:
    """Calculate bus factor: min contributors covering 50%+ of commits."""
    if not contributors:
        return 0
    total = sum(c["commits"] for c in contributors)
    if total == 0:
        return 0

    sorted_contribs = sorted(contributors, key=lambda x: x["commits"], reverse=True)
    cumulative = 0
    for i, c in enumerate(sorted_contribs):
        cumulative += c["commits"]
        if cumulative >= total * 0.5:
            return i + 1
    return len(sorted_contribs)


def score_contributor_diversity(path: str = ".") -> HealthDimension:
    """Score contributor diversity via bus factor analysis."""
    contributors = _get_git_contributors(path)

    if not contributors:
        return HealthDimension(
            name="contributor_diversity",
            score=50.0,
            grade="F",
            summary="No git history found or not a git repository",
            details=["Unable to analyze contributor data"],
            trend="stable",
        )

    bus_factor = _calculate_bus_factor(contributors)
    total_contributors = len(contributors)

    # Score mapping: 1 person = 30, 2 = 60, 3+ = 80, 5+ = 100
    if bus_factor >= 5:
        score = 100.0
    elif bus_factor >= 3:
        score = 80.0
    elif bus_factor >= 2:
        score = 60.0
    elif bus_factor >= 1:
        score = 30.0
    else:
        score = 0.0

    details = [
        f"{c['name']}: {c['commits']} commits"
        for c in sorted(contributors, key=lambda x: x["commits"], reverse=True)[:5]
    ]

    return HealthDimension(
        name="contributor_diversity",
        score=score,
        grade=_score_to_grade(score),
        summary=f"Bus factor: {bus_factor} (from {total_contributors} contributors)",
        details=details,
        trend="stable",
    )


# ---------------------------------------------------------------------------
# Overall calculation
# ---------------------------------------------------------------------------

def calculate_overall(dimensions: list) -> tuple[float, str]:
    """Calculate weighted average score and grade from dimensions."""
    if not dimensions:
        return 0.0, "F"

    total_weight = 0.0
    weighted_sum = 0.0

    for dim in dimensions:
        weight = DIMENSION_WEIGHTS.get(dim.name, 0.0)
        weighted_sum += dim.score * weight
        total_weight += weight

    if total_weight == 0:
        return 0.0, "F"

    score = weighted_sum / total_weight
    score = round(score, 1)
    return score, _score_to_grade(score)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def generate_recommendations(dimensions: list) -> list[str]:
    """Generate top 3 actionable recommendations from dimension scores."""
    if not dimensions:
        return ["Run a full health check to establish a baseline"]

    recs_map = {
        "complexity": {
            "low": "Refactor high-complexity functions (McCabe > 10) by extracting helper functions",
            "mid": "Review complexity hotspots and consider breaking down the largest functions",
            "high": "Complexity is well-managed. Consider adding complexity linting to CI",
        },
        "doc_coverage": {
            "low": "Add docstrings to public functions and classes - coverage is critically low",
            "mid": "Improve documentation coverage by focusing on public API functions first",
            "high": "Documentation coverage is strong. Consider adding usage examples",
        },
        "tech_debt": {
            "low": "Schedule a tech debt sprint to address accumulated TODO/FIXME markers",
            "mid": "Create issues for existing TODO/FIXME items and triage by priority",
            "high": "Tech debt is well-managed. Keep tracking markers in CI",
        },
        "dependency_health": {
            "low": "Pin all dependency versions to prevent supply-chain drift",
            "mid": "Review and pin unpinned dependencies, especially production ones",
            "high": "Dependencies are well-managed. Consider automated update checks",
        },
        "contributor_diversity": {
            "low": "Critical bus factor risk - cross-train team members on key components",
            "mid": "Improve knowledge sharing through code reviews and pair programming",
            "high": "Good contributor diversity. Maintain through regular knowledge sharing",
        },
    }

    scored = sorted(dimensions, key=lambda d: d.score)
    recommendations = []

    for dim in scored:
        if dim.name not in recs_map:
            continue
        if dim.score < 60:
            level = "low"
        elif dim.score < 80:
            level = "mid"
        else:
            level = "high"
        recommendations.append(recs_map[dim.name][level])

    return recommendations[:3]


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

DIMENSION_FUNCTIONS = {
    "complexity": score_complexity,
    "doc_coverage": score_doc_coverage,
    "tech_debt": score_tech_debt,
    "dependency_health": score_dependency_health,
    "contributor_diversity": score_contributor_diversity,
}


def analyze_health(path: str = ".", dimensions: Optional[list[str]] = None) -> CodeHealthReport:
    """Run full health analysis. Optionally limit to specific dimensions."""
    selected = dimensions or ALL_DIMENSIONS
    selected = [d for d in selected if d in DIMENSION_FUNCTIONS]

    results = []
    for dim_name in selected:
        fn = DIMENSION_FUNCTIONS[dim_name]
        results.append(fn(path))

    overall_score, overall_grade = calculate_overall(results)
    recommendations = generate_recommendations(results)

    return CodeHealthReport(
        overall_score=overall_score,
        overall_grade=overall_grade,
        dimensions=results,
        recommendations=recommendations,
        generated_at=datetime.now(timezone.utc).isoformat(),
        repo_path=str(Path(path).resolve()),
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_text(report: CodeHealthReport) -> str:
    """Format report as plain text."""
    lines = []
    lines.append("=" * 60)
    lines.append("CODE HEALTH DASHBOARD")
    lines.append("=" * 60)
    lines.append(f"Repository: {report.repo_path}")
    lines.append(f"Generated:  {report.generated_at}")
    lines.append(f"Overall:    {report.overall_score}/100 ({report.overall_grade})")
    lines.append("")

    for dim in report.dimensions:
        lines.append(f"  [{dim.grade}] {dim.name}: {dim.score}/100")
        lines.append(f"      {dim.summary}")
        for detail in dim.details[:3]:
            lines.append(f"      - {detail}")
        lines.append("")

    if report.recommendations:
        lines.append("RECOMMENDATIONS:")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_slack(report: CodeHealthReport) -> str:
    """Format report as Slack mrkdwn. Uses *bold* and _italic_."""
    lines = []
    lines.append(f"*Code Health Dashboard* - {report.overall_grade} ({report.overall_score}/100)")
    lines.append(f"_Repository: {report.repo_path}_")
    lines.append("")

    grade_emoji = {"A": ":white_check_mark:", "B": ":large_green_circle:", "C": ":warning:", "D": ":red_circle:", "F": ":fire:"}

    for dim in report.dimensions:
        emoji = grade_emoji.get(dim.grade, ":question:")
        lines.append(f"{emoji} *{dim.name}*: {dim.score}/100 ({dim.grade})")
        lines.append(f"    {dim.summary}")

    if report.recommendations:
        lines.append("")
        lines.append("*Recommendations:*")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")

    return "\n".join(lines)


def format_json(report: CodeHealthReport) -> str:
    """Format report as JSON string."""
    data = asdict(report)
    return json.dumps(data, indent=2)


def format_digest(report: CodeHealthReport) -> dict:
    """Format for grip_digest MCP app. One card per dimension."""
    cards = []
    for dim in report.dimensions:
        if dim.grade in ("A", "B"):
            status = "success"
        elif dim.grade == "C":
            status = "warning"
        else:
            status = "error"

        cards.append({
            "title": dim.name.replace("_", " ").title(),
            "status": status,
            "score": dim.score,
            "grade": dim.grade,
            "summary": dim.summary,
            "details": dim.details[:3],
        })

    return {
        "title": "Code Health Dashboard",
        "overall_score": report.overall_score,
        "overall_grade": report.overall_grade,
        "cards": cards,
        "recommendations": report.recommendations,
        "generated_at": report.generated_at,
    }


def format_gap(report: CodeHealthReport) -> dict:
    """Format for grip_gap_visualiser. Current vs target scores."""
    targets = {
        "complexity": 90.0,
        "doc_coverage": 80.0,
        "tech_debt": 85.0,
        "dependency_health": 95.0,
        "contributor_diversity": 80.0,
    }

    dimensions = []
    for dim in report.dimensions:
        target = targets.get(dim.name, 80.0)
        gap = target - dim.score
        dimensions.append({
            "name": dim.name,
            "current": dim.score,
            "target": target,
            "gap": round(max(0.0, gap), 1),
            "grade": dim.grade,
            "on_target": dim.score >= target,
        })

    return {
        "title": "Code Health Gap Analysis",
        "overall_current": report.overall_score,
        "overall_target": 85.0,
        "overall_gap": round(max(0.0, 85.0 - report.overall_score), 1),
        "dimensions": dimensions,
        "generated_at": report.generated_at,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Code Health Dashboard")
    parser.add_argument("--path", default=".", help="Repository path to analyze")
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "digest", "gap"],
        default="text",
        help="Output format",
    )
    parser.add_argument(
        "--dimensions",
        default=None,
        help="Comma-separated list of dimensions to analyze",
    )
    args = parser.parse_args()

    dims = args.dimensions.split(",") if args.dimensions else None
    report = analyze_health(args.path, dims)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "digest": lambda r: json.dumps(format_digest(r), indent=2),
        "gap": lambda r: json.dumps(format_gap(r), indent=2),
    }

    output = formatters[args.format](report)
    print(output)


if __name__ == "__main__":
    main()
