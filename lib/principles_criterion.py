#!/usr/bin/env python3
"""
principles_criterion.py — Design Principles as Convergence Signals.

Turns all 6 GRIP design principles into measurable, automatable signals:

    DRY   → dup_density       (dedup_scanner.py)
    KISS  → avg_complexity    (cyclomatic complexity via AST)
    SOLID → coupling_score    (afferent coupling between modules)
    GRASP → responsibility    (methods per class, fan-in/fan-out)
    YAGNI → dead_code_ratio   (unreachable functions, unused imports)
    YSH   → pattern_count     (repeated patterns awaiting abstraction)

Each principle maps to a Signal in an AttestCriterion, enabling:

    result = converge(
        state=principles_scan(path),
        criterion=principles_criterion(),
        improve=principles_improve,
        max_depth=5,
    )

ARTICLE: Issue #202 — Design Principles as Convergence Signals
"""

import ast
import itertools
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.attest import Signal, AttestCriterion
from lib.converge import converge_iter, ConvergenceResult


# ── Principle score dataclass ───────────────────────────────────────────────

@dataclass
class PrinciplesReport:
    """Snapshot of how well a codebase embodies the 8 GRIP design principles.

    Scores are in [0.0, 1.0] where 1.0 = perfectly principled.
    All 8: DRY, KISS, CC, Big-O, SOLID, GRASP, YAGNI, YSH.
    """
    path: str = "."

    # Per-principle scores (higher = better)
    dry_score:   float = 1.0   # 1 - dup_density
    kiss_score:  float = 1.0   # 1 - normalised cyclomatic complexity
    solid_score: float = 1.0   # 1 - coupling_ratio
    grasp_score: float = 1.0   # 1 - responsibility_ratio
    yagni_score: float = 1.0   # 1 - dead_code_ratio
    ysh_score:   float = 1.0   # pattern_repetitions / 3x_threshold
    cc_score:    float = 1.0   # 1 - (offenders / total_functions)
    bigo_score:  float = 1.0   # 1 - (quadratic_functions / total_functions)

    # Raw diagnostics
    dup_density:        float = 0.0
    avg_complexity:     float = 0.0
    coupling_ratio:     float = 0.0
    responsibility:     float = 0.0
    dead_code_ratio:    float = 0.0
    pattern_count:      int   = 0
    file_count:         int   = 0
    lines_total:        int   = 0
    cc_offender_count:  int   = 0   # functions above CC threshold
    max_loop_depth:     int   = 0   # deepest nested loop across all functions

    # Detail storage
    violations: list[str] = field(default_factory=list)

    @property
    def composite_score(self) -> float:
        """Weighted average across all 8 principles."""
        weights = {
            "dry":   0.15,
            "kiss":  0.10,
            "cc":    0.10,
            "bigo":  0.10,
            "solid": 0.15,
            "grasp": 0.10,
            "yagni": 0.15,
            "ysh":   0.15,
        }
        return (
            weights["dry"]   * self.dry_score
            + weights["kiss"]  * self.kiss_score
            + weights["cc"]    * self.cc_score
            + weights["bigo"]  * self.bigo_score
            + weights["solid"] * self.solid_score
            + weights["grasp"] * self.grasp_score
            + weights["yagni"] * self.yagni_score
            + weights["ysh"]   * self.ysh_score
        )

    def summary(self) -> str:
        lines = [
            f"PrinciplesReport — {self.path}",
            f"Composite score : {self.composite_score:.1%}",
            f"",
            f"  DRY   (dup_density={self.dup_density:.1%})     → {self.dry_score:.1%}",
            f"  KISS  (avg_complexity={self.avg_complexity:.1f})   → {self.kiss_score:.1%}",
            f"  CC    (offenders={self.cc_offender_count})       → {self.cc_score:.1%}",
            f"  BIG-O (max_depth={self.max_loop_depth})          → {self.bigo_score:.1%}",
            f"  SOLID (coupling={self.coupling_ratio:.1%})        → {self.solid_score:.1%}",
            f"  GRASP (responsibility={self.responsibility:.1f}) → {self.grasp_score:.1%}",
            f"  YAGNI (dead_code={self.dead_code_ratio:.1%})     → {self.yagni_score:.1%}",
            f"  YSH   (patterns×{self.pattern_count})           → {self.ysh_score:.1%}",
        ]
        if self.violations:
            lines.append("\nTop violations:")
            for v in self.violations[:5]:
                lines.append(f"  • {v}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "composite_score": self.composite_score,
            "dry_score":   self.dry_score,
            "kiss_score":  self.kiss_score,
            "cc_score":    self.cc_score,
            "bigo_score":  self.bigo_score,
            "solid_score": self.solid_score,
            "grasp_score": self.grasp_score,
            "yagni_score": self.yagni_score,
            "ysh_score":   self.ysh_score,
            "dup_density":       self.dup_density,
            "avg_complexity":    self.avg_complexity,
            "cc_offender_count": self.cc_offender_count,
            "max_loop_depth":    self.max_loop_depth,
            "coupling_ratio":    self.coupling_ratio,
            "dead_code_ratio":   self.dead_code_ratio,
            "pattern_count":     self.pattern_count,
            "file_count":        self.file_count,
            "violations":        self.violations[:10],
        }


# ── AST-based analysers ─────────────────────────────────────────────────────

def _cyclomatic_complexity(tree: ast.Module) -> list[float]:
    """Approximate cyclomatic complexity per function via branch node count."""
    complexities = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        branches = sum(
            1 for n in ast.walk(node)
            if isinstance(n, (
                ast.If, ast.For, ast.While, ast.ExceptHandler,
                ast.With, ast.Assert, ast.comprehension,
            ))
        )
        complexities.append(1 + branches)
    return complexities


def _methods_per_class(tree: ast.Module) -> list[int]:
    """Count methods per class for GRASP responsibility analysis."""
    counts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            counts.append(len(methods))
    return counts


MAX_SCAN_FILES = 3000  # Warn if more files found — prevents home-dir-scale scans


def _collect_py_files(path: Path, exclude: set[str] | None = None) -> list[Path]:
    """Walk path, yield .py files excluding venv/node_modules.

    Safety: aborts if file count exceeds MAX_SCAN_FILES to prevent
    runaway scans on non-project directories (e.g. home dir).
    """
    excluded = exclude or {
        "node_modules", ".next", "dist", "build", "__pycache__",
        "venv", ".venv", "target", "vendor", ".git",
        "Library", "Applications", "Downloads", "Documents",
        "Pictures", "Music", "Movies", "Desktop",
    }
    files: list[Path] = []
    for root, dirs, fnames in os.walk(path):
        dirs[:] = [d for d in dirs if d not in excluded and not d.startswith(".")]
        py_in_dir = [Path(root) / f for f in fnames if f.endswith(".py")]
        files.extend(py_in_dir)
        if len(files) > MAX_SCAN_FILES:
            print(
                f"[principles] WARNING: >{MAX_SCAN_FILES} .py files in {path}, truncating.",
                file=sys.stderr,
            )
            return files[:MAX_SCAN_FILES]
    return files


# ── Shared AST analysis (DRY: used by both scanners) ──────────────────────

_YSH_EXCLUDE = {
    "main", "setup", "teardown", "run", "start", "stop",
    "init", "reset", "close", "open", "load", "save",
    "parse", "format", "encode", "decode", "validate",
    "as_dict", "to_dict", "from_dict", "summary", "report",
    "scan", "check", "build", "create", "update", "delete",
    "get", "set", "add", "remove", "clear", "copy", "merge",
}


@dataclass
class _ASTResults:
    """Intermediate results from per-file AST analysis."""
    all_complexities: list[float] = field(default_factory=list)
    all_methods: list[int] = field(default_factory=list)
    module_imports: dict = field(default_factory=dict)
    lines_total: int = 0
    pattern_counts: dict = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    # CC + Big-O tracking
    cc_offenders: int = 0         # functions with CC > threshold
    total_functions: int = 0      # total function count for ratio
    max_loop_depth: int = 0       # deepest nested loop
    quadratic_functions: int = 0  # functions with loop depth >= 2


def _max_loop_depth(func_node: ast.AST) -> int:
    """Find maximum nesting depth of for/while loops in a function.

    Used for Big-O heuristic: depth >= 2 implies O(n^2)+.
    """
    max_depth = 0

    def _walk(node: ast.AST, depth: int) -> None:
        nonlocal max_depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While)):
                new_depth = depth + 1
                max_depth = max(max_depth, new_depth)
                _walk(child, new_depth)
            else:
                _walk(child, depth)

    _walk(func_node, 0)
    return max_depth


def _analyze_kiss_cc(fp: Path, tree: ast.AST, r: _ASTResults, cc_threshold: int) -> None:
    """KISS complexity + CC offender detection."""
    comps = _cyclomatic_complexity(tree)
    r.all_complexities.extend(comps)
    for c in comps:
        if c > 10:
            r.violations.append(f"KISS: {fp.name} complexity={c:.0f}")
        r.total_functions += 1
        if c > cc_threshold:
            r.cc_offenders += 1


def _analyze_bigo(fp: Path, tree: ast.AST, r: _ASTResults) -> None:
    """BIG-O: check loop nesting depth per function."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            depth = _max_loop_depth(node)
            r.max_loop_depth = max(r.max_loop_depth, depth)
            if depth >= 2:
                r.quadratic_functions += 1
                r.violations.append(
                    f"BIG-O: {fp.name}:{node.name}() has O(n^{depth}) nested loops"
                )


def _analyze_grasp(fp: Path, tree: ast.AST, r: _ASTResults) -> None:
    """GRASP: responsibility check via methods-per-class."""
    methods = _methods_per_class(tree)
    r.all_methods.extend(methods)
    for m in methods:
        if m > 15:
            r.violations.append(f"GRASP: {fp.name} class has {m} methods")


def _extract_import_names(node: ast.AST) -> list[str]:
    """Extract top-level module names from a single import node."""
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module.split(".")[0]]
    return []


def _analyze_solid(fp: Path, tree: ast.AST, r: _ASTResults) -> None:
    """SOLID: collect module-level imports for coupling analysis."""
    imports: set[str] = set()
    for node in ast.walk(tree):
        imports.update(_extract_import_names(node))
    r.module_imports[str(fp)] = imports


def _analyze_ysh(tree: ast.AST, r: _ASTResults) -> None:
    """YSH: track repeated function names across files."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = node.name
            if sig.startswith("test_") or sig.startswith("_"):
                continue
            if sig in _YSH_EXCLUDE or len(sig) <= 3:
                continue
            r.pattern_counts[sig] = r.pattern_counts.get(sig, 0) + 1


def _analyze_single_file(filepath: Path, r: _ASTResults, cc_threshold: int) -> None:
    """Analyse one Python file for all principle signals."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    r.lines_total += source.count("\n")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    _analyze_kiss_cc(filepath, tree, r, cc_threshold)
    _analyze_bigo(filepath, tree, r)
    _analyze_grasp(filepath, tree, r)
    _analyze_solid(filepath, tree, r)
    _analyze_ysh(tree, r)


def _analyze_files(py_files: list[Path]) -> _ASTResults:
    """Run AST analysis on a list of Python files."""
    r = _ASTResults()
    cc_threshold = 15
    for filepath in py_files:
        _analyze_single_file(filepath, r, cc_threshold)
    return r


def _ratio_to_solid(ratio: float) -> float:
    """Convert coupling ratio to SOLID score (0.0-1.0)."""
    return max(0.0, 1.0 - max(0, ratio - 0.3) / 0.7)


def _load_cross_repo_solid_evidence() -> dict | None:
    """Load cross-repo SOLID evidence from cube-state.json (e.g. HAL)."""
    cube_path = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude")) / "cache" / "cube-state.json"
    if not cube_path.exists():
        return None
    try:
        import json
        cube = json.loads(cube_path.read_text())
        return cube.get("cross_repo_evidence", {}).get("SOLID")
    except Exception:
        return None


def _blend_with_cross_repo(grip_score: float, grip_loc: int) -> float:
    """Blend GRIP SOLID score with cross-repo evidence, weighted by LOC."""
    evidence = _load_cross_repo_solid_evidence()
    if not evidence:
        return grip_score
    hal_loc = evidence.get("loc", 0)
    if hal_loc <= 0:
        return grip_score
    hal_ratio = evidence.get("coupling_ratio", 0.3)
    hal_score = _ratio_to_solid(hal_ratio)
    hal_weight = hal_loc / (grip_loc + hal_loc)
    return grip_score * (1 - hal_weight) + hal_score * hal_weight


def _compute_solid_score(report: PrinciplesReport, ast_r: _ASTResults) -> None:
    """SOLID score: prefer dependency_mapper instability, fallback to import ratio.

    Blends with cross-repo evidence (HAL) when available, weighted by LOC.
    """
    grip_loc = ast_r.lines_total or 167498
    try:
        from lib.dependency_mapper import build_graph, instability_score
        lib_path = Path(report.path) / "lib"
        graph = build_graph(str(lib_path) if lib_path.is_dir() else report.path)
        if graph.modules:
            report.coupling_ratio = instability_score(graph)
            report.solid_score = _blend_with_cross_repo(
                _ratio_to_solid(report.coupling_ratio), grip_loc)
            return
    except Exception:
        pass
    # Fallback: import-based coupling ratio (hash-based O(n) lookup)
    all_module_names = {Path(fp).stem for fp in ast_r.module_imports}
    all_imports = list(itertools.chain.from_iterable(ast_r.module_imports.values()))
    total_deps = len(all_imports)
    internal_deps = sum(1 for imp in all_imports if imp in all_module_names)
    if total_deps > 0:
        report.coupling_ratio = internal_deps / total_deps
        report.solid_score = _blend_with_cross_repo(
            _ratio_to_solid(report.coupling_ratio), grip_loc)


def _compute_scores(report: PrinciplesReport, ast_r: _ASTResults) -> None:
    """Compute all 8 principle scores from AST results."""
    report.lines_total = ast_r.lines_total

    if ast_r.all_complexities:
        report.avg_complexity = sum(ast_r.all_complexities) / len(ast_r.all_complexities)
        report.kiss_score = max(0.0, 1.0 - (report.avg_complexity - 1.0) / 19.0)

    report.cc_offender_count = ast_r.cc_offenders
    if ast_r.total_functions > 0:
        report.cc_score = max(0.0, 1.0 - (ast_r.cc_offenders / ast_r.total_functions) * 5.0)

    report.max_loop_depth = ast_r.max_loop_depth
    if ast_r.total_functions > 0:
        report.bigo_score = max(0.0, 1.0 - (ast_r.quadratic_functions / ast_r.total_functions) * 5.0)

    if ast_r.all_methods:
        report.responsibility = sum(ast_r.all_methods) / len(ast_r.all_methods)
        report.grasp_score = max(0.0, 1.0 - max(0, report.responsibility - 5) / 10.0)

    _compute_solid_score(report, ast_r)

    ysh_ready = [sig for sig, count in ast_r.pattern_counts.items() if count >= 3]
    report.pattern_count = len(ysh_ready)
    report.ysh_score = max(0.0, 1.0 - report.pattern_count / max(report.file_count, 1) / 2.0)
    for sig in ysh_ready[:3]:
        ast_r.violations.append(f"YSH: `{sig}()` appears {ast_r.pattern_counts[sig]}x — extract now")


# ── Main scanner ────────────────────────────────────────────────────────────

def _scan_dry(root: Path, violations: list[str]) -> tuple[float, float]:
    """DRY scan via dedup_scanner. Returns (dup_density, dry_score)."""
    try:
        from lib.dedup_scanner import DedupScanner
        dedup_report = DedupScanner(path=str(root)).scan()
        for cluster in dedup_report.top_clusters(3):
            locs = ", ".join(f"{fp}:{s}" for fp, s, _ in cluster.locations[:2])
            violations.append(f"DRY: {locs} ({cluster.wasted_tokens} wasted tokens)")
        return dedup_report.dup_density, 1.0 - min(1.0, dedup_report.dup_density)
    except Exception:
        return 0.0, 1.0


def _scan_yagni(root: Path, violations: list[str]) -> tuple[float, float]:
    """YAGNI scan via YAGNIScanner. Returns (dead_ratio, yagni_score)."""
    try:
        from lib.yagni_scanner import YAGNIScanner
        yagni_report = YAGNIScanner(path=str(root)).scan()
        for sym in yagni_report.top_symbols(3):
            violations.append(f"YAGNI: {sym.file}:{sym.line} — {sym.kind} `{sym.name}` unused")
        return yagni_report.dead_ratio, 1.0 - min(1.0, yagni_report.dead_ratio)
    except Exception:
        return 0.0, 1.0


def principles_scan(path: str = ".") -> PrinciplesReport:
    """Scan a codebase and return a PrinciplesReport.

    There is deliberately no "skip under pytest" short-circuit here. One stood
    from 2026-03-09 (#980) as a speed guard and returned an empty report -- which,
    because every score defaults to 1.0, was a PERFECT report. Measured 2026-08-26:
    every principles_scan() call in the whole suite passes a tmp_path fixture
    (2 files, 0.009s), so the expensive case the guard protected was never once
    exercised by a test, while the guard silently disabled the function under test.
    Gutting the real scanner beneath it changed no result. Scan a smaller path if
    you need speed; do not fabricate a report.
    """
    root = Path(path).expanduser().resolve()
    report = PrinciplesReport(path=str(root))

    py_files = _collect_py_files(root)
    report.file_count = len(py_files)
    if not py_files:
        return report

    violations: list[str] = []
    report.dup_density, report.dry_score = _scan_dry(root, violations)
    report.dead_code_ratio, report.yagni_score = _scan_yagni(root, violations)

    ast_r = _analyze_files(py_files)
    _compute_scores(report, ast_r)
    violations.extend(ast_r.violations)

    report.violations = violations
    return report


# ── File-scoped scanner (for CI diff analysis) ──────────────────────────────

def principles_scan_files(file_paths: list[Path]) -> PrinciplesReport:
    """Scan specific files and return a PrinciplesReport.

    Unlike principles_scan() which walks a directory, this operates
    on an explicit file list — ideal for diff-scoped CI analysis.
    Files that don't exist or fail to parse are silently skipped.
    """
    report = PrinciplesReport(path="<file-list>")

    py_files = [p for p in file_paths if str(p).endswith(".py") and p.exists()]
    report.file_count = len(py_files)

    if not py_files:
        return report

    # DRY via dedup_scanner (optional, needs single parent dir)
    try:
        from lib.dedup_scanner import DedupScanner
        parents = {p.resolve().parent for p in py_files}
        if len(parents) == 1:
            scanner = DedupScanner(path=str(parents.pop()))
            dedup_report = scanner.scan()
            report.dup_density = dedup_report.dup_density
            report.dry_score = 1.0 - min(1.0, report.dup_density)
    except Exception:
        pass

    # AST analysis + score computation (shared with principles_scan)
    ast_r = _analyze_files(py_files)
    _compute_scores(report, ast_r)

    report.violations = ast_r.violations
    return report


# ── Convergence integration ─────────────────────────────────────────────────

def principles_criterion(
    target: float = 0.80,
    dry_min: float = 0.70,
    kiss_min: float = 0.60,
    yagni_min: float = 0.70,
) -> Callable[[PrinciplesReport], bool]:
    """Returns a criterion for converge() — True when all principles pass.

    Args:
        target    : Minimum composite score (default 0.80 = 80%).
        dry_min   : Minimum DRY score (catches hidden duplication).
        kiss_min  : Minimum KISS score (catches accidental complexity).
        yagni_min : Minimum YAGNI score (catches dead code accumulation).
    """
    def criterion(report: PrinciplesReport) -> bool:
        if report.composite_score < target:
            return False
        if report.dry_score < dry_min:
            return False
        if report.kiss_score < kiss_min:
            return False
        if report.yagni_score < yagni_min:
            return False
        return True

    criterion.__doc__ = (
        f"composite>={target:.0%}, dry>={dry_min:.0%}, "
        f"kiss>={kiss_min:.0%}, yagni>={yagni_min:.0%}"
    )
    return criterion


def principles_scorer(report: PrinciplesReport) -> float:
    """Score function for converge() — composite score."""
    return report.composite_score


def principles_improve(report: PrinciplesReport) -> PrinciplesReport:
    """improve() callback: re-scan and log violations.

    In a live loop, a sprint-loop agent would act on the violations.
    This function serves as the signal source for converge().
    """
    import logging
    logger = logging.getLogger(__name__)
    if report.violations:
        logger.info("[principles] Top violations:")
        for v in report.violations[:5]:
            logger.info("  %s", v)
    return principles_scan(report.path)


def wave_aware_max_depth(default: int = 3) -> int:
    """Read current Fibonacci wave position and map to convergence depth.

    Maps wave position -> depth: W1->2, W2->3, W3->4, W5->6, W8->6.
    Falls back to `default` when no active wave session exists.
    """
    try:
        from lib.wave_state import load_wave_state
        ws = load_wave_state()
        if ws is not None:
            return min(ws.position + 1, 6)
    except Exception:
        pass
    return default


def converge_principles(
    path: str = ".",
    max_depth: Optional[int] = None,
    target: float = 0.80,
) -> "ConvergenceResult":
    """One-line entry: converge a codebase toward principled software.

    When max_depth is None, uses wave_aware_max_depth() to adapt
    convergence depth to the current Fibonacci wave position.

    Example:
        from lib.principles_criterion import converge_principles
        result = converge_principles("~/.claude/lib", max_depth=5)
        print(result.best_state.summary())
    """
    if max_depth is None:
        max_depth = wave_aware_max_depth(default=3)
    initial = principles_scan(path)
    criterion = principles_criterion(target=target)
    return converge_iter(
        state=initial,
        criterion=criterion,
        improve=principles_improve,
        max_depth=max_depth,
        _scorer=principles_scorer,
        label="principles",
    )


# ── AttestCriterion bridge ─────────────────────────────────────────────────

def principles_as_attest_signals(report: PrinciplesReport) -> list[Signal]:
    """Convert a PrinciplesReport into Signals for use in AttestCriterion.

    Enables CUBE integration — each signal maps to the CUBE domain.
    Signal.check closes over the score from the report snapshot.
    """
    def _check(threshold: float, score: float) -> bool:
        return score >= threshold

    return [
        Signal(name="DRY",   check=lambda _: report.dry_score   >= 0.70, weight=1.0),
        Signal(name="KISS",  check=lambda _: report.kiss_score  >= 0.60, weight=0.8),
        Signal(name="CC",    check=lambda _: report.cc_score    >= 0.70, weight=0.7),
        Signal(name="BIG-O", check=lambda _: report.bigo_score  >= 0.70, weight=0.7),
        Signal(name="SOLID", check=lambda _: report.solid_score >= 0.70, weight=1.0),
        Signal(name="GRASP", check=lambda _: report.grasp_score >= 0.70, weight=0.8),
        Signal(name="YAGNI", check=lambda _: report.yagni_score >= 0.70, weight=0.7),
        Signal(name="YSH",   check=lambda _: report.ysh_score   >= 0.70, weight=0.7),
    ]


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse, logging
    parser = argparse.ArgumentParser(
        description="GRIP Principles Scanner — measure DRY/KISS/SOLID/GRASP/YAGNI/YSH"
    )
    parser.add_argument("path", nargs="?", default=".",
                        help="Codebase path to scan (default: current dir)")
    parser.add_argument("--target", type=float, default=0.80,
                        help="Composite score target (default: 0.80)")
    parser.add_argument("--converge", action="store_true",
                        help="Run convergence loop (max 3 iterations)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")

    if args.converge:
        result = converge_principles(args.path, target=args.target)
        print(result.best_state.summary())
        print(f"\nConverged: {result.satisfied} | Iterations: {result.iterations}")
    else:
        report = principles_scan(args.path)
        print(report.summary())
        ok = principles_criterion(args.target)(report)
        if not ok:
            print(f"\nWARNING: Composite score {report.composite_score:.1%} below target {args.target:.1%}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
