#!/usr/bin/env python3
"""
grip_health_cli.py -- Unified GRIP health dashboard.

Combines subsystem health checks (genome, convergence, memory, tests,
achievements) into a single weighted report with ASCII visualization.

Usage:
    python3 lib/grip_health_cli.py              # Full report
    python3 lib/grip_health_cli.py --section genome  # Single subsystem
    python3 lib/grip_health_cli.py score         # Overall score number
    python3 lib/grip_health_cli.py --json        # Machine-readable output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SubsystemHealth:
    """Health status for a single GRIP subsystem."""
    name: str
    score: float  # 0-100
    status: str   # healthy | degraded | critical
    details: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


@dataclass
class HealthReport:
    """Aggregated health report across all subsystems."""
    subsystems: list[SubsystemHealth] = field(default_factory=list)
    overall_score: float = 0.0
    timestamp: str = ""
    recommendation: str = ""


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _status_from_score(score: float) -> str:
    if score >= 70:
        return "healthy"
    if score >= 40:
        return "degraded"
    return "critical"


# ---------------------------------------------------------------------------
# Subsystem checks
# ---------------------------------------------------------------------------

WEIGHTS = {
    "genome": 0.25,
    "convergence": 0.30,
    "memory": 0.15,
    "tests": 0.20,
    "achievements": 0.10,
}


def check_genome(claude_dir: str) -> SubsystemHealth:
    """Check genome health: existence, gene count, fitness levels."""
    try:
        sys_path_added = False
        root = str(Path(claude_dir).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
            sys_path_added = True

        from lib.converge_dna import load_genome

        genome_path = Path(claude_dir) / "cache" / "genome.json"
        genome = load_genome(genome_path)

        if sys_path_added and root in sys.path:
            sys.path.remove(root)

        if genome is None:
            return SubsystemHealth(
                name="genome",
                score=0.0,
                status="critical",
                details={"exists": False},
                issues=["No genome file found"],
            )

        gene_count = len(genome.genes)
        living = genome.living_genes()
        avg_fitness = genome.avg_fitness
        dying_genes = [g for g in genome.genes.values() if g.fitness < 0.2]

        score = 100.0
        issues: list[str] = []

        if gene_count == 0:
            score = 0.0
            issues.append("Genome has no genes")
        else:
            for g in dying_genes:
                score = max(0.0, score - 10.0)
                issues.append(f"Gene '{g.name}' fitness {g.fitness:.2f} < 0.2")

            if avg_fitness < 0.3:
                score = max(0.0, score - 20.0)
                issues.append(f"Average fitness {avg_fitness:.2f} critically low")

        return SubsystemHealth(
            name="genome",
            score=round(min(100.0, max(0.0, score)), 1),
            status=_status_from_score(score),
            details={
                "exists": True,
                "gene_count": gene_count,
                "living_count": len(living),
                "avg_fitness": round(avg_fitness, 3),
                "dying_count": len(dying_genes),
                "generation": genome.generation,
            },
            issues=issues,
        )
    except Exception as exc:
        return SubsystemHealth(
            name="genome",
            score=0.0,
            status="critical",
            details={"error": str(exc)},
            issues=[f"Genome check failed: {exc}"],
        )


def check_convergence(claude_dir: str) -> SubsystemHealth:
    """Check convergence health: contraction fitness, anomalies."""
    try:
        sys_path_added = False
        root = str(Path(claude_dir).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
            sys_path_added = True

        from lib.contraction_fitness import convergence_quality, _load_runs
        from lib.converge_anomaly import load_metrics, detect_anomalies

        if sys_path_added and root in sys.path:
            sys.path.remove(root)

        metrics_path = Path(claude_dir) / "learning" / "converge-metrics.jsonl"

        # Contraction analysis
        runs = _load_runs(metrics_path)
        run_count = len(runs)
        contractive_count = 0
        for run_id, run_data in runs.items():
            trajectory = [d.get("score", 0) for d in run_data.get("depths", []) if "score" in d]
            if len(trajectory) >= 3:
                quality = convergence_quality(trajectory)
                if quality.is_contractive:
                    contractive_count += 1

        # Anomaly analysis
        anomaly_runs = load_metrics(metrics_path)
        report = detect_anomalies(anomaly_runs)
        high_anomalies = [a for a in report.anomalies if a.severity == "high"]

        score = 100.0
        issues: list[str] = []

        if run_count == 0:
            score = 50.0
            issues.append("No convergence runs found")
        else:
            non_contractive = run_count - contractive_count
            score -= non_contractive * 5
            if non_contractive > 0:
                issues.append(f"{non_contractive}/{run_count} runs are not contractive")

        score -= len(high_anomalies) * 10
        if high_anomalies:
            issues.append(f"{len(high_anomalies)} high-severity anomalies detected")

        return SubsystemHealth(
            name="convergence",
            score=round(min(100.0, max(0.0, score)), 1),
            status=_status_from_score(score),
            details={
                "run_count": run_count,
                "contractive_count": contractive_count,
                "anomaly_count": len(report.anomalies),
                "high_anomaly_count": len(high_anomalies),
            },
            issues=issues,
        )
    except Exception as exc:
        return SubsystemHealth(
            name="convergence",
            score=0.0,
            status="critical",
            details={"error": str(exc)},
            issues=[f"Convergence check failed: {exc}"],
        )


def check_memory(claude_dir: str) -> SubsystemHealth:
    """Check memory health: MEMORY.md size, cache directory."""
    memory_path = Path(claude_dir) / "projects"
    cache_path = Path(claude_dir) / "cache"

    score = 100.0
    issues: list[str] = []
    details: dict = {}

    # Find MEMORY.md files under projects/
    memory_files: list[Path] = []
    if memory_path.is_dir():
        for root, _dirs, files in os.walk(str(memory_path)):
            for f in files:
                if f == "MEMORY.md":
                    memory_files.append(Path(root) / f)

    # Also check top-level MEMORY.md
    top_memory = Path(claude_dir) / "MEMORY.md"
    if top_memory.exists():
        memory_files.append(top_memory)

    if not memory_files:
        score -= 20.0
        issues.append("No MEMORY.md found")
        details["memory_files"] = 0
    else:
        details["memory_files"] = len(memory_files)
        max_lines = 0
        bloated_files: list[str] = []
        for mf in memory_files:
            try:
                line_count = len(mf.read_text().splitlines())
                max_lines = max(max_lines, line_count)
                if line_count > 200:
                    bloated_files.append(f"{mf.name} ({line_count} lines)")
            except OSError:
                pass
        details["max_lines"] = max_lines
        if bloated_files:
            penalty = len(bloated_files) * 15.0
            score -= penalty
            issues.append(f"Memory bloat: {', '.join(bloated_files)}")

    if not cache_path.is_dir():
        score -= 20.0
        issues.append("cache/ directory missing")
        details["cache_exists"] = False
    else:
        details["cache_exists"] = True

    return SubsystemHealth(
        name="memory",
        score=round(min(100.0, max(0.0, score)), 1),
        status=_status_from_score(score),
        details=details,
        issues=issues,
    )


def check_tests(claude_dir: str) -> SubsystemHealth:
    """Check test health: test file count vs lib file count."""
    tests_dir = Path(claude_dir) / "tests"
    lib_dir = Path(claude_dir) / "lib"

    test_count = 0
    lib_count = 0

    if tests_dir.is_dir():
        test_count = len([
            f for f in os.listdir(str(tests_dir))
            if f.startswith("test_") and f.endswith(".py")
        ])

    if lib_dir.is_dir():
        lib_count = len([
            f for f in os.listdir(str(lib_dir))
            if f.endswith(".py") and not f.startswith("_")
        ])

    if lib_count == 0:
        score = 100.0 if test_count > 0 else 0.0
    else:
        score = min(100.0, (test_count / lib_count) * 100.0)

    issues: list[str] = []
    if test_count == 0:
        issues.append("No test files found")
    if lib_count > 0 and test_count < lib_count:
        gap = lib_count - test_count
        issues.append(f"{gap} lib files lack corresponding tests")

    return SubsystemHealth(
        name="tests",
        score=round(score, 1),
        status=_status_from_score(score),
        details={
            "test_count": test_count,
            "lib_count": lib_count,
            "coverage_ratio": round(test_count / lib_count, 2) if lib_count > 0 else 0.0,
        },
        issues=issues,
    )


def check_achievements(claude_dir: str) -> SubsystemHealth:
    """Check achievement health: unlocked badges vs total."""
    try:
        sys_path_added = False
        root = str(Path(claude_dir).resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
            sys_path_added = True

        from lib.achievements import load_store, all_badges

        if sys_path_added and root in sys.path:
            sys.path.remove(root)

        cache_dir = os.path.join(claude_dir, "cache")
        store = load_store(cache_dir)
        badges = all_badges(store)

        total = len(badges)
        unlocked = sum(1 for b in badges if b.is_unlocked)

        score = (unlocked / total * 100.0) if total > 0 else 0.0
        issues: list[str] = []
        if unlocked == 0:
            issues.append("No badges unlocked")
        elif unlocked < total / 2:
            issues.append(f"Only {unlocked}/{total} badges unlocked")

        return SubsystemHealth(
            name="achievements",
            score=round(min(100.0, score), 1),
            status=_status_from_score(score),
            details={
                "unlocked": unlocked,
                "total": total,
            },
            issues=issues,
        )
    except Exception as exc:
        return SubsystemHealth(
            name="achievements",
            score=0.0,
            status="critical",
            details={"error": str(exc)},
            issues=[f"Achievements check failed: {exc}"],
        )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

CHECKERS = {
    "genome": check_genome,
    "convergence": check_convergence,
    "memory": check_memory,
    "tests": check_tests,
    "achievements": check_achievements,
}


def generate_report(claude_dir: str) -> HealthReport:
    """Run all subsystem checks and produce a weighted HealthReport."""
    subsystems: list[SubsystemHealth] = []
    for name in WEIGHTS:
        checker = CHECKERS[name]
        subsystems.append(checker(claude_dir))

    overall = sum(
        sub.score * WEIGHTS[sub.name]
        for sub in subsystems
    )

    # Recommendation: focus on lowest-scoring subsystem
    lowest = min(subsystems, key=lambda s: s.score)
    if lowest.score >= 90:
        recommendation = "All subsystems healthy. Maintain current practices."
    else:
        recommendation = f"Focus on {lowest.name} to improve overall health (score: {lowest.score:.0f}/100)."

    return HealthReport(
        subsystems=subsystems,
        overall_score=round(overall, 1),
        timestamp=datetime.now(timezone.utc).isoformat(),
        recommendation=recommendation,
    )


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------

def _bar(score: float, width: int = 20) -> str:
    """Render a score as a filled/empty bar: [####----] 78%."""
    filled = int(round(score / 100.0 * width))
    empty = width - filled
    return "#" * filled + "-" * empty


def format_report(report: HealthReport) -> str:
    """Format a HealthReport as a human-readable ASCII table."""
    lines: list[str] = []
    lines.append("GRIP Health Dashboard")
    lines.append("=" * 60)
    lines.append(f"Timestamp: {report.timestamp}")
    lines.append(f"Overall:   [{_bar(report.overall_score)}] {report.overall_score:.0f}%")
    lines.append("")

    # Column headers
    header = f"{'Subsystem':<15} {'Score':>5}  {'Bar':<22} {'Status':<10} {'Weight':>6}"
    lines.append(header)
    lines.append("-" * 60)

    for sub in report.subsystems:
        weight_pct = int(WEIGHTS.get(sub.name, 0) * 100)
        bar = f"[{_bar(sub.score)}]"
        lines.append(
            f"{sub.name:<15} {sub.score:>5.0f}  {bar:<22} {sub.status:<10} {weight_pct:>5}%"
        )

    lines.append("-" * 60)

    # Issues section
    all_issues = []
    for sub in report.subsystems:
        for issue in sub.issues:
            all_issues.append(f"  [{sub.name}] {issue}")

    if all_issues:
        lines.append("")
        lines.append("Issues:")
        lines.extend(all_issues)

    lines.append("")
    lines.append(f"Recommendation: {report.recommendation}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GRIP unified health dashboard",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        help="'score' to print just the overall score number",
    )
    parser.add_argument(
        "--section",
        choices=list(CHECKERS.keys()),
        default=None,
        help="Show only a specific subsystem",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output",
    )
    parser.add_argument(
        "--claude-dir",
        default=None,
        help="Path to .claude directory (default: ~/.claude)",
    )

    args = parser.parse_args(argv)
    claude_dir = args.claude_dir or os.path.expanduser("~/.claude")

    # score subcommand: just print the number
    if args.command == "score":
        report = generate_report(claude_dir)
        if args.json:
            print(json.dumps({"overall_score": report.overall_score}))
        else:
            print(f"{report.overall_score:.0f}")
        return 0

    # Single section
    if args.section:
        checker = CHECKERS[args.section]
        health = checker(claude_dir)
        if args.json:
            print(json.dumps(asdict(health), indent=2))
        else:
            bar = f"[{_bar(health.score)}]"
            print(f"{health.name}: {bar} {health.score:.0f}% ({health.status})")
            for issue in health.issues:
                print(f"  - {issue}")
            if health.details:
                for k, v in health.details.items():
                    print(f"  {k}: {v}")
        return 0

    # Full report
    report = generate_report(claude_dir)
    if args.json:
        output = {
            "overall_score": report.overall_score,
            "timestamp": report.timestamp,
            "recommendation": report.recommendation,
            "subsystems": [asdict(s) for s in report.subsystems],
        }
        print(json.dumps(output, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
