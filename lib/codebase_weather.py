"""Codebase Weather Report Generator.

Generates fun, data-driven "weather reports" for codebases that visualize
stability, volatility, and health as weather metaphors.

Usage:
    python3 lib/codebase_weather.py forecast [--path DIR] [--days N] [--format text|slack|json|digest|progress]
    python3 lib/codebase_weather.py history [--days N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WeatherCondition(Enum):
    """Weather conditions mapped to codebase states."""

    SUNNY = "sunny"
    PARTLY_CLOUDY = "partly_cloudy"
    CLOUDY = "cloudy"
    RAINY = "rainy"
    STORMY = "stormy"
    FOGGY = "foggy"
    WINDY = "windy"
    SNOWY = "snowy"


class Trend(Enum):
    """Directional trend for codebase health."""

    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class WeatherMetrics:
    """Raw metrics collected from the codebase.

    Attributes:
        commit_frequency: Average commits per day over the measurement window.
        churn_rate: Average files changed per day.
        test_health: Ratio of test files to source files (0.0 - 1.0).
        bug_count: Number of open bugs / known issues.
        debt_score: Technical debt score (TODO/FIXME/HACK count weighted by age).
        doc_coverage: Documentation coverage ratio (0.0 - 1.0).
        conflict_count: Number of merge conflicts or conflicting branches.
        stale_branches: Number of branches with no recent activity.
    """

    commit_frequency: float = 0.0
    churn_rate: float = 0.0
    test_health: float = 0.0
    bug_count: int = 0
    debt_score: float = 0.0
    doc_coverage: float = 0.0
    conflict_count: int = 0
    stale_branches: int = 0


@dataclass
class WeatherForecast:
    """A single weather snapshot for the codebase.

    Attributes:
        condition: The dominant weather condition.
        temperature: Stability score from 0 (frozen) to 100 (hot / highly active).
        humidity: Tech-debt percentage (0-100).
        wind_speed: Change velocity (commits + churn normalised to 0-100).
        visibility: Documentation coverage percentage (0-100).
        pressure: Deadline / urgency proxy (0-100, higher = more pressure).
    """

    condition: WeatherCondition = WeatherCondition.SUNNY
    temperature: float = 50.0
    humidity: float = 0.0
    wind_speed: float = 0.0
    visibility: float = 100.0
    pressure: float = 50.0


@dataclass
class WeatherReport:
    """Complete weather report with history, trend, and fun extras.

    Attributes:
        current_forecast: The current weather forecast.
        trend: Whether the codebase is improving, declining, or stable.
        seven_day_history: List of daily forecasts for the past 7 days.
        alerts: Actionable warning strings.
        fun_advisory: A fun, weather-themed advisory message.
        generated_at: ISO timestamp of report generation.
    """

    current_forecast: WeatherForecast = field(default_factory=WeatherForecast)
    trend: Trend = Trend.STABLE
    seven_day_history: list[WeatherForecast] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    fun_advisory: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Metric Collectors
# ---------------------------------------------------------------------------

def measure_churn(git_log: str, days: int = 7) -> float:
    """Measure file churn rate from git log output.

    Args:
        git_log: Output of ``git log --stat`` or similar.
        days: Number of days the log covers.

    Returns:
        Average files changed per day. Returns 0.0 for empty input.
    """
    if not git_log or not git_log.strip():
        return 0.0

    file_changes = 0
    for line in git_log.splitlines():
        line = line.strip()
        # Match lines like " 3 files changed, 10 insertions(+), 2 deletions(-)"
        m = re.search(r"(\d+)\s+files?\s+changed", line)
        if m:
            file_changes += int(m.group(1))
        # Also count individual file stat lines: " src/foo.py | 12 +++---"
        elif re.match(r"^\s*\S+.*\|\s+\d+", line):
            file_changes += 1

    return file_changes / max(days, 1)


def measure_test_health(path: str) -> float:
    """Estimate test health based on file ratios.

    Walks *path* and counts files that look like tests (filename contains
    ``test_`` or ``_test`` or lives under a ``tests/`` / ``test/`` directory)
    versus total source files.

    Args:
        path: Root directory to scan.

    Returns:
        Ratio between 0.0 and 1.0.  Returns 0.0 when no source files found.
    """
    source_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".sh"}
    excluded_dirs = {
        "node_modules", ".next", "dist", "build", "__pycache__",
        "venv", ".venv", "target", "vendor", "Pods", ".git",
    }

    total_source = 0
    total_tests = 0
    root = Path(path)

    if not root.is_dir():
        return 0.0

    for item in _walk_files(root, excluded_dirs):
        if item.suffix in source_extensions:
            total_source += 1
            name_lower = item.name.lower()
            parent_parts = {p.lower() for p in item.parts}
            if (
                name_lower.startswith("test_")
                or name_lower.endswith("_test" + item.suffix)
                or ".test." in name_lower
                or ".spec." in name_lower
                or "tests" in parent_parts
                or "test" in parent_parts
            ):
                total_tests += 1

    if total_source == 0:
        return 0.0
    return min(total_tests / total_source, 1.0)


def measure_debt(git_log_or_source: str) -> float:
    """Score technical debt from source content.

    Counts occurrences of ``TODO``, ``FIXME``, ``HACK``, ``XXX``, ``KLUDGE``
    and weights them.  A higher score means more debt.

    Args:
        git_log_or_source: Source code text (or concatenated file contents).

    Returns:
        Weighted debt score (float).
    """
    if not git_log_or_source:
        return 0.0

    weights = {
        "TODO": 1.0,
        "FIXME": 2.0,
        "HACK": 3.0,
        "XXX": 2.5,
        "KLUDGE": 3.0,
        "TECHDEBT": 2.0,
    }

    score = 0.0
    upper = git_log_or_source.upper()
    for keyword, weight in weights.items():
        count = upper.count(keyword)
        score += count * weight
    return score


def measure_velocity(git_log: str, days: int = 7) -> float:
    """Measure commit velocity from git log.

    Args:
        git_log: Output of ``git log --oneline`` or similar.
        days: Window size in days.

    Returns:
        Commits per day.
    """
    if not git_log or not git_log.strip():
        return 0.0

    # Count lines that look like commits (hash + message) or "commit <sha>"
    commits = 0
    for line in git_log.splitlines():
        line = line.strip()
        if not line:
            continue
        # git log --oneline: "abc1234 fix: something"
        if re.match(r"^[0-9a-f]{7,40}\s", line):
            commits += 1
        # git log default: "commit abc1234..."
        elif line.startswith("commit "):
            commits += 1

    return commits / max(days, 1)


def measure_doc_coverage(path: str) -> float:
    """Estimate documentation coverage.

    Checks for README presence and docstring density in Python files.

    Args:
        path: Root directory to scan.

    Returns:
        Score between 0.0 and 1.0.
    """
    root = Path(path)
    if not root.is_dir():
        return 0.0

    excluded_dirs = {
        "node_modules", ".next", "dist", "build", "__pycache__",
        "venv", ".venv", "target", "vendor", "Pods", ".git",
    }

    scores: list[float] = []

    # README presence
    readme_names = {"README.md", "README.rst", "README.txt", "README"}
    has_readme = any((root / name).exists() for name in readme_names)
    scores.append(1.0 if has_readme else 0.0)

    # Docstring density in Python files
    py_files = list(_walk_files(root, excluded_dirs, suffixes={".py"}))
    if py_files:
        documented = 0
        for pf in py_files:
            try:
                content = pf.read_text(errors="ignore")
                if '"""' in content or "'''" in content:
                    documented += 1
            except OSError:
                pass
        scores.append(documented / len(py_files))

    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def count_stale_branches(branch_list: str, stale_days: int = 30) -> int:
    """Count stale branches from git branch output.

    A branch is considered stale if its name suggests staleness or if
    it appears in a list of branches with dates older than *stale_days*.

    For simplicity, this counts all non-main, non-current branches from
    the raw ``git branch`` output.  If dates are provided (tab-separated
    ``branch\\tdate`` format), branches older than *stale_days* are counted.

    Args:
        branch_list: Output of ``git branch`` or ``branch\\tdate`` lines.
        stale_days: Days without activity to consider stale.

    Returns:
        Number of stale branches.
    """
    if not branch_list or not branch_list.strip():
        return 0

    protected = {"main", "master", "develop", "dev"}
    stale = 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=stale_days)

    for line in branch_list.splitlines():
        line = line.strip().lstrip("* ")
        if not line:
            continue

        # Tab-separated: "branch_name\t2026-01-15"
        parts = line.split("\t")
        name = parts[0].strip()

        if name in protected:
            continue

        if len(parts) >= 2:
            try:
                date_str = parts[1].strip()
                branch_date = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                if branch_date < cutoff:
                    stale += 1
            except (ValueError, IndexError):
                stale += 1
        else:
            # No date info — count non-protected branches
            stale += 1

    return stale


# ---------------------------------------------------------------------------
# Weather Engine
# ---------------------------------------------------------------------------

def calculate_weather(metrics: WeatherMetrics) -> WeatherForecast:
    """Determine the weather forecast from collected metrics.

    The engine maps metric combinations to weather conditions using a
    priority-based decision tree.

    Args:
        metrics: Collected codebase metrics.

    Returns:
        A WeatherForecast with condition, temperature, humidity, etc.
    """
    # -- Derived scores --
    temperature = _compute_temperature(metrics)
    humidity = _compute_humidity(metrics)
    wind_speed = _compute_wind_speed(metrics)
    visibility = metrics.doc_coverage * 100.0
    pressure = _compute_pressure(metrics)

    # -- Condition classification --
    condition = _classify_condition(metrics, temperature, humidity, wind_speed, visibility, pressure)

    return WeatherForecast(
        condition=condition,
        temperature=round(temperature, 1),
        humidity=round(humidity, 1),
        wind_speed=round(wind_speed, 1),
        visibility=round(visibility, 1),
        pressure=round(pressure, 1),
    )


def generate_advisory(forecast: WeatherForecast) -> str:
    """Generate a fun, weather-themed advisory message.

    Args:
        forecast: The current weather forecast.

    Returns:
        A human-friendly advisory string designed to make developers smile.
    """
    advisories = {
        WeatherCondition.SUNNY: [
            "Perfect coding weather! Ship that feature.",
            "Clear skies ahead. Great day to merge those PRs.",
            "Sunshine and green tests. Life is good.",
            "UV index: HIGH productivity. Apply sunscreen and ship code.",
            "Not a cloud in the CI pipeline. Deploy with confidence!",
        ],
        WeatherCondition.PARTLY_CLOUDY: [
            "Mostly clear with a chance of TODO comments.",
            "A few clouds on the horizon. Nothing a good code review can't fix.",
            "Partly cloudy: some tech debt rolling in from the west.",
            "Pack a light jacket. That legacy module might get chilly.",
            "Occasional shade from open issues. Still a good day to code.",
        ],
        WeatherCondition.CLOUDY: [
            "Overcast. Consider refactoring before the rain starts.",
            "Grey skies. Time to tackle that backlog.",
            "Cloud cover increasing. Might want to write some tests.",
            "The forecast says 'meh'. Time for some housekeeping.",
            "Clouds gathering around the TODO list. Grab a coffee.",
        ],
        WeatherCondition.RAINY: [
            "Bring an umbrella: PRs are brewing conflicts.",
            "Steady rain. Tests are getting soggy.",
            "Drizzle of bugs turning into a downpour. Prioritize fixes.",
            "Rain check on new features. Fix the leaks first.",
            "Wet code review ahead. Galoshes recommended.",
        ],
        WeatherCondition.STORMY: [
            "SEVERE WEATHER WARNING: Critical bugs detected! Seek shelter in the debugger.",
            "Thunder and lightning in the CI pipeline. All hands on deck!",
            "Storm surge: merge conflicts flooding the main branch.",
            "Tornado watch: codebase spinning out of control. Batten down the hatches!",
            "Category 5 code storm. Cancel all meetings and start fixing.",
        ],
        WeatherCondition.FOGGY: [
            "Dense fog advisory: Can't see the docs from here.",
            "Visibility near zero. Add some docstrings before someone gets lost.",
            "Fog rolling in. Even the IDE can't autocomplete this mess.",
            "Navigation hazard: no README detected in 50-mile radius.",
            "Foghorn needed. This code could use some comments.",
        ],
        WeatherCondition.WINDY: [
            "Strong winds of change! Hold onto your imports.",
            "Breezy refactoring day. Files moving at 60 mph.",
            "Wind advisory: rapid changes ahead. Pin your dependencies!",
            "Gusty commits blowing through. Good thing you have version control.",
            "Kite-flying weather: riding the refactoring winds nicely.",
        ],
        WeatherCondition.SNOWY: [
            "Frozen solid. When was the last commit... last ice age?",
            "Snow day! Nobody's been coding here for a while.",
            "Code is hibernating. Consider a warm cup of motivation.",
            "Permafrost detected. This repo needs a spring thaw.",
            "Blizzard conditions: development snowed in completely.",
        ],
    }

    options = advisories.get(forecast.condition, advisories[WeatherCondition.CLOUDY])
    # Deterministic-ish pick based on temperature
    idx = int(forecast.temperature) % len(options)
    advisory = options[idx]

    # Add "feels like" if pressure is notably different from temperature
    diff = abs(forecast.pressure - forecast.temperature)
    if diff > 15:
        feels_like = forecast.temperature + (forecast.pressure - 50) * 0.4
        advisory += f" (Feels like {feels_like:.0f} deg with that deadline pressure)"

    return advisory


def generate_alerts(forecast: WeatherForecast) -> list[str]:
    """Generate actionable weather alerts.

    Args:
        forecast: The current weather forecast.

    Returns:
        List of alert strings (may be empty if all clear).
    """
    alerts: list[str] = []

    if forecast.humidity > 70:
        alerts.append(f"HIGH HUMIDITY: Tech debt at {forecast.humidity:.0f}%. Schedule a cleanup sprint.")

    if forecast.visibility < 30:
        alerts.append(f"LOW VISIBILITY: Doc coverage at {forecast.visibility:.0f}%. Add READMEs and docstrings.")

    if forecast.wind_speed > 80:
        alerts.append(f"HIGH WIND: Change velocity at {forecast.wind_speed:.0f}. Slow down and review carefully.")

    if forecast.pressure > 80:
        alerts.append(f"HIGH PRESSURE: Urgency at {forecast.pressure:.0f}. Watch for burnout.")

    if forecast.temperature < 10:
        alerts.append(f"FROST WARNING: Activity at {forecast.temperature:.0f} deg. Is this repo still alive?")

    if forecast.temperature > 90:
        alerts.append(f"HEAT ADVISORY: Activity at {forecast.temperature:.0f} deg. Pace yourself.")

    if forecast.condition == WeatherCondition.STORMY:
        alerts.append("STORM WARNING: Multiple critical indicators. Focus on stabilization.")

    return alerts


def get_weather_emoji(condition: WeatherCondition) -> str:
    """Return an emoji for the weather condition.

    Args:
        condition: A WeatherCondition enum value.

    Returns:
        A weather emoji string.
    """
    emojis = {
        WeatherCondition.SUNNY: ":sunny:",
        WeatherCondition.PARTLY_CLOUDY: ":partly_sunny:",
        WeatherCondition.CLOUDY: ":cloud:",
        WeatherCondition.RAINY: ":rain_cloud:",
        WeatherCondition.STORMY: ":thunder_cloud_and_rain:",
        WeatherCondition.FOGGY: ":fog:",
        WeatherCondition.WINDY: ":dash:",
        WeatherCondition.SNOWY: ":snowflake:",
    }
    return emojis.get(condition, ":question:")


def get_temperature_bar(temp: float, width: int = 20) -> str:
    """Render an ASCII thermometer bar.

    Args:
        temp: Temperature value (0 - 100).
        width: Character width of the bar.

    Returns:
        ASCII art thermometer string.
    """
    clamped = max(0.0, min(100.0, temp))
    filled = int(clamped / 100.0 * width)
    empty = width - filled

    # Pick indicator based on temperature range
    if clamped < 20:
        char = "."
        label = "COLD"
    elif clamped < 40:
        char = "o"
        label = "COOL"
    elif clamped < 60:
        char = "O"
        label = "WARM"
    elif clamped < 80:
        char = "#"
        label = "HOT"
    else:
        char = "@"
        label = "BLAZING"

    bar = f"[{char * filled}{' ' * empty}] {clamped:.0f} deg ({label})"
    return bar


# ---------------------------------------------------------------------------
# Output Formatters
# ---------------------------------------------------------------------------

_SUNNY_ART = r"""
    \   /
     .-.
  -- (   ) --
     `-'
    /   \
"""

_CLOUDY_ART = r"""
     .--.
  .-(    ).
 (___.__)__)
"""

_RAINY_ART = r"""
     .--.
  .-(    ).
 (___.__)__)
  ' ' ' ' '
"""

_STORMY_ART = r"""
     .--.
  .-(    ).
 (___.__)__)
  /_/ /_/ /
    /  /
"""

_FOGGY_ART = r"""
  _ - _ - _ -
 _ - _ - _ -
  _ - _ - _ -
"""

_WINDY_ART = r"""
  ~~~~>
    ~~~>
  ~~~~>
    ~~~>
"""

_SNOWY_ART = r"""
     .--.
  .-(    ).
 (___.__)__)
  *  *  *  *
"""

_PARTLY_CLOUDY_ART = r"""
   \  /
  _ /"".-.
    \_(   ).
    /(___(__)
"""

_ASCII_ART = {
    WeatherCondition.SUNNY: _SUNNY_ART,
    WeatherCondition.PARTLY_CLOUDY: _PARTLY_CLOUDY_ART,
    WeatherCondition.CLOUDY: _CLOUDY_ART,
    WeatherCondition.RAINY: _RAINY_ART,
    WeatherCondition.STORMY: _STORMY_ART,
    WeatherCondition.FOGGY: _FOGGY_ART,
    WeatherCondition.WINDY: _WINDY_ART,
    WeatherCondition.SNOWY: _SNOWY_ART,
}


def format_text(report: WeatherReport) -> str:
    """Format a full weather report as plain text with ASCII art.

    Args:
        report: The complete weather report.

    Returns:
        Multi-line text string.
    """
    fc = report.current_forecast
    art = _ASCII_ART.get(fc.condition, _CLOUDY_ART)
    condition_label = fc.condition.value.replace("_", " ").upper()

    lines = [
        "=" * 52,
        "  CODEBASE WEATHER REPORT",
        f"  Generated: {report.generated_at[:19]}",
        "=" * 52,
        art,
        f"  Condition:   {condition_label}",
        f"  Temperature: {get_temperature_bar(fc.temperature)}",
        f"  Humidity:    {fc.humidity:.0f}% tech debt",
        f"  Wind Speed:  {fc.wind_speed:.0f} (change velocity)",
        f"  Visibility:  {fc.visibility:.0f}% (doc coverage)",
        f"  Pressure:    {fc.pressure:.0f} (deadline proximity)",
        "",
        f"  Trend: {report.trend.value.upper()}",
        "",
    ]

    if report.alerts:
        lines.append("  ALERTS:")
        for alert in report.alerts:
            lines.append(f"    ! {alert}")
        lines.append("")

    if report.fun_advisory:
        lines.append(f"  Advisory: {report.fun_advisory}")
        lines.append("")

    if report.seven_day_history:
        lines.append("  7-Day History:")
        for i, day_fc in enumerate(report.seven_day_history):
            emoji = get_weather_emoji(day_fc.condition)
            label = day_fc.condition.value.replace("_", " ")
            lines.append(f"    Day {i + 1}: {emoji} {label} ({day_fc.temperature:.0f} deg)")
        lines.append("")

    lines.append("=" * 52)
    return "\n".join(lines)


def format_slack(report: WeatherReport) -> str:
    """Format a weather report for Slack (mrkdwn).

    Uses Slack's mrkdwn syntax: *bold*, _italic_, no **bold**.

    Args:
        report: The complete weather report.

    Returns:
        Slack mrkdwn formatted string.
    """
    fc = report.current_forecast
    emoji = get_weather_emoji(fc.condition)
    condition_label = fc.condition.value.replace("_", " ").upper()

    lines = [
        f"{emoji} *Codebase Weather Report*",
        "",
        f"*Condition:* {condition_label}",
        f"*Temperature:* {fc.temperature:.0f} deg (stability)",
        f"*Humidity:* {fc.humidity:.0f}% (tech debt)",
        f"*Wind Speed:* {fc.wind_speed:.0f} (change velocity)",
        f"*Visibility:* {fc.visibility:.0f}% (doc coverage)",
        f"*Pressure:* {fc.pressure:.0f} (deadline proximity)",
        f"*Trend:* {report.trend.value}",
    ]

    if report.alerts:
        lines.append("")
        lines.append("*Alerts:*")
        for alert in report.alerts:
            lines.append(f"  :warning: {alert}")

    if report.fun_advisory:
        lines.append("")
        lines.append(f"_Advisory: {report.fun_advisory}_")

    return "\n".join(lines)


def format_json(report: WeatherReport) -> str:
    """Serialize a weather report to JSON.

    Args:
        report: The complete weather report.

    Returns:
        Pretty-printed JSON string.
    """
    data = _report_to_dict(report)
    return json.dumps(data, indent=2, default=str)


def generate_mcp_digest(report: WeatherReport) -> dict[str, Any]:
    """Generate an MCP-compatible digest with weather cards.

    Args:
        report: The complete weather report.

    Returns:
        Dictionary suitable for MCP tool responses.
    """
    fc = report.current_forecast
    emoji = get_weather_emoji(fc.condition)

    return {
        "type": "weather_digest",
        "title": f"{emoji} Codebase Weather",
        "cards": [
            {
                "label": "Condition",
                "value": fc.condition.value.replace("_", " ").upper(),
                "emoji": emoji,
            },
            {
                "label": "Temperature",
                "value": f"{fc.temperature:.0f} deg",
                "description": "Stability score (0=frozen, 100=blazing)",
            },
            {
                "label": "Humidity",
                "value": f"{fc.humidity:.0f}%",
                "description": "Tech debt level",
            },
            {
                "label": "Wind Speed",
                "value": f"{fc.wind_speed:.0f}",
                "description": "Change velocity",
            },
            {
                "label": "Visibility",
                "value": f"{fc.visibility:.0f}%",
                "description": "Documentation coverage",
            },
            {
                "label": "Pressure",
                "value": f"{fc.pressure:.0f}",
                "description": "Deadline proximity",
            },
        ],
        "trend": report.trend.value,
        "advisory": report.fun_advisory,
        "alerts": report.alerts,
        "generated_at": report.generated_at,
    }


def generate_mcp_progress(report: WeatherReport) -> dict[str, Any]:
    """Generate MCP-compatible progress bars for each metric.

    Args:
        report: The complete weather report.

    Returns:
        Dictionary with progress bar data for each metric.
    """
    fc = report.current_forecast

    def bar(value: float, max_val: float = 100.0) -> dict:
        pct = min(value / max_val * 100, 100) if max_val > 0 else 0
        return {"value": round(value, 1), "max": max_val, "percent": round(pct, 1)}

    return {
        "type": "weather_progress",
        "metrics": {
            "temperature": {**bar(fc.temperature), "label": "Stability", "unit": "deg"},
            "humidity": {**bar(fc.humidity), "label": "Tech Debt", "unit": "%"},
            "wind_speed": {**bar(fc.wind_speed), "label": "Change Velocity", "unit": ""},
            "visibility": {**bar(fc.visibility), "label": "Doc Coverage", "unit": "%"},
            "pressure": {**bar(fc.pressure), "label": "Deadline Proximity", "unit": ""},
        },
        "overall_condition": fc.condition.value,
        "trend": report.trend.value,
        "generated_at": report.generated_at,
    }


# ---------------------------------------------------------------------------
# Report Builder
# ---------------------------------------------------------------------------

def build_report(
    path: str = ".",
    days: int = 7,
    *,
    git_log_stat: Optional[str] = None,
    git_log_oneline: Optional[str] = None,
    branch_list: Optional[str] = None,
    source_content: Optional[str] = None,
) -> WeatherReport:
    """Build a complete weather report for a codebase.

    Collects metrics, calculates weather, generates advisories and alerts,
    and assembles the full report.

    Args:
        path: Root directory of the codebase.
        days: Number of days for the measurement window.
        git_log_stat: Pre-collected ``git log --stat`` output (for testing).
        git_log_oneline: Pre-collected ``git log --oneline`` output (for testing).
        branch_list: Pre-collected ``git branch`` output (for testing).
        source_content: Pre-collected source content for debt scanning.

    Returns:
        A complete WeatherReport.
    """
    # Collect raw data (allow injection for testing)
    if git_log_stat is None:
        git_log_stat = _run_git(path, ["log", f"--since={days} days ago", "--stat"])
    if git_log_oneline is None:
        git_log_oneline = _run_git(path, ["log", f"--since={days} days ago", "--oneline"])
    if branch_list is None:
        branch_list = _run_git(path, ["branch"])
    if source_content is None:
        source_content = _collect_source_markers(path)

    metrics = WeatherMetrics(
        commit_frequency=measure_velocity(git_log_oneline, days),
        churn_rate=measure_churn(git_log_stat, days),
        test_health=measure_test_health(path),
        bug_count=0,  # Would need issue tracker integration
        debt_score=measure_debt(source_content),
        doc_coverage=measure_doc_coverage(path),
        conflict_count=0,
        stale_branches=count_stale_branches(branch_list),
    )

    forecast = calculate_weather(metrics)
    alerts = generate_alerts(forecast)
    advisory = generate_advisory(forecast)
    trend = _compute_trend(git_log_oneline, days)

    return WeatherReport(
        current_forecast=forecast,
        trend=trend,
        seven_day_history=[],
        alerts=alerts,
        fun_advisory=advisory,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _walk_files(
    root: Path,
    excluded_dirs: set[str],
    suffixes: Optional[set[str]] = None,
):
    """Yield files under *root*, skipping excluded directories."""
    try:
        for item in root.iterdir():
            if item.is_dir():
                if item.name in excluded_dirs:
                    continue
                yield from _walk_files(item, excluded_dirs, suffixes)
            elif item.is_file():
                if suffixes is None or item.suffix in suffixes:
                    yield item
    except PermissionError:
        pass


def _compute_temperature(metrics: WeatherMetrics) -> float:
    """Compute stability temperature (0 = frozen, 100 = blazing).

    Higher commit frequency and lower bugs/debt -> higher temperature.
    No commits at all -> near 0.
    """
    if metrics.commit_frequency == 0 and metrics.churn_rate == 0:
        return 5.0  # Frozen

    # Base: commit frequency contributes warmth
    base = min(metrics.commit_frequency * 15, 60)

    # Test health warms things up
    base += metrics.test_health * 20

    # Bugs and debt cool things down (but don't freeze active repos)
    penalty = min(metrics.bug_count * 2 + metrics.debt_score * 0.3, 30)
    base -= penalty

    return max(0.0, min(100.0, base))


def _compute_humidity(metrics: WeatherMetrics) -> float:
    """Compute tech-debt humidity (0 = clean, 100 = swampy)."""
    # Debt score normalized
    debt_pct = min(metrics.debt_score / 50.0 * 100, 80)
    # Low test health adds humidity
    test_penalty = (1.0 - metrics.test_health) * 20
    return max(0.0, min(100.0, debt_pct + test_penalty))


def _compute_wind_speed(metrics: WeatherMetrics) -> float:
    """Compute change velocity (0 = calm, 100 = hurricane)."""
    churn_component = min(metrics.churn_rate * 5, 50)
    velocity_component = min(metrics.commit_frequency * 10, 50)
    return max(0.0, min(100.0, churn_component + velocity_component))


def _compute_pressure(metrics: WeatherMetrics) -> float:
    """Compute deadline/urgency pressure (0 = relaxed, 100 = crunch)."""
    base = 30.0
    base += metrics.bug_count * 5
    base += metrics.conflict_count * 10
    base += metrics.stale_branches * 2
    # High churn without tests = pressure
    if metrics.churn_rate > 5 and metrics.test_health < 0.3:
        base += 20
    return max(0.0, min(100.0, base))


def _classify_condition(
    metrics: WeatherMetrics,
    temperature: float,
    humidity: float,
    wind_speed: float,
    visibility: float,
    pressure: float,
) -> WeatherCondition:
    """Classify the dominant weather condition from derived scores."""

    # SNOWY: frozen — no activity
    if temperature < 10:
        return WeatherCondition.SNOWY

    # STORMY: critical problems
    if (pressure > 75 and humidity > 60) or metrics.conflict_count > 3:
        return WeatherCondition.STORMY

    # FOGGY: poor visibility / documentation
    if visibility < 25:
        return WeatherCondition.FOGGY

    # WINDY: lots of change but improving
    if wind_speed > 70 and humidity < 40:
        return WeatherCondition.WINDY

    # RAINY: under pressure with debt
    if humidity > 50 and pressure > 50:
        return WeatherCondition.RAINY

    # CLOUDY: moderate issues
    if humidity > 35 or pressure > 55:
        return WeatherCondition.CLOUDY

    # PARTLY_CLOUDY: minor issues
    if humidity > 15 or wind_speed > 40:
        return WeatherCondition.PARTLY_CLOUDY

    # SUNNY: all clear
    return WeatherCondition.SUNNY


def _compute_trend(git_log_oneline: str, days: int) -> Trend:
    """Compute a simple trend by comparing recent vs older commit density.

    Splits the log (newest-first) into two halves and counts actual commit
    lines in each half.  If more commits appear in the recent half, the
    codebase is improving; if fewer, it's declining.
    """
    if not git_log_oneline or not git_log_oneline.strip():
        return Trend.STABLE

    all_lines = [ln for ln in git_log_oneline.splitlines() if ln.strip()]
    total = len(all_lines)
    if total < 4:
        return Trend.STABLE

    def _is_commit(line: str) -> bool:
        line = line.strip()
        return bool(re.match(r"^[0-9a-f]{7,40}\s", line) or line.startswith("commit "))

    midpoint = total // 2
    recent_commits = sum(1 for ln in all_lines[:midpoint] if _is_commit(ln))
    older_commits = sum(1 for ln in all_lines[midpoint:] if _is_commit(ln))

    if older_commits == 0 and recent_commits == 0:
        return Trend.STABLE
    if older_commits == 0:
        return Trend.IMPROVING

    ratio = recent_commits / older_commits
    if ratio > 1.3:
        return Trend.IMPROVING
    elif ratio < 0.7:
        return Trend.DECLINING
    return Trend.STABLE


def _run_git(path: str, args: list[str]) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            ["git", "-C", path] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _collect_source_markers(path: str, max_files: int = 200) -> str:
    """Collect TODO/FIXME/HACK markers from source files."""
    root = Path(path)
    excluded = {
        "node_modules", ".next", "dist", "build", "__pycache__",
        "venv", ".venv", "target", "vendor", "Pods", ".git",
    }
    source_exts = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"}
    chunks: list[str] = []
    count = 0

    for item in _walk_files(root, excluded, source_exts):
        if count >= max_files:
            break
        try:
            content = item.read_text(errors="ignore")
            # Only keep lines with markers
            for line in content.splitlines():
                upper = line.upper()
                if any(kw in upper for kw in ("TODO", "FIXME", "HACK", "XXX", "KLUDGE")):
                    chunks.append(line)
        except OSError:
            pass
        count += 1

    return "\n".join(chunks)


def _report_to_dict(report: WeatherReport) -> dict[str, Any]:
    """Convert a WeatherReport to a plain dict for JSON serialization."""
    fc = report.current_forecast
    history = []
    for h in report.seven_day_history:
        history.append({
            "condition": h.condition.value,
            "temperature": h.temperature,
            "humidity": h.humidity,
            "wind_speed": h.wind_speed,
            "visibility": h.visibility,
            "pressure": h.pressure,
        })

    return {
        "current_forecast": {
            "condition": fc.condition.value,
            "temperature": fc.temperature,
            "humidity": fc.humidity,
            "wind_speed": fc.wind_speed,
            "visibility": fc.visibility,
            "pressure": fc.pressure,
        },
        "trend": report.trend.value,
        "seven_day_history": history,
        "alerts": report.alerts,
        "fun_advisory": report.fun_advisory,
        "generated_at": report.generated_at,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_cli_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        description="Codebase Weather Report Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    forecast_p = sub.add_parser("forecast", help="Generate a weather forecast")
    forecast_p.add_argument("--path", default=".", help="Codebase root directory")
    forecast_p.add_argument("--days", type=int, default=7, help="Lookback window in days")
    forecast_p.add_argument(
        "--format", dest="fmt", default="text",
        choices=["text", "slack", "json", "digest", "progress"],
        help="Output format",
    )

    history_p = sub.add_parser("history", help="Show weather history")
    history_p.add_argument("--days", type=int, default=7, help="Number of days")
    history_p.add_argument("--path", default=".", help="Codebase root directory")

    return parser


def cli_main(argv: Optional[list[str]] = None) -> int:
    """Entry point for CLI usage.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 = success).
    """
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "forecast":
        report = build_report(path=args.path, days=args.days)
        fmt = args.fmt

        if fmt == "text":
            print(format_text(report))
        elif fmt == "slack":
            print(format_slack(report))
        elif fmt == "json":
            print(format_json(report))
        elif fmt == "digest":
            print(json.dumps(generate_mcp_digest(report), indent=2, default=str))
        elif fmt == "progress":
            print(json.dumps(generate_mcp_progress(report), indent=2, default=str))
        return 0

    elif args.command == "history":
        # Generate daily reports
        report = build_report(path=args.path, days=args.days)
        print(format_text(report))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(cli_main())
