"""GRIP Metrics Dashboard -- unified real-time metrics aggregation.

Aggregates 6 data sources (session, genome, converge, velocity, cost, activity)
into bordered ASCII panels with sparklines and alert detection.
Pure Python, no external dependencies.
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from enum import Enum
from path_encoding import encode_project_path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class PanelType(Enum):
    SESSION = "session"
    GENOME = "genome"
    CONVERGE = "converge"
    VELOCITY = "velocity"
    COST = "cost"
    ACTIVITY = "activity"


@dataclass
class AlertInfo:
    severity: Severity
    source: str
    message: str
    timestamp: float = 0.0


@dataclass
class SparklineData:
    label: str
    values: List[float]


@dataclass
class DashboardPanel:
    name: str
    panel_type: PanelType
    title: str
    data: Dict[str, Any]
    sparklines: List[SparklineData] = field(default_factory=list)
    alerts: List[AlertInfo] = field(default_factory=list)
    rendered_lines: List[str] = field(default_factory=list)


@dataclass
class DashboardState:
    panels: List[DashboardPanel]
    timestamp: float
    alerts: List[AlertInfo]
    refresh_count: int = 0


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def collect_session_data(claude_dir: Path) -> Dict[str, Any]:
    """Read recent git log, count commits, check session state files."""
    data: Dict[str, Any] = {
        "generation": 0,
        "commits_today": 0,
        "open_prs": 0,
        "branch": "unknown",
        "duration_minutes": 0,
    }

    # Read MEMORY.md for generation
    memory_path = claude_dir / "projects" / encode_project_path(claude_dir) / "memory" / "MEMORY.md"
    if not memory_path.exists():
        # Try alternative location
        memory_path = claude_dir / "MEMORY.md"
    if memory_path.exists():
        try:
            content = memory_path.read_text(errors="replace")
            for line in content.splitlines():
                if line.startswith("Gen ") and "|" in line:
                    parts = line.split("|")
                    gen_part = parts[0].strip()
                    gen_num = gen_part.replace("Gen ", "").strip()
                    try:
                        data["generation"] = int(gen_num)
                    except ValueError:
                        pass
        except OSError:
            pass

    # Git data
    git_dir = claude_dir
    if (claude_dir / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(git_dir), "rev-list", "--count",
                 "--since=midnight", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                data["commits_today"] = int(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass

        try:
            result = subprocess.run(
                ["git", "-C", str(git_dir), "branch", "--show-current"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                data["branch"] = result.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Session instances
    instances_dir = claude_dir / "cache" / "instances"
    if instances_dir.exists():
        try:
            instance_files = sorted(instances_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if instance_files:
                latest = json.loads(instance_files[0].read_text(errors="replace"))
                if "generation" in latest:
                    data["generation"] = max(data["generation"], latest.get("generation", 0))
                if "start_time" in latest:
                    elapsed = time.time() - latest["start_time"]
                    data["duration_minutes"] = int(elapsed / 60)
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    return data


def collect_genome_data(claude_dir: Path) -> Dict[str, Any]:
    """Load genome, count genes by tier, fitness stats, recent evolution."""
    data: Dict[str, Any] = {
        "total_genes": 0,
        "active_genes": 0,
        "dormant_genes": 0,
        "mean_fitness": 0.0,
        "min_fitness": 0.0,
        "max_fitness": 0.0,
        "fitness_history": [],
        "low_fitness_count": 0,
    }

    genome_path = claude_dir / "config" / "genome.json"
    if not genome_path.exists():
        genome_path = claude_dir / "genome.json"
    if not genome_path.exists():
        return data

    try:
        genome = json.loads(genome_path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return data

    genes = genome.get("genes", genome.get("principles", []))
    if isinstance(genes, dict):
        genes = list(genes.values())

    if not genes:
        return data

    data["total_genes"] = len(genes)
    fitnesses = []

    for gene in genes:
        fitness = 0.0
        if isinstance(gene, dict):
            fitness = gene.get("fitness", gene.get("score", 0.0))
            tier = gene.get("tier", "active")
            if tier == "dormant":
                data["dormant_genes"] += 1
            else:
                data["active_genes"] += 1
        else:
            data["active_genes"] += 1

        try:
            fitness = float(fitness)
        except (TypeError, ValueError):
            fitness = 0.0
        fitnesses.append(fitness)
        if fitness < 0.3:
            data["low_fitness_count"] += 1

    if fitnesses:
        data["mean_fitness"] = round(sum(fitnesses) / len(fitnesses), 3)
        data["min_fitness"] = round(min(fitnesses), 3)
        data["max_fitness"] = round(max(fitnesses), 3)
        data["fitness_history"] = [round(f, 3) for f in fitnesses[-20:]]

    return data


def collect_converge_data(claude_dir: Path) -> Dict[str, Any]:
    """Read metrics JSONL, count runs, avg score, recent trajectories."""
    data: Dict[str, Any] = {
        "total_runs": 0,
        "avg_score": 0.0,
        "recent_scores": [],
        "last_run_timestamp": 0.0,
        "hours_since_last": 0.0,
    }

    metrics_path = claude_dir / "cache" / "converge_metrics.jsonl"
    if not metrics_path.exists():
        metrics_path = claude_dir / "converge_metrics.jsonl"
    if not metrics_path.exists():
        return data

    try:
        lines = metrics_path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return data

    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not entries:
        return data

    data["total_runs"] = len(entries)
    scores = []
    for entry in entries:
        score = entry.get("score", entry.get("final_score", entry.get("quality", 0.0)))
        try:
            scores.append(float(score))
        except (TypeError, ValueError):
            continue
        ts = entry.get("timestamp", entry.get("time", 0.0))
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            ts = 0.0
        if ts > data["last_run_timestamp"]:
            data["last_run_timestamp"] = ts

    if scores:
        data["avg_score"] = round(sum(scores) / len(scores), 1)
        data["recent_scores"] = [round(s, 1) for s in scores[-20:]]

    if data["last_run_timestamp"] > 0:
        data["hours_since_last"] = round(
            (time.time() - data["last_run_timestamp"]) / 3600, 1
        )

    return data


def collect_velocity_data(claude_dir: Path) -> Dict[str, Any]:
    """Read sprint data, PRs per wave, acceleration."""
    data: Dict[str, Any] = {
        "prs_per_wave": 0.0,
        "acceleration_pct": 0.0,
        "total_prs": 0,
        "wave_history": [],
        "current_wave": 0,
    }

    velocity_path = claude_dir / "cache" / "velocity.json"
    if not velocity_path.exists():
        velocity_path = claude_dir / "velocity.json"
    if not velocity_path.exists():
        return data

    try:
        velocity = json.loads(velocity_path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return data

    waves = velocity.get("waves", [])
    if isinstance(waves, list) and waves:
        data["current_wave"] = len(waves)
        pr_counts = []
        for wave in waves:
            if isinstance(wave, dict):
                count = wave.get("prs", wave.get("pr_count", 0))
            else:
                try:
                    count = int(wave)
                except (TypeError, ValueError):
                    count = 0
            pr_counts.append(count)

        data["wave_history"] = pr_counts[-20:]
        data["total_prs"] = sum(pr_counts)
        if pr_counts:
            data["prs_per_wave"] = round(sum(pr_counts) / len(pr_counts), 1)
        if len(pr_counts) >= 2:
            recent = pr_counts[-1]
            previous = pr_counts[-2] if pr_counts[-2] != 0 else 1
            data["acceleration_pct"] = round(
                ((recent - previous) / abs(previous)) * 100, 1
            )

    return data


def collect_cost_data(claude_dir: Path) -> Dict[str, Any]:
    """Estimate cost from session data (if cost_analyzer data exists)."""
    data: Dict[str, Any] = {
        "total_cost_usd": 0.0,
        "session_cost_usd": 0.0,
        "cost_per_commit": 0.0,
        "cost_history": [],
    }

    cost_path = claude_dir / "cache" / "cost_data.json"
    if not cost_path.exists():
        cost_path = claude_dir / "cost_data.json"
    if not cost_path.exists():
        return data

    try:
        cost = json.loads(cost_path.read_text(errors="replace"))
    except (json.JSONDecodeError, OSError):
        return data

    data["total_cost_usd"] = round(cost.get("total_usd", 0.0), 2)
    data["session_cost_usd"] = round(cost.get("session_usd", 0.0), 2)
    data["cost_per_commit"] = round(cost.get("per_commit_usd", 0.0), 2)
    history = cost.get("history", [])
    if isinstance(history, list):
        data["cost_history"] = [round(float(v), 2) for v in history[-20:] if _is_numeric(v)]

    return data


def collect_activity_data(claude_dir: Path) -> Dict[str, Any]:
    """Recent events (last 10 commits, PRs, converge runs)."""
    data: Dict[str, Any] = {
        "recent_commits": [],
        "recent_events": [],
    }

    git_dir = claude_dir
    if (claude_dir / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(git_dir), "log", "--oneline", "-10",
                 "--format=%h %s"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if line.strip():
                        data["recent_commits"].append(line.strip())
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Activity log
    activity_path = claude_dir / "cache" / "activity.jsonl"
    if activity_path.exists():
        try:
            lines = activity_path.read_text(errors="replace").strip().splitlines()
            for line in lines[-10:]:
                try:
                    data["recent_events"].append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    return data


# ---------------------------------------------------------------------------
# Alert detection
# ---------------------------------------------------------------------------


def detect_alerts(panels: List[DashboardPanel]) -> List[AlertInfo]:
    """Check for anomalies across all panels."""
    alerts: List[AlertInfo] = []
    now = time.time()

    for panel in panels:
        d = panel.data

        if panel.panel_type == PanelType.GENOME:
            low = d.get("low_fitness_count", 0)
            if low > 0:
                sev = Severity.CRITICAL if low >= 5 else Severity.WARNING
                alerts.append(AlertInfo(
                    severity=sev,
                    source="genome",
                    message=f"{low} gene{'s' if low != 1 else ''} below fitness 0.3",
                    timestamp=now,
                ))
            mean = d.get("mean_fitness", 1.0)
            if mean < 0.5:
                alerts.append(AlertInfo(
                    severity=Severity.CRITICAL,
                    source="genome",
                    message=f"Mean fitness critically low: {mean}",
                    timestamp=now,
                ))

        elif panel.panel_type == PanelType.CONVERGE:
            hours = d.get("hours_since_last", 0.0)
            if hours > 24 and d.get("total_runs", 0) > 0:
                alerts.append(AlertInfo(
                    severity=Severity.WARNING,
                    source="converge",
                    message=f"No converge runs in {hours:.0f}h",
                    timestamp=now,
                ))
            avg = d.get("avg_score", 100.0)
            if avg < 50 and d.get("total_runs", 0) > 0:
                alerts.append(AlertInfo(
                    severity=Severity.WARNING,
                    source="converge",
                    message=f"Average converge score low: {avg}",
                    timestamp=now,
                ))

        elif panel.panel_type == PanelType.VELOCITY:
            accel = d.get("acceleration_pct", 0.0)
            if accel < -50:
                alerts.append(AlertInfo(
                    severity=Severity.WARNING,
                    source="velocity",
                    message=f"Velocity declining sharply: {accel}%",
                    timestamp=now,
                ))

        elif panel.panel_type == PanelType.COST:
            per_commit = d.get("cost_per_commit", 0.0)
            if per_commit > 5.0:
                alerts.append(AlertInfo(
                    severity=Severity.WARNING,
                    source="cost",
                    message=f"Cost per commit high: ${per_commit:.2f}",
                    timestamp=now,
                ))

    return alerts


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_sparkline(values: List[float], width: int = 20) -> str:
    """Render ASCII sparkline from numeric values."""
    if not values:
        return ""

    # Take last `width` values
    vals = values[-width:]

    min_v = min(vals)
    max_v = max(vals)
    rng = max_v - min_v

    chars = []
    for v in vals:
        if rng == 0:
            idx = 4  # middle
        else:
            idx = int(((v - min_v) / rng) * (len(SPARKLINE_CHARS) - 1))
            idx = max(0, min(idx, len(SPARKLINE_CHARS) - 1))
        chars.append(SPARKLINE_CHARS[idx])

    return "".join(chars)


def render_panel(panel: DashboardPanel, width: int = 40) -> List[str]:
    """Render a single bordered ASCII panel."""
    inner_width = width - 4  # 2 for borders, 2 for padding
    lines: List[str] = []

    # Title bar: top = corner + dash + title_text + dashes + corner
    # Total = 1 + 1 + len(title_text) + remaining + 1 = width
    # So remaining = width - 3 - len(title_text)
    title_text = f" {panel.title} "
    max_title = width - 4  # leave room for corners + minimum 1 dash each side
    if len(title_text) > max_title:
        title_text = title_text[:max_title]
    remaining = width - 3 - len(title_text)
    if remaining < 0:
        remaining = 0
    top = "\u250c\u2500" + title_text + "\u2500" * remaining + "\u2510"
    lines.append(top)

    # Data lines
    content_lines = _format_panel_content(panel, inner_width)
    for cl in content_lines:
        padded = cl[:inner_width].ljust(inner_width)
        lines.append(f"\u2502 {padded} \u2502")

    # Sparklines
    for sp in panel.sparklines:
        spark = render_sparkline(sp.values, inner_width - len(sp.label) - 2)
        spark_line = f"{sp.label}: {spark}"
        padded = spark_line[:inner_width].ljust(inner_width)
        lines.append(f"\u2502 {padded} \u2502")

    # Panel alerts
    for alert in panel.alerts:
        icon = _severity_icon(alert.severity)
        msg = f"{icon} {alert.message}"
        padded = msg[:inner_width].ljust(inner_width)
        lines.append(f"\u2502 {padded} \u2502")

    # Bottom border
    bottom = "\u2514" + "\u2500" * (width - 2) + "\u2518"
    lines.append(bottom)

    panel.rendered_lines = lines
    return lines


def _format_panel_content(panel: DashboardPanel, width: int) -> List[str]:
    """Format panel data into content lines."""
    d = panel.data
    lines: List[str] = []

    if panel.panel_type == PanelType.SESSION:
        lines.append(f"Gen: {d.get('generation', '?')}")
        lines.append(f"Commits today: {d.get('commits_today', 0)}")
        lines.append(f"Branch: {d.get('branch', 'unknown')}")
        dur = d.get("duration_minutes", 0)
        if dur >= 60:
            lines.append(f"Duration: {dur // 60}h {dur % 60}m")
        else:
            lines.append(f"Duration: {dur}m")

    elif panel.panel_type == PanelType.GENOME:
        total = d.get("total_genes", 0)
        active = d.get("active_genes", 0)
        dormant = d.get("dormant_genes", 0)
        lines.append(f"Genes: {total} ({active} active, {dormant} dorm)")
        lines.append(f"Mean Fitness: {d.get('mean_fitness', 0.0)}")
        lines.append(f"Range: {d.get('min_fitness', 0.0)} - {d.get('max_fitness', 0.0)}")

    elif panel.panel_type == PanelType.CONVERGE:
        lines.append(f"Runs: {d.get('total_runs', 0)}")
        lines.append(f"Avg Score: {d.get('avg_score', 0.0)}")
        hours = d.get("hours_since_last", 0.0)
        if hours > 0:
            lines.append(f"Last run: {hours:.1f}h ago")

    elif panel.panel_type == PanelType.VELOCITY:
        lines.append(f"PRs/Wave: {d.get('prs_per_wave', 0.0)}")
        lines.append(f"Total PRs: {d.get('total_prs', 0)}")
        accel = d.get("acceleration_pct", 0.0)
        sign = "+" if accel >= 0 else ""
        lines.append(f"Acceleration: {sign}{accel}%")

    elif panel.panel_type == PanelType.COST:
        lines.append(f"Total: ${d.get('total_cost_usd', 0.0):.2f}")
        lines.append(f"Session: ${d.get('session_cost_usd', 0.0):.2f}")
        lines.append(f"Per Commit: ${d.get('cost_per_commit', 0.0):.2f}")

    elif panel.panel_type == PanelType.ACTIVITY:
        commits = d.get("recent_commits", [])
        if commits:
            lines.append("Recent commits:")
            for c in commits[:5]:
                truncated = c[:width - 2]
                lines.append(f"  {truncated}")
        else:
            lines.append("No recent activity")

    return lines


def _severity_icon(severity: Severity) -> str:
    """Return text icon for severity level."""
    return {
        Severity.INFO: "[i]",
        Severity.WARNING: "[!]",
        Severity.CRITICAL: "[!!]",
    }.get(severity, "[?]")


def render_dashboard(state: DashboardState, compact: bool = False) -> str:
    """Full dashboard with all panels in 2-column layout."""
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(state.timestamp))
    output_lines: List[str] = []
    output_lines.append(f"GRIP Metrics Dashboard ({ts})")
    output_lines.append("=" * 50)
    output_lines.append("")

    if compact:
        # Single column, minimal
        for panel in state.panels:
            rendered = render_panel(panel, width=48)
            output_lines.extend(rendered)
            output_lines.append("")
    else:
        # 2-column layout
        panel_width = 24
        pairs = []
        for i in range(0, len(state.panels), 2):
            left = state.panels[i]
            right = state.panels[i + 1] if i + 1 < len(state.panels) else None
            pairs.append((left, right))

        for left, right in pairs:
            left_lines = render_panel(left, width=panel_width)
            right_lines = render_panel(right, width=panel_width) if right else []

            max_h = max(len(left_lines), len(right_lines))
            empty_left = " " * panel_width
            empty_right = " " * panel_width

            for row in range(max_h):
                l = left_lines[row] if row < len(left_lines) else empty_left
                r = right_lines[row] if row < len(right_lines) else empty_right
                output_lines.append(f"{l}  {r}")
            output_lines.append("")

    # Alerts section
    if state.alerts:
        output_lines.append(f"Alerts ({len(state.alerts)}):")
        for alert in state.alerts:
            icon = _severity_icon(alert.severity)
            output_lines.append(f"  {icon} {alert.source}: {alert.message}")
    else:
        output_lines.append("Alerts: None")

    output_lines.append("")
    return "\n".join(output_lines)


# ---------------------------------------------------------------------------
# Dashboard building
# ---------------------------------------------------------------------------

# Mapping of panel type to collector function name
_COLLECTORS = {
    PanelType.SESSION: ("session", "Session", collect_session_data),
    PanelType.GENOME: ("genome", "Genome Health", collect_genome_data),
    PanelType.CONVERGE: ("converge", "Converge Stats", collect_converge_data),
    PanelType.VELOCITY: ("velocity", "Sprint Velocity", collect_velocity_data),
    PanelType.COST: ("cost", "Cost Tracker", collect_cost_data),
    PanelType.ACTIVITY: ("activity", "Recent Activity", collect_activity_data),
}

# Default panel order
DEFAULT_PANELS = [
    PanelType.SESSION,
    PanelType.GENOME,
    PanelType.CONVERGE,
    PanelType.VELOCITY,
    PanelType.COST,
    PanelType.ACTIVITY,
]


def build_dashboard(
    claude_dir: Optional[Path] = None,
    panels: Optional[List[str]] = None,
) -> DashboardState:
    """Collect all data, build panels, detect alerts."""
    if claude_dir is None:
        claude_dir = CLAUDE_DIR

    # Determine which panels to include
    if panels:
        panel_types = []
        name_map = {pt.value: pt for pt in PanelType}
        for name in panels:
            name_lower = name.lower()
            if name_lower in name_map:
                panel_types.append(name_map[name_lower])
    else:
        panel_types = list(DEFAULT_PANELS)

    built_panels: List[DashboardPanel] = []
    for pt in panel_types:
        name, title, collector = _COLLECTORS[pt]
        data = collector(claude_dir)

        sparklines = []
        if pt == PanelType.GENOME and data.get("fitness_history"):
            sparklines.append(SparklineData(label="Trend", values=data["fitness_history"]))
        elif pt == PanelType.CONVERGE and data.get("recent_scores"):
            sparklines.append(SparklineData(label="Scores", values=data["recent_scores"]))
        elif pt == PanelType.VELOCITY and data.get("wave_history"):
            sparklines.append(SparklineData(label="Waves", values=[float(v) for v in data["wave_history"]]))
        elif pt == PanelType.COST and data.get("cost_history"):
            sparklines.append(SparklineData(label="Cost", values=data["cost_history"]))

        panel = DashboardPanel(
            name=name,
            panel_type=pt,
            title=title,
            data=data,
            sparklines=sparklines,
        )
        built_panels.append(panel)

    alerts = detect_alerts(built_panels)

    # Assign per-panel alerts
    for alert in alerts:
        for panel in built_panels:
            if panel.name == alert.source:
                panel.alerts.append(alert)
                break

    return DashboardState(
        panels=built_panels,
        timestamp=time.time(),
        alerts=alerts,
        refresh_count=0,
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_state(state: DashboardState) -> str:
    """JSON export of dashboard state."""
    def _serialize(obj: Any) -> Any:
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        return str(obj)

    raw = asdict(state)
    return json.dumps(raw, default=_serialize, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_numeric(v: Any) -> bool:
    """Check if a value can be converted to float."""
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="GRIP Metrics Dashboard")
    parser.add_argument("--panels", nargs="*", help="Panel names to show")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--compact", action="store_true", help="Compact layout")
    parser.add_argument("--claude-dir", type=str, help="Override CLAUDE_DIR")
    args = parser.parse_args()

    claude_dir = Path(args.claude_dir) if args.claude_dir else CLAUDE_DIR
    state = build_dashboard(claude_dir=claude_dir, panels=args.panels)

    if args.json:
        print(export_state(state))
    else:
        print(render_dashboard(state, compact=args.compact))


if __name__ == "__main__":
    main()
