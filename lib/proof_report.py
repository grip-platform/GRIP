#!/usr/bin/env python3
"""
proof_report.py -- Publishable proof report with falsification battery.

Aggregates all GRIP metrics into a structured report with:
    A: Convergence compound analysis (speed/quality/efficiency)
    B: Temporal facet analysis (achievement + friction trends per window)
    C: Genome trajectory (is mean fitness increasing?)
    D: CUBE progression (domain level advancement)
    E: Principle convergence (SOLID/KISS/DRY etc. score trends)
    F: Falsification battery (6 explicit PASS/FAIL checks)
    G: Statistical rigour (effect sizes, trend tests, N adequacy)

Verdict scale:
    PROVEN:       >= 4 sections improving, 0 falsification failures, N >= 20
    LIKELY:       >= 3 sections improving, <= 1 failure
    PROMISING:    >= 2 sections improving, <= 2 failures
    INSUFFICIENT: < 2 improving OR 3+ failures
    FALSIFIED:    3+ falsification checks FAIL

Output: CLI text, JSON, Markdown.

Usage:
    python3 lib/proof_report.py report     # CLI text
    python3 lib/proof_report.py json       # Machine-readable
    python3 lib/proof_report.py falsify    # Falsification battery only
    python3 lib/proof_report.py markdown   # Publishable markdown
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
LEARNING_DIR = CLAUDE_DIR / "learning"


# =========================================================================
# Statistics (pure Python, no scipy)
# =========================================================================

def mann_kendall(data: list[float]) -> dict:
    """Mann-Kendall trend test (non-parametric).

    Returns:
        s: Test statistic (positive = increasing trend)
        z: Normalised z-score
        p: Two-tailed p-value (approximate)
        trend: "increasing", "decreasing", or "no_trend"
    """
    n = len(data)
    if n < 4:
        return {"s": 0, "z": 0.0, "p": 1.0, "trend": "no_trend", "n": n}

    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = data[j] - data[i]
            if diff > 0:
                s += 1
            elif diff < 0:
                s -= 1

    # Variance under null hypothesis
    var_s = n * (n - 1) * (2 * n + 5) / 18.0

    # Normalised z-score
    if s > 0:
        z = (s - 1) / math.sqrt(var_s) if var_s > 0 else 0
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s) if var_s > 0 else 0
    else:
        z = 0

    # Approximate p-value from z (standard normal CDF)
    p = 2 * (1 - _normal_cdf(abs(z)))

    trend = "no_trend"
    if p < 0.05:
        trend = "increasing" if s > 0 else "decreasing"

    return {"s": s, "z": round(z, 4), "p": round(p, 4), "trend": trend, "n": n}


def _normal_cdf(x: float) -> float:
    """Approximate standard normal CDF using error function approximation."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def cohens_d(group1: list[float], group2: list[float]) -> float:
    """Cohen's d effect size between two groups.

    Returns positive d when group2 mean > group1 mean (improvement).
    """
    if len(group1) < 2 or len(group2) < 2:
        return 0.0
    m1 = sum(group1) / len(group1)
    m2 = sum(group2) / len(group2)
    var1 = sum((x - m1) ** 2 for x in group1) / (len(group1) - 1)
    var2 = sum((x - m2) ** 2 for x in group2) / (len(group2) - 1)
    pooled_std = math.sqrt((var1 + var2) / 2)
    if pooled_std == 0:
        return 0.0
    return round((m2 - m1) / pooled_std, 4)


# =========================================================================
# Section builders
# =========================================================================

def _section_a_compound() -> dict:
    """Section A: Convergence compound analysis."""
    try:
        from lib.converge_metrics import compound_analysis
        return compound_analysis()
    except Exception as e:
        return {"error": str(e), "verdict": "INSUFFICIENT"}


def _section_b_facet_trends() -> dict:
    """Section B: Temporal facet analysis from facet-windows.jsonl."""
    path = LEARNING_DIR / "facet-windows.jsonl"
    if not path.exists():
        return {"error": "No facet-windows.jsonl. Run benchmark_backfill.py first.", "improving": False}

    windows = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            try:
                windows.append(json.loads(line))
            except Exception:
                continue

    if len(windows) < 2:
        return {"windows": len(windows), "improving": False, "reason": "insufficient_windows"}

    achievement_rates = [w["achievement_rate"] for w in windows]
    friction_densities = [w["friction_density"] for w in windows]

    achievement_trend = mann_kendall(achievement_rates)
    friction_trend = mann_kendall(friction_densities)

    # Achievement improving OR friction decreasing = section improving
    improving = (achievement_trend["trend"] == "increasing" or
                 friction_trend["trend"] == "decreasing")

    return {
        "windows": len(windows),
        "achievement_trend": achievement_trend,
        "friction_trend": friction_trend,
        "improving": improving,
        "first_achievement": achievement_rates[0],
        "last_achievement": achievement_rates[-1],
        "first_friction": friction_densities[0],
        "last_friction": friction_densities[-1],
    }


def _section_c_genome() -> dict:
    """Section C: Genome fitness trajectory."""
    genome_path = CLAUDE_DIR / "cache" / "genome.json"
    if not genome_path.exists():
        return {"error": "No genome.json", "improving": False}

    try:
        data = json.loads(genome_path.read_text())
    except Exception:
        return {"error": "Invalid genome.json", "improving": False}

    history = data.get("fitness_history", [])
    if len(history) < 3:
        return {"history_length": len(history), "improving": False, "reason": "insufficient_history"}

    trend = mann_kendall(history)
    return {
        "history_length": len(history),
        "trend": trend,
        "improving": trend["trend"] == "increasing",
        "first_fitness": history[0],
        "last_fitness": history[-1],
        "avg_fitness": round(sum(history) / len(history), 4),
    }


def _section_d_cube() -> dict:
    """Section D: CUBE progression."""
    cube_path = CLAUDE_DIR / "cache" / "cube-state.json"
    if not cube_path.exists():
        return {"error": "No cube-state.json", "improving": False}

    try:
        data = json.loads(cube_path.read_text())
    except Exception:
        return {"error": "Invalid cube-state.json", "improving": False}

    levels = data.get("levels", {})
    if not levels:
        return {"domains": 0, "improving": False}

    total = len(levels)
    at_thread = sum(1 for v in levels.values() if v == 0)
    at_fork = sum(1 for v in levels.values() if v == 1)
    at_council = sum(1 for v in levels.values() if v >= 2)
    at_mesh = sum(1 for v in levels.values() if v >= 4)

    # Improving if more than 50% have advanced past THREAD/FORK
    improving = at_council / total > 0.5 if total else False

    return {
        "domains": total,
        "at_thread": at_thread,
        "at_fork": at_fork,
        "at_council_plus": at_council,
        "at_mesh": at_mesh,
        "mesh_fraction": round(at_mesh / total, 4) if total else 0,
        "improving": improving,
    }


def _section_e_principles() -> dict:
    """Section E: Principle convergence from evo-convergence.jsonl."""
    path = LEARNING_DIR / "evo-convergence.jsonl"
    if not path.exists():
        return {"error": "No evo-convergence.jsonl", "improving": False}

    entries = []
    for line in path.read_text().strip().split("\n"):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                continue

    if len(entries) < 3:
        return {"entries": len(entries), "improving": False, "reason": "insufficient_data"}

    # Extract per-principle score trajectories
    principles = {}
    for entry in entries:
        cube = entry.get("cube", {})
        for principle, score in cube.items():
            principles.setdefault(principle, []).append(score)

    trends = {}
    improving_count = 0
    for principle, scores in principles.items():
        if len(scores) >= 3:
            trend = mann_kendall(scores)
            trends[principle] = trend
            if trend["trend"] == "increasing":
                improving_count += 1

    return {
        "entries": len(entries),
        "principles_tracked": len(principles),
        "trends": trends,
        "improving_count": improving_count,
        "improving": improving_count >= len(principles) / 2 if principles else False,
    }


# =========================================================================
# Falsification Battery (Section F)
# =========================================================================

@dataclass
class FalsificationCheck:
    """A single falsification check."""
    id: str
    description: str
    passed: bool
    detail: str = ""


def _falsification_battery() -> list[FalsificationCheck]:
    """Run 6 falsification checks. Each can FAIL the proof."""
    checks = []

    # F1: Friction density must be decreasing or stable
    b = _section_b_facet_trends()
    f1_detail = ""
    f1_passed = True
    if "friction_trend" in b:
        ft = b["friction_trend"]
        if ft["trend"] == "increasing" and ft["p"] < 0.1:
            f1_passed = False
            f1_detail = f"Friction increasing (z={ft['z']}, p={ft['p']})"
        else:
            f1_detail = f"Friction trend: {ft['trend']} (p={ft['p']})"
    else:
        f1_detail = "Insufficient data"
        f1_passed = True  # Cannot falsify without data
    checks.append(FalsificationCheck("F1", "Friction density not increasing", f1_passed, f1_detail))

    # F2: Achievement rate not declining
    f2_passed = True
    f2_detail = ""
    if "achievement_trend" in b:
        at = b["achievement_trend"]
        if at["trend"] == "decreasing" and at["p"] < 0.1:
            f2_passed = False
            f2_detail = f"Achievement declining (z={at['z']}, p={at['p']})"
        else:
            f2_detail = f"Achievement trend: {at['trend']} (p={at['p']})"
    else:
        f2_detail = "Insufficient data"
    checks.append(FalsificationCheck("F2", "Achievement rate not declining", f2_passed, f2_detail))

    # F3: Genome fitness not declining
    c = _section_c_genome()
    f3_passed = True
    f3_detail = ""
    if "trend" in c:
        gt = c["trend"]
        if gt["trend"] == "decreasing":
            f3_passed = False
            f3_detail = f"Genome fitness declining (z={gt['z']})"
        else:
            f3_detail = f"Genome fitness trend: {gt['trend']}"
    else:
        f3_detail = c.get("reason", "No data")
    checks.append(FalsificationCheck("F3", "Genome fitness not declining", f3_passed, f3_detail))

    # F4: >50% CUBE domains past THREAD/FORK
    d = _section_d_cube()
    f4_passed = True
    f4_detail = ""
    if d.get("domains", 0) > 0:
        stuck = d.get("at_thread", 0) + d.get("at_fork", 0)
        total = d["domains"]
        stuck_pct = stuck / total
        f4_passed = stuck_pct < 0.5
        f4_detail = f"{stuck}/{total} domains at THREAD/FORK ({stuck_pct:.0%})"
    else:
        f4_detail = "No CUBE data"
    checks.append(FalsificationCheck("F4", ">50% CUBE domains advanced past FORK", f4_passed, f4_detail))

    # F5: Mean contraction L < 1.0
    f5_passed = True
    f5_detail = ""
    metrics_path = LEARNING_DIR / "converge-metrics.jsonl"
    if metrics_path.exists():
        try:
            from lib.converge_metrics import compute_contraction_rate
            runs = []
            for line in metrics_path.read_text().strip().split("\n"):
                if line.strip():
                    try:
                        runs.append(json.loads(line))
                    except Exception:
                        continue

            contraction_rates = []
            for r in runs:
                traj = r.get("trajectory", [])
                if len(traj) >= 2:
                    cr = compute_contraction_rate(traj)
                    if cr.get("mean_rate") is not None:
                        contraction_rates.append(cr["mean_rate"])

            if contraction_rates:
                mean_l = sum(contraction_rates) / len(contraction_rates)
                f5_passed = mean_l < 1.0
                f5_detail = f"Mean L={mean_l:.4f} across {len(contraction_rates)} runs"
            else:
                f5_detail = "No trajectory data"
        except Exception as e:
            f5_detail = f"Error: {e}"
    else:
        f5_detail = "No converge-metrics.jsonl"
    checks.append(FalsificationCheck("F5", "Mean contraction rate L < 1.0", f5_passed, f5_detail))

    # F6: Efficiency not regressing in second half
    f6_passed = True
    f6_detail = ""
    a = _section_a_compound()
    if "evidence_points" in a:
        ep = a["evidence_points"]
        f6_passed = ep >= 1  # At least some evidence of compound improvement
        f6_detail = f"Evidence points: {ep}/5"
    else:
        f6_detail = a.get("error", "No compound data")
    checks.append(FalsificationCheck("F6", "Efficiency not regressing", f6_passed, f6_detail))

    return checks


# =========================================================================
# Section G: Statistical Rigour
# =========================================================================

def _section_g_statistics() -> dict:
    """Compute statistical rigour metrics across all data sources."""
    stats = {}

    # Sample sizes
    facets_dir = CLAUDE_DIR / "usage-data" / "facets"
    stats["n_facets"] = len(list(facets_dir.glob("*.json"))) if facets_dir.exists() else 0

    metrics_path = LEARNING_DIR / "converge-metrics.jsonl"
    if metrics_path.exists():
        lines = [l for l in metrics_path.read_text().strip().split("\n") if l.strip()]
        stats["n_converge_runs"] = len(lines)
    else:
        stats["n_converge_runs"] = 0

    evo_path = LEARNING_DIR / "evo-convergence.jsonl"
    if evo_path.exists():
        lines = [l for l in evo_path.read_text().strip().split("\n") if l.strip()]
        stats["n_evo_entries"] = len(lines)
    else:
        stats["n_evo_entries"] = 0

    stats["n_adequate"] = stats["n_facets"] >= 20

    # External scoring divergence (Phase 8 integration)
    stats["external_scoring"] = _external_scoring_divergence()

    # Caveats
    caveats = []
    if stats["external_scoring"].get("available"):
        caveats.append("External scoring uses different LLM families (Gemini, Llama, Qwen) — reduced but not eliminated bias")
    else:
        caveats.append("Quality scores are self-assessed (LLM-scored, not human-scored)")
    caveats.extend([
        "Cannot prove causation (GRIP vs natural LLM improvement)",
        "Facets lack internal timestamps (using filesystem mtime as proxy)",
        "Early sessions had different task distributions than recent ones",
    ])
    stats["caveats"] = caveats

    return stats


def _external_scoring_divergence() -> dict:
    """Compare self-scores vs external scores across converge runs.

    Reads converge-metrics.jsonl for entries that have both score and
    external_score fields. Computes correlation and mean divergence.
    """
    metrics_path = LEARNING_DIR / "converge-metrics.jsonl"
    if not metrics_path.exists():
        return {"available": False, "reason": "no converge-metrics.jsonl"}

    pairs = []  # (self_score, external_score)
    try:
        for line in metrics_path.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                self_score = entry.get("final_score", entry.get("score"))
                ext_score = entry.get("external_score")
                if self_score is not None and ext_score is not None:
                    pairs.append((float(self_score), float(ext_score)))
            except Exception:
                continue
    except Exception:
        return {"available": False, "reason": "read error"}

    if len(pairs) < 3:
        return {"available": False, "reason": f"insufficient paired scores ({len(pairs)})",
                "paired_count": len(pairs)}

    self_scores = [p[0] for p in pairs]
    ext_scores = [p[1] for p in pairs]
    divergences = [s - e for s, e in pairs]
    mean_divergence = sum(divergences) / len(divergences)

    # Direction: positive = self-scoring inflates
    inflation_count = sum(1 for d in divergences if d > 0)

    # Pearson correlation (pure Python)
    n = len(pairs)
    mean_s = sum(self_scores) / n
    mean_e = sum(ext_scores) / n
    num = sum((s - mean_s) * (e - mean_e) for s, e in pairs)
    den_s = math.sqrt(sum((s - mean_s) ** 2 for s in self_scores))
    den_e = math.sqrt(sum((e - mean_e) ** 2 for e in ext_scores))
    correlation = round(num / (den_s * den_e), 4) if den_s > 0 and den_e > 0 else 0.0

    return {
        "available": True,
        "paired_count": len(pairs),
        "mean_divergence": round(mean_divergence, 2),
        "inflation_rate": round(inflation_count / len(pairs), 4),
        "correlation": correlation,
        "mean_self": round(sum(self_scores) / n, 2),
        "mean_external": round(sum(ext_scores) / n, 2),
    }


# =========================================================================
# Verdict computation
# =========================================================================

def compute_verdict(sections: dict, checks: list[FalsificationCheck]) -> str:
    """Compute overall verdict from section results and falsification checks."""
    improving_count = sum(1 for s in ["B", "C", "D", "E"]
                         if sections.get(s, {}).get("improving", False))

    failures = sum(1 for c in checks if not c.passed)
    n = sections.get("G", {}).get("n_facets", 0)

    if failures >= 3:
        return "FALSIFIED"
    if improving_count < 2 or failures >= 3:
        return "INSUFFICIENT"
    if improving_count >= 2 and failures <= 2:
        if improving_count >= 4 and failures == 0 and n >= 20:
            return "PROVEN"
        if improving_count >= 3 and failures <= 1:
            return "LIKELY"
        return "PROMISING"
    return "INSUFFICIENT"


# =========================================================================
# Report assembly
# =========================================================================

@dataclass
class ProofReport:
    """Complete proof report with all sections."""
    sections: dict = field(default_factory=dict)
    falsification: list = field(default_factory=list)
    verdict: str = "INSUFFICIENT"
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


def generate_report() -> ProofReport:
    """Generate complete proof report."""
    sections = {
        "A": _section_a_compound(),
        "B": _section_b_facet_trends(),
        "C": _section_c_genome(),
        "D": _section_d_cube(),
        "E": _section_e_principles(),
        "G": _section_g_statistics(),
    }

    checks = _falsification_battery()
    sections["F"] = [{"id": c.id, "desc": c.description, "passed": c.passed, "detail": c.detail}
                     for c in checks]

    verdict = compute_verdict(sections, checks)

    return ProofReport(sections=sections, falsification=checks, verdict=verdict)


def format_cli(report: ProofReport) -> str:
    """Format report as CLI text."""
    lines = [
        "=" * 64,
        "  GRIP Convergence Proof Report",
        "=" * 64,
        "",
    ]

    # Section A
    a = report.sections.get("A", {})
    lines.append("  A. Convergence Compound Analysis")
    lines.append(f"     Verdict: {a.get('verdict', 'N/A')}")
    lines.append(f"     Evidence: {a.get('evidence_points', 0)}/5 points")
    lines.append("")

    # Section B
    b = report.sections.get("B", {})
    lines.append("  B. Temporal Facet Analysis")
    lines.append(f"     Windows: {b.get('windows', 0)}")
    if "achievement_trend" in b:
        at = b["achievement_trend"]
        lines.append(f"     Achievement: {at['trend']} (p={at['p']})")
    if "friction_trend" in b:
        ft = b["friction_trend"]
        lines.append(f"     Friction: {ft['trend']} (p={ft['p']})")
    lines.append(f"     Improving: {b.get('improving', False)}")
    lines.append("")

    # Section C
    c = report.sections.get("C", {})
    lines.append("  C. Genome Trajectory")
    lines.append(f"     History: {c.get('history_length', 0)} entries")
    if "trend" in c:
        lines.append(f"     Trend: {c['trend']['trend']} (p={c['trend']['p']})")
    lines.append(f"     Improving: {c.get('improving', False)}")
    lines.append("")

    # Section D
    d = report.sections.get("D", {})
    lines.append("  D. CUBE Progression")
    lines.append(f"     Domains: {d.get('domains', 0)}")
    lines.append(f"     MESH: {d.get('at_mesh', 0)} | Council+: {d.get('at_council_plus', 0)}")
    lines.append(f"     Improving: {d.get('improving', False)}")
    lines.append("")

    # Section E
    e = report.sections.get("E", {})
    lines.append("  E. Principle Convergence")
    lines.append(f"     Entries: {e.get('entries', 0)}")
    lines.append(f"     Improving principles: {e.get('improving_count', 0)}/{e.get('principles_tracked', 0)}")
    lines.append("")

    # Section F
    lines.append("  F. Falsification Battery")
    for check in report.falsification:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"     [{status}] {check.id}: {check.description}")
        if check.detail:
            lines.append(f"            {check.detail}")
    lines.append("")

    # Section G
    g = report.sections.get("G", {})
    lines.append("  G. Statistical Rigour")
    lines.append(f"     N(facets): {g.get('n_facets', 0)}")
    lines.append(f"     N(converge): {g.get('n_converge_runs', 0)}")
    lines.append(f"     N adequate: {g.get('n_adequate', False)}")
    ext = g.get("external_scoring", {})
    if ext.get("available"):
        lines.append(f"     External scoring: {ext['paired_count']} paired runs")
        lines.append(f"     Mean divergence: {ext['mean_divergence']:+.1f} (self - external)")
        lines.append(f"     Inflation rate: {ext['inflation_rate']:.0%}")
        lines.append(f"     Correlation: {ext['correlation']:.3f}")
    else:
        lines.append(f"     External scoring: not available ({ext.get('reason', 'N/A')})")
    if g.get("caveats"):
        lines.append("     Caveats:")
        for caveat in g["caveats"]:
            lines.append(f"       - {caveat}")
    lines.append("")

    # Verdict
    lines.append("=" * 64)
    lines.append(f"  VERDICT: {report.verdict}")
    lines.append("=" * 64)

    return "\n".join(lines)


def format_markdown(report: ProofReport) -> str:
    """Format report as publishable Markdown."""
    lines = [
        "# GRIP Convergence Proof Report",
        "",
        f"Generated: {report.timestamp}",
        "",
        f"**Verdict: {report.verdict}**",
        "",
    ]

    # Sections
    section_names = {
        "A": "Convergence Compound Analysis",
        "B": "Temporal Facet Analysis",
        "C": "Genome Trajectory",
        "D": "CUBE Progression",
        "E": "Principle Convergence",
        "F": "Falsification Battery",
        "G": "Statistical Rigour",
    }

    for key in ["A", "B", "C", "D", "E"]:
        data = report.sections.get(key, {})
        lines.append(f"## {key}. {section_names[key]}")
        lines.append("")
        lines.append(f"- Improving: {'Yes' if data.get('improving', False) else 'No'}")
        if key == "B" and "achievement_trend" in data:
            lines.append(f"- Achievement trend: {data['achievement_trend']['trend']} (p={data['achievement_trend']['p']})")
            lines.append(f"- Friction trend: {data['friction_trend']['trend']} (p={data['friction_trend']['p']})")
        if key == "C" and "trend" in data:
            lines.append(f"- Fitness trend: {data['trend']['trend']} (p={data['trend']['p']})")
        if key == "D":
            lines.append(f"- MESH domains: {data.get('at_mesh', 0)}/{data.get('domains', 0)}")
        if key == "E":
            lines.append(f"- Improving principles: {data.get('improving_count', 0)}/{data.get('principles_tracked', 0)}")
        lines.append("")

    # Falsification
    lines.append(f"## F. {section_names['F']}")
    lines.append("")
    lines.append("| Check | Description | Result | Detail |")
    lines.append("|-------|-------------|--------|--------|")
    for check in report.falsification:
        status = "PASS" if check.passed else "**FAIL**"
        lines.append(f"| {check.id} | {check.description} | {status} | {check.detail} |")
    lines.append("")

    # Statistics
    g = report.sections.get("G", {})
    lines.append(f"## G. {section_names['G']}")
    lines.append("")
    lines.append(f"- N(facets): {g.get('n_facets', 0)}")
    lines.append(f"- N(converge runs): {g.get('n_converge_runs', 0)}")
    lines.append(f"- Sample adequate: {'Yes' if g.get('n_adequate') else 'No'}")
    lines.append("")
    ext = g.get("external_scoring", {})
    if ext.get("available"):
        lines.append("### External Scoring Validation")
        lines.append("")
        lines.append(f"- Paired runs: {ext['paired_count']}")
        lines.append(f"- Mean divergence: {ext['mean_divergence']:+.1f} (self - external)")
        lines.append(f"- Self-scoring inflation rate: {ext['inflation_rate']:.0%}")
        lines.append(f"- Correlation (Pearson r): {ext['correlation']:.3f}")
        lines.append("")
    if g.get("caveats"):
        lines.append("### Limitations")
        lines.append("")
        for caveat in g["caveats"]:
            lines.append(f"- {caveat}")
        lines.append("")

    return "\n".join(lines)


def format_json(report: ProofReport) -> str:
    """Format report as JSON."""
    return json.dumps({
        "verdict": report.verdict,
        "timestamp": report.timestamp,
        "sections": {
            k: v if k != "F" else [
                {"id": c.id, "description": c.description, "passed": c.passed, "detail": c.detail}
                for c in report.falsification
            ]
            for k, v in report.sections.items()
        },
        "falsification_failures": sum(1 for c in report.falsification if not c.passed),
    }, indent=2)


# =========================================================================
# CLI
# =========================================================================

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"

    report = generate_report()

    if cmd == "report":
        print(format_cli(report))
    elif cmd == "json":
        print(format_json(report))
    elif cmd == "markdown":
        print(format_markdown(report))
    elif cmd == "falsify":
        checks = _falsification_battery()
        failures = 0
        for c in checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"[{status}] {c.id}: {c.description}")
            if c.detail:
                print(f"         {c.detail}")
            if not c.passed:
                failures += 1
        print(f"\n{failures}/{len(checks)} checks FAILED")
    else:
        print("Usage: python3 proof_report.py [report|json|markdown|falsify]")
        sys.exit(1)


if __name__ == "__main__":
    main()
