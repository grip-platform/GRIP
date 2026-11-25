"""Sprint forecast engine.

Predicts sprint outcomes from historical velocity data, commit patterns,
and team capacity. Provides confidence intervals and risk factors.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from subprocess_helpers import run_git as _run_git, count_output_lines as _count_output_lines


# === Data Models ===

@dataclass
class SprintRecord:
    """Historical sprint data point."""
    start: str = ""
    end: str = ""
    planned_points: int = 0
    completed_points: int = 0
    commits: int = 0
    prs_merged: int = 0
    bugs_found: int = 0

    @property
    def completion_rate(self) -> float:
        if self.planned_points == 0:
            return 0.0
        return self.completed_points / self.planned_points

    @property
    def velocity(self) -> int:
        return self.completed_points


@dataclass
class ForecastResult:
    """Sprint forecast prediction."""
    predicted_velocity: float = 0.0
    confidence_low: float = 0.0
    confidence_high: float = 0.0
    confidence_level: float = 0.0  # 0-1
    risk_factors: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    trend: str = "stable"  # improving, stable, declining
    health_score: float = 0.0  # 0-100
    historical_velocities: list[float] = field(default_factory=list)


@dataclass
class TeamCapacity:
    """Team availability for a sprint."""
    total_days: int = 10
    members: int = 1
    holidays: int = 0
    planned_absences: int = 0

    @property
    def available_days(self) -> int:
        return max(0, (self.total_days * self.members) - self.holidays - self.planned_absences)

    @property
    def capacity_ratio(self) -> float:
        full = self.total_days * self.members
        if full == 0:
            return 0.0
        return self.available_days / full


# === Git Data Extraction ===

def _count_git_lines(cmd: str) -> str:
    """Run a git command and return line count as string (replaces shell pipe to wc -l)."""
    return str(_count_output_lines(cmd))


def extract_sprint_from_git(days: int = 14) -> SprintRecord:
    """Extract sprint-like metrics from recent git history."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Count commits
    commit_count = _count_git_lines(f'git log --since={since} --oneline')
    commits = int(commit_count.strip()) if commit_count.strip().isdigit() else 0

    # Count merged PRs (merge commits)
    merge_count = _count_git_lines(f'git log --since={since} --merges --oneline')
    prs = int(merge_count.strip()) if merge_count.strip().isdigit() else 0

    # Count bug fixes
    fix_count = _count_git_lines(f'git log --since={since} --oneline --grep=fix:')
    bugs = int(fix_count.strip()) if fix_count.strip().isdigit() else 0

    # Estimate points from commits (heuristic: 1 point per 3 commits)
    estimated_points = max(1, commits // 3)

    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    return SprintRecord(
        start=start_date,
        end=end_date,
        planned_points=estimated_points + 2,  # Assume slight overcommit
        completed_points=estimated_points,
        commits=commits,
        prs_merged=prs,
        bugs_found=bugs,
    )


def extract_velocity_history(sprints: int = 6, sprint_length: int = 14) -> list[SprintRecord]:
    """Extract multiple sprint periods from git history."""
    records = []
    for i in range(sprints):
        end_offset = i * sprint_length
        start_offset = (i + 1) * sprint_length

        since = (datetime.now(timezone.utc) - timedelta(days=start_offset)).isoformat()
        until = (datetime.now(timezone.utc) - timedelta(days=end_offset)).isoformat()

        commit_count = _count_git_lines(
            f'git log --since={since} --until={until} --oneline'
        )
        commits = int(commit_count.strip()) if commit_count.strip().isdigit() else 0

        merge_count = _count_git_lines(
            f'git log --since={since} --until={until} --merges --oneline'
        )
        prs = int(merge_count.strip()) if merge_count.strip().isdigit() else 0

        fix_count = _count_git_lines(
            f'git log --since={since} --until={until} --oneline --grep=fix:'
        )
        bugs = int(fix_count.strip()) if fix_count.strip().isdigit() else 0

        estimated_points = max(1, commits // 3)

        start_date = (datetime.now(timezone.utc) - timedelta(days=start_offset)).strftime("%Y-%m-%d")
        end_date = (datetime.now(timezone.utc) - timedelta(days=end_offset)).strftime("%Y-%m-%d")

        records.append(SprintRecord(
            start=start_date,
            end=end_date,
            planned_points=estimated_points + 2,
            completed_points=estimated_points,
            commits=commits,
            prs_merged=prs,
            bugs_found=bugs,
        ))

    return list(reversed(records))  # Oldest first


# === Forecasting Engine ===

def calculate_velocity_trend(velocities: list[float]) -> str:
    """Determine velocity trend from history."""
    if len(velocities) < 2:
        return "stable"

    # Compare last third to first third
    third = max(1, len(velocities) // 3)
    early = statistics.mean(velocities[:third])
    late = statistics.mean(velocities[-third:])

    if early == 0:
        return "improving" if late > 0 else "stable"

    change = (late - early) / early

    if change > 0.15:
        return "improving"
    elif change < -0.15:
        return "declining"
    return "stable"


def forecast_velocity(
    history: list[SprintRecord],
    capacity: Optional[TeamCapacity] = None,
) -> ForecastResult:
    """Forecast next sprint velocity from historical data."""
    if not history:
        return ForecastResult(
            predicted_velocity=0,
            confidence_level=0,
            risk_factors=["No historical data available"],
            recommendations=["Complete at least 3 sprints to enable forecasting"],
            health_score=0,
        )

    velocities = [s.velocity for s in history]
    completion_rates = [s.completion_rate for s in history]

    # Weighted average (recent sprints matter more)
    weights = [1 + i * 0.5 for i in range(len(velocities))]
    weighted_sum = sum(v * w for v, w in zip(velocities, weights))
    weight_total = sum(weights)
    predicted = weighted_sum / weight_total if weight_total > 0 else 0

    # Apply capacity adjustment
    if capacity and capacity.capacity_ratio < 1.0:
        predicted *= capacity.capacity_ratio

    # Confidence interval using standard deviation
    if len(velocities) >= 2:
        std_dev = statistics.stdev(velocities)
        # 80% confidence interval
        margin = 1.28 * std_dev / math.sqrt(len(velocities))
        confidence_low = max(0, predicted - margin)
        confidence_high = predicted + margin
    else:
        confidence_low = predicted * 0.7
        confidence_high = predicted * 1.3

    # Confidence level based on data quality
    data_points = len(velocities)
    if data_points >= 6:
        confidence_level = 0.85
    elif data_points >= 4:
        confidence_level = 0.7
    elif data_points >= 2:
        confidence_level = 0.5
    else:
        confidence_level = 0.3

    # Reduce confidence if high variance
    if len(velocities) >= 2:
        cv = statistics.stdev(velocities) / (statistics.mean(velocities) or 1)
        if cv > 0.5:
            confidence_level *= 0.7

    confidence_level = min(1.0, max(0.0, confidence_level))

    # Detect trend
    trend = calculate_velocity_trend([float(v) for v in velocities])

    # Risk factors
    risk_factors = _detect_risk_factors(history, capacity)

    # Recommendations
    recommendations = _generate_recommendations(
        trend, velocities, completion_rates, risk_factors
    )

    # Health score (0-100)
    health_score = _calculate_health_score(
        velocities, completion_rates, trend, risk_factors
    )

    return ForecastResult(
        predicted_velocity=round(predicted, 1),
        confidence_low=round(confidence_low, 1),
        confidence_high=round(confidence_high, 1),
        confidence_level=round(confidence_level, 2),
        risk_factors=risk_factors,
        recommendations=recommendations,
        trend=trend,
        health_score=round(health_score, 1),
        historical_velocities=[float(v) for v in velocities],
    )


def _detect_risk_factors(
    history: list[SprintRecord],
    capacity: Optional[TeamCapacity],
) -> list[str]:
    """Identify risk factors for the upcoming sprint."""
    risks = []

    if len(history) < 3:
        risks.append("Limited history (< 3 sprints) reduces forecast accuracy")

    velocities = [s.velocity for s in history]
    if len(velocities) >= 2:
        cv = statistics.stdev(velocities) / (statistics.mean(velocities) or 1)
        if cv > 0.4:
            risks.append(f"High velocity variance (CV={cv:.1%}) suggests unstable delivery")

    # Check for declining trend
    if len(velocities) >= 3:
        recent = velocities[-2:]
        avg_recent = statistics.mean(recent)
        avg_overall = statistics.mean(velocities)
        if avg_recent < avg_overall * 0.8:
            risks.append("Recent velocity below historical average")

    # Bug ratio
    total_bugs = sum(s.bugs_found for s in history)
    total_commits = sum(s.commits for s in history)
    if total_commits > 0 and total_bugs / total_commits > 0.3:
        risks.append(f"High bug ratio ({total_bugs}/{total_commits} commits are fixes)")

    # Capacity
    if capacity and capacity.capacity_ratio < 0.8:
        risks.append(f"Reduced capacity ({capacity.capacity_ratio:.0%} of full team)")

    # Completion rate dropping
    rates = [s.completion_rate for s in history]
    if len(rates) >= 2 and rates[-1] < 0.7:
        risks.append(f"Last sprint completion rate was only {rates[-1]:.0%}")

    return risks


def _generate_recommendations(
    trend: str,
    velocities: list[int],
    completion_rates: list[float],
    risk_factors: list[str],
) -> list[str]:
    """Generate actionable recommendations."""
    recs = []

    if trend == "declining":
        recs.append("Investigate causes of declining velocity — scope creep? tech debt?")
        recs.append("Consider reducing planned scope by 15-20% next sprint")

    if trend == "improving":
        recs.append("Velocity is improving — incrementally increase planned scope")

    avg_rate = statistics.mean(completion_rates) if completion_rates else 0
    if avg_rate < 0.8:
        recs.append(f"Average completion rate is {avg_rate:.0%} — plan fewer items or break into smaller chunks")

    if avg_rate > 0.95 and len(velocities) >= 3:
        recs.append("Consistently completing all planned work — consider stretching goals")

    if any("variance" in r.lower() for r in risk_factors):
        recs.append("Stabilize delivery by reducing WIP limits and focusing on finishing")

    if any("bug ratio" in r.lower() for r in risk_factors):
        recs.append("Allocate 20% of sprint capacity to bug fixing and tech debt")

    if not recs:
        recs.append("Sprint velocity is healthy — maintain current pace")

    return recs


def _calculate_health_score(
    velocities: list[int],
    completion_rates: list[float],
    trend: str,
    risk_factors: list[str],
) -> float:
    """Calculate overall sprint health score (0-100)."""
    score = 50.0  # Start at neutral

    # Trend bonus/penalty
    if trend == "improving":
        score += 15
    elif trend == "declining":
        score -= 15

    # Completion rate
    avg_rate = statistics.mean(completion_rates) if completion_rates else 0
    if avg_rate >= 0.9:
        score += 20
    elif avg_rate >= 0.7:
        score += 10
    elif avg_rate < 0.5:
        score -= 20

    # Consistency (low variance = good)
    if len(velocities) >= 2 and statistics.mean(velocities) > 0:
        cv = statistics.stdev(velocities) / statistics.mean(velocities)
        if cv < 0.2:
            score += 15
        elif cv < 0.4:
            score += 5
        elif cv > 0.6:
            score -= 10

    # Risk penalty
    score -= len(risk_factors) * 3

    return max(0, min(100, score))


# === Formatters ===

TREND_INDICATORS = {
    "improving": "^",
    "stable": "=",
    "declining": "v",
}

HEALTH_LABELS = {
    (80, 101): "Excellent",
    (60, 80): "Good",
    (40, 60): "Fair",
    (20, 40): "Needs Attention",
    (0, 20): "Critical",
}


def _health_label(score: float) -> str:
    for (low, high), label in HEALTH_LABELS.items():
        if low <= score < high:
            return label
    return "Unknown"


def format_forecast_text(forecast: ForecastResult) -> str:
    """Format forecast as terminal text."""
    lines = []
    lines.append("Sprint Forecast")
    lines.append("=" * 50)

    trend_icon = TREND_INDICATORS.get(forecast.trend, "=")
    lines.append(f"Predicted Velocity: {forecast.predicted_velocity} pts [{trend_icon} {forecast.trend}]")
    lines.append(
        f"Confidence Range: {forecast.confidence_low} - {forecast.confidence_high} pts "
        f"({forecast.confidence_level:.0%} confidence)"
    )
    lines.append(f"Health Score: {forecast.health_score}/100 ({_health_label(forecast.health_score)})")

    if forecast.historical_velocities:
        spark = _sparkline(forecast.historical_velocities)
        lines.append(f"Velocity Trend: {spark}")

    if forecast.risk_factors:
        lines.append("")
        lines.append("Risk Factors:")
        for risk in forecast.risk_factors:
            lines.append(f"  ! {risk}")

    if forecast.recommendations:
        lines.append("")
        lines.append("Recommendations:")
        for i, rec in enumerate(forecast.recommendations, 1):
            lines.append(f"  {i}. {rec}")

    return "\n".join(lines)


def format_forecast_slack(forecast: ForecastResult) -> str:
    """Format forecast as Slack mrkdwn."""
    lines = []
    trend_icon = TREND_INDICATORS.get(forecast.trend, "=")
    health_label = _health_label(forecast.health_score)

    lines.append("*Sprint Forecast*")
    lines.append(
        f"Predicted Velocity: *{forecast.predicted_velocity} pts* [{trend_icon} {forecast.trend}]"
    )
    lines.append(
        f"Range: {forecast.confidence_low} - {forecast.confidence_high} pts "
        f"({forecast.confidence_level:.0%} confidence)"
    )
    lines.append(f"Health: *{forecast.health_score}/100* ({health_label})")

    if forecast.historical_velocities:
        spark = _sparkline(forecast.historical_velocities)
        lines.append(f"Trend: `{spark}`")

    if forecast.risk_factors:
        lines.append("")
        lines.append("_Risk Factors:_")
        for risk in forecast.risk_factors:
            lines.append(f"  - {risk}")

    if forecast.recommendations:
        lines.append("")
        lines.append("_Recommendations:_")
        for rec in forecast.recommendations:
            lines.append(f"  - {rec}")

    return "\n".join(lines)


def format_forecast_json(forecast: ForecastResult) -> str:
    """Format forecast as JSON."""
    data = {
        "predicted_velocity": forecast.predicted_velocity,
        "confidence_range": {
            "low": forecast.confidence_low,
            "high": forecast.confidence_high,
        },
        "confidence_level": forecast.confidence_level,
        "trend": forecast.trend,
        "health_score": forecast.health_score,
        "health_label": _health_label(forecast.health_score),
        "risk_factors": forecast.risk_factors,
        "recommendations": forecast.recommendations,
        "historical_velocities": forecast.historical_velocities,
    }
    return json.dumps(data, indent=2)


def _sparkline(values: list[float]) -> str:
    """Generate a Unicode sparkline from values."""
    if not values:
        return ""
    blocks = " _.-~*"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    return "".join(
        blocks[min(len(blocks) - 1, int((v - mn) / rng * (len(blocks) - 1)))]
        for v in values
    )


# === CLI ===

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sprint Forecast Engine")
    parser.add_argument("--sprints", type=int, default=6, help="Historical sprints to analyze")
    parser.add_argument("--sprint-length", type=int, default=14, help="Sprint length in days")
    parser.add_argument("--members", type=int, default=1, help="Team size")
    parser.add_argument("--holidays", type=int, default=0, help="Holiday days")
    parser.add_argument("--absences", type=int, default=0, help="Planned absence days")
    parser.add_argument("--format", choices=["text", "slack", "json"], default="text")
    args = parser.parse_args()

    history = extract_velocity_history(args.sprints, args.sprint_length)
    capacity = TeamCapacity(
        total_days=args.sprint_length,
        members=args.members,
        holidays=args.holidays,
        planned_absences=args.absences,
    )
    forecast = forecast_velocity(history, capacity)

    if args.format == "slack":
        print(format_forecast_slack(forecast))
    elif args.format == "json":
        print(format_forecast_json(forecast))
    else:
        print(format_forecast_text(forecast))


if __name__ == "__main__":
    main()
