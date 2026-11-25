"""
Autonomous Insight Convergence Machine (Issue #167).

Monitors GRIP metrics, session history, and KONO memory for inflection
points where multiple signals converge. When convergence is detected,
generates actionable insights and optionally triggers deeper analysis.

Convergence Signals:
  1. Pattern Density  - 3+ similar patterns in recent sessions
  2. Fitness Decay    - Declining scores/metrics over time
  3. Friction Density - Recurring errors or blockers
  4. Feature Gap      - Missing capabilities mentioned repeatedly
  5. Cost Anomaly     - Unusual token/API spend patterns

Usage:
    python3 lib/insight_convergence.py scan              # Scan for convergence
    python3 lib/insight_convergence.py insights           # Generate insights
    python3 lib/insight_convergence.py report [text|slack|json]  # Full report
    python3 lib/insight_convergence.py triggers            # Show trigger history
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import subprocess

from lib.subprocess_utils import run_git as _run_git
from typing import Optional
from path_encoding import encode_project_path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
REPO_SLUG = os.environ.get("GRIP_REPO", "grip-platform/GRIP")

# Convergence thresholds
PATTERN_DENSITY_THRESHOLD = 3     # 3+ similar patterns = convergence
FITNESS_DECAY_WINDOW = 7          # Days to check for declining metrics
FRICTION_THRESHOLD = 3            # 3+ same-category errors = signal
COST_ANOMALY_MULTIPLIER = 2.0    # 2x average = anomaly
MAX_AUTO_TRIGGERS_PER_DAY = 2    # Safety cap


@dataclass
class ConvergenceSignal:
    """A detected convergence signal."""
    signal_type: str       # pattern_density, fitness_decay, friction, feature_gap, cost_anomaly
    confidence: float      # 0.0 - 1.0
    description: str
    evidence: list = field(default_factory=list)
    recommended_action: str = ""
    severity: str = "info"  # info, warning, critical


@dataclass
class InsightReport:
    """A complete convergence analysis report."""
    generated_at: str = ""
    signals: list = field(default_factory=list)
    convergence_score: float = 0.0   # 0-100
    top_insights: list = field(default_factory=list)
    recommended_actions: list = field(default_factory=list)
    trigger_recommendation: str = ""  # none, broly_fork, broly_cluster


def _read_jsonl(path: Path) -> list:
    """Read a JSONL file safely."""
    if not path.exists():
        return []
    entries = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except (IOError, OSError):
        pass
    return entries


def _read_json(path: Path) -> dict:
    """Read a JSON file safely."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# === Signal Detectors ===

def detect_pattern_density(days: int = 7) -> list:
    """Detect repeated patterns in recent session data.

    Looks for:
    - Repeated error types across sessions
    - Repeated tool usage patterns
    - Repeated file access patterns
    """
    signals = []
    metrics = _read_jsonl(CLAUDE_DIR / "data" / "rsi-metrics.jsonl")

    if not metrics:
        return signals

    # Filter to recent entries
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for m in metrics:
        ts = m.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt >= cutoff:
                    recent.append(m)
            except (ValueError, TypeError):
                continue

    if not recent:
        return signals

    # Count event types
    event_counts = Counter(m.get("event", "unknown") for m in recent)

    # Detect repeated patterns
    for event, count in event_counts.most_common(10):
        if count >= PATTERN_DENSITY_THRESHOLD:
            signals.append(ConvergenceSignal(
                signal_type="pattern_density",
                confidence=min(count / 10.0, 1.0),
                description=f"'{event}' occurred {count}x in {days} days",
                evidence=[f"{event}: {count} occurrences"],
                recommended_action=f"Investigate recurring '{event}' pattern",
                severity="warning" if count >= 5 else "info",
            ))

    return signals


def detect_fitness_decay(days: int = FITNESS_DECAY_WINDOW) -> list:
    """Detect declining metrics over time.

    Checks PR scores, test counts, and session achievement rates.
    """
    signals = []

    # Check PR scores from git log
    log_output = _run_git([
        "log", "--all", "--no-merges",
        f"--since={days} days ago",
        "--format=%s",
    ])

    if log_output:
        lines = log_output.split("\n")
        # Count feat vs non-feat commits
        feat_count = sum(1 for l in lines if l.lower().startswith("feat"))
        total = len(lines)

        if total >= 5:
            feat_ratio = feat_count / total
            if feat_ratio < 0.3:
                signals.append(ConvergenceSignal(
                    signal_type="fitness_decay",
                    confidence=0.7,
                    description=f"Feature ratio declining: {feat_ratio:.0%} features "
                                f"({feat_count}/{total} commits)",
                    evidence=[f"feat: {feat_count}", f"total: {total}"],
                    recommended_action="Prioritise user-facing features over infrastructure",
                    severity="warning",
                ))

    # Check RSI metrics for score trends
    metrics = _read_jsonl(CLAUDE_DIR / "data" / "rsi-metrics.jsonl")
    pr_scores = []
    for m in metrics:
        score = m.get("ralph_score")
        if score is not None:
            try:
                pr_scores.append(float(score))
            except (ValueError, TypeError):
                continue

    if len(pr_scores) >= 5:
        # Check if recent scores are lower than earlier ones
        mid = len(pr_scores) // 2
        early_avg = sum(pr_scores[:mid]) / mid
        late_avg = sum(pr_scores[mid:]) / (len(pr_scores) - mid)

        if late_avg < early_avg - 5:
            signals.append(ConvergenceSignal(
                signal_type="fitness_decay",
                confidence=0.6,
                description=f"PR scores declining: {early_avg:.0f} -> {late_avg:.0f}",
                evidence=[f"Early avg: {early_avg:.1f}", f"Recent avg: {late_avg:.1f}"],
                recommended_action="Review PR quality standards",
                severity="warning",
            ))

    return signals


def detect_friction(days: int = 7) -> list:
    """Detect recurring friction points (errors, blockers, retries).

    Analyzes session data for repeated failures.
    """
    signals = []
    metrics = _read_jsonl(CLAUDE_DIR / "data" / "rsi-metrics.jsonl")

    # Count error categories
    error_counts = Counter()
    for m in metrics:
        if m.get("event") in ("error", "failure", "retry", "blocked"):
            category = m.get("category", m.get("event", "unknown"))
            error_counts[category] += 1

    for category, count in error_counts.most_common(5):
        if count >= FRICTION_THRESHOLD:
            signals.append(ConvergenceSignal(
                signal_type="friction",
                confidence=min(count / 8.0, 1.0),
                description=f"Recurring friction: '{category}' ({count}x)",
                evidence=[f"{category}: {count} occurrences"],
                recommended_action=f"Create automation to prevent '{category}' errors",
                severity="critical" if count >= 8 else "warning",
            ))

    # Check for git index.lock issues (known friction point)
    log = _run_git(["log", "--all", "--format=%s", f"--since={days} days ago"])
    if log:
        lock_mentions = sum(1 for l in log.split("\n") if "lock" in l.lower())
        if lock_mentions >= 2:
            signals.append(ConvergenceSignal(
                signal_type="friction",
                confidence=0.8,
                description=f"Git lock contention ({lock_mentions}x in {days}d)",
                evidence=["index.lock race condition"],
                recommended_action="Implement lock-aware git wrapper (Issue #129)",
                severity="warning",
            ))

    return signals


def detect_feature_gaps(days: int = 14) -> list:
    """Detect missing capabilities mentioned repeatedly.

    Scans issues, MEMORY, and session data for repeated feature requests.
    """
    signals = []

    # Read MEMORY for pending items
    memory_path = CLAUDE_DIR / "projects" / encode_project_path(CLAUDE_DIR) / "memory" / "MEMORY.md"
    if memory_path.exists():
        try:
            content = memory_path.read_text()
            # Count PENDING items
            pending = content.count("PENDING")
            if pending >= 10:
                signals.append(ConvergenceSignal(
                    signal_type="feature_gap",
                    confidence=0.7,
                    description=f"{pending} pending backlog items detected",
                    evidence=[f"PENDING count: {pending}"],
                    recommended_action="Run backlog prioritiser to triage",
                    severity="info",
                ))
        except IOError:
            pass

    # Check GitHub issues count
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "-R", REPO_SLUG, "--state", "open",
             "--json", "number", "--limit", "200"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            issues = json.loads(result.stdout)
            if len(issues) >= 20:
                signals.append(ConvergenceSignal(
                    signal_type="feature_gap",
                    confidence=0.5,
                    description=f"{len(issues)} open issues — backlog growing",
                    evidence=[f"Open issues: {len(issues)}"],
                    recommended_action="Sprint-loop automation to reduce backlog",
                    severity="warning" if len(issues) >= 30 else "info",
                ))
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return signals


def detect_cost_anomaly() -> list:
    """Detect unusual token/API spend patterns."""
    signals = []
    metrics = _read_jsonl(CLAUDE_DIR / "data" / "rsi-metrics.jsonl")

    costs = []
    for m in metrics:
        cost = m.get("estimated_cost")
        if cost is not None:
            try:
                costs.append(float(cost))
            except (ValueError, TypeError):
                continue

    if len(costs) >= 3:
        avg_cost = sum(costs) / len(costs)
        latest = costs[-1]

        if latest > avg_cost * COST_ANOMALY_MULTIPLIER and avg_cost > 0:
            signals.append(ConvergenceSignal(
                signal_type="cost_anomaly",
                confidence=0.8,
                description=f"Cost spike: ${latest:.2f} vs avg ${avg_cost:.2f}",
                evidence=[f"Latest: ${latest:.2f}", f"Average: ${avg_cost:.2f}"],
                recommended_action="Review recent session for token waste",
                severity="warning",
            ))

    return signals


# === Convergence Engine ===

def scan_convergence(days: int = 7) -> InsightReport:
    """Run all convergence detectors and compile a report.

    Returns an InsightReport with all detected signals, a convergence
    score, and recommended actions.
    """
    all_signals = []

    # Run all detectors
    all_signals.extend(detect_pattern_density(days))
    all_signals.extend(detect_fitness_decay(days))
    all_signals.extend(detect_friction(days))
    all_signals.extend(detect_feature_gaps(days))
    all_signals.extend(detect_cost_anomaly())

    # Calculate convergence score
    if not all_signals:
        score = 0.0
    else:
        # Weighted by confidence and severity
        severity_weights = {"critical": 3.0, "warning": 2.0, "info": 1.0}
        weighted_sum = sum(
            s.confidence * severity_weights.get(s.severity, 1.0)
            for s in all_signals
        )
        # Normalize to 0-100 scale
        score = min(weighted_sum * 10, 100.0)

    # Generate top insights (deduplicated)
    seen_types = set()
    top_insights = []
    for s in sorted(all_signals, key=lambda x: x.confidence, reverse=True):
        key = (s.signal_type, s.description[:30])
        if key not in seen_types:
            seen_types.add(key)
            top_insights.append(s.description)
        if len(top_insights) >= 5:
            break

    # Generate recommended actions
    actions = []
    for s in all_signals:
        if s.recommended_action and s.recommended_action not in actions:
            actions.append(s.recommended_action)

    # Determine trigger recommendation
    trigger = "none"
    critical_count = sum(1 for s in all_signals if s.severity == "critical")
    warning_count = sum(1 for s in all_signals if s.severity == "warning")

    if critical_count >= 2 or score >= 70:
        trigger = "broly_cluster"
    elif warning_count >= 3 or score >= 40:
        trigger = "broly_fork"

    return InsightReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        signals=all_signals,
        convergence_score=round(score, 1),
        top_insights=top_insights[:5],
        recommended_actions=actions[:5],
        trigger_recommendation=trigger,
    )


# === Formatters ===

def format_report_text(report: InsightReport) -> str:
    """Format convergence report as plain text."""
    lines = [
        "=" * 60,
        "  Insight Convergence Report",
        f"  Score: {report.convergence_score:.0f}/100 | "
        f"Signals: {len(report.signals)} | "
        f"Trigger: {report.trigger_recommendation}",
        "=" * 60,
        "",
    ]

    if report.top_insights:
        lines.append("TOP INSIGHTS:")
        for i, insight in enumerate(report.top_insights, 1):
            lines.append(f"  {i}. {insight}")
        lines.append("")

    if report.signals:
        # Group by type
        by_type = defaultdict(list)
        for s in report.signals:
            by_type[s.signal_type].append(s)

        type_labels = {
            "pattern_density": "Pattern Density",
            "fitness_decay": "Fitness Decay",
            "friction": "Friction Points",
            "feature_gap": "Feature Gaps",
            "cost_anomaly": "Cost Anomalies",
        }

        for sig_type, sigs in by_type.items():
            label = type_labels.get(sig_type, sig_type.title())
            lines.append(f"{label} ({len(sigs)}):")
            for s in sigs:
                severity_marker = {"critical": "!", "warning": "*", "info": "-"}.get(s.severity, "-")
                lines.append(f"  {severity_marker} [{s.confidence:.0%}] {s.description}")
            lines.append("")

    if report.recommended_actions:
        lines.append("RECOMMENDED ACTIONS:")
        for i, action in enumerate(report.recommended_actions, 1):
            lines.append(f"  {i}. {action}")
        lines.append("")

    return "\n".join(lines)


def format_report_slack(report: InsightReport) -> str:
    """Format convergence report as Slack mrkdwn."""
    lines = [
        "*Insight Convergence Report*",
        f"_Score: {report.convergence_score:.0f}/100 | "
        f"{len(report.signals)} signals | "
        f"Trigger: {report.trigger_recommendation}_",
        "",
    ]

    if report.top_insights:
        lines.append("*Top Insights*")
        for insight in report.top_insights:
            lines.append(f"  - {insight}")
        lines.append("")

    if report.signals:
        by_type = defaultdict(list)
        for s in report.signals:
            by_type[s.signal_type].append(s)

        type_labels = {
            "pattern_density": "Pattern Density",
            "fitness_decay": "Fitness Decay",
            "friction": "Friction Points",
            "feature_gap": "Feature Gaps",
            "cost_anomaly": "Cost Anomalies",
        }

        for sig_type, sigs in by_type.items():
            label = type_labels.get(sig_type, sig_type.title())
            lines.append(f"*{label}* ({len(sigs)})")
            for s in sigs[:5]:
                lines.append(f"  - [{s.severity}] {s.description}")
            lines.append("")

    if report.recommended_actions:
        lines.append("*Actions*")
        for action in report.recommended_actions:
            lines.append(f"  - {action}")

    return "\n".join(lines)


def format_report_json(report: InsightReport) -> str:
    """Format convergence report as JSON."""
    data = {
        "generated_at": report.generated_at,
        "convergence_score": report.convergence_score,
        "signal_count": len(report.signals),
        "trigger_recommendation": report.trigger_recommendation,
        "top_insights": report.top_insights,
        "recommended_actions": report.recommended_actions,
        "signals": [
            {
                "type": s.signal_type,
                "confidence": round(s.confidence, 2),
                "severity": s.severity,
                "description": s.description,
                "evidence": s.evidence,
                "action": s.recommended_action,
            }
            for s in report.signals
        ],
    }
    return json.dumps(data, indent=2)


# === Trigger History ===

def load_trigger_history() -> list:
    """Load trigger history from disk."""
    path = CLAUDE_DIR / "data" / "convergence-triggers.jsonl"
    return _read_jsonl(path)


def record_trigger(report: InsightReport) -> None:
    """Record a convergence trigger event."""
    path = CLAUDE_DIR / "data" / "convergence-triggers.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": report.generated_at,
        "score": report.convergence_score,
        "signal_count": len(report.signals),
        "trigger": report.trigger_recommendation,
        "top_insights": report.top_insights[:3],
    }

    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def can_auto_trigger() -> bool:
    """Check if auto-trigger is allowed (max 2 per 24h)."""
    history = load_trigger_history()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    recent_triggers = 0
    for entry in history:
        ts = entry.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt >= cutoff:
                    recent_triggers += 1
            except (ValueError, TypeError):
                continue

    return recent_triggers < MAX_AUTO_TRIGGERS_PER_DAY


FORMATTERS = {
    "text": format_report_text,
    "slack": format_report_slack,
    "json": format_report_json,
}


def main():
    """CLI entry point."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"

    if cmd == "scan":
        report = scan_convergence()
        print(f"Convergence score: {report.convergence_score:.0f}/100")
        print(f"Signals: {len(report.signals)}")
        print(f"Trigger recommendation: {report.trigger_recommendation}")
        if report.top_insights:
            print("\nTop insights:")
            for i, insight in enumerate(report.top_insights, 1):
                print(f"  {i}. {insight}")

    elif cmd == "insights":
        report = scan_convergence()
        for s in report.signals:
            print(f"[{s.severity}] {s.signal_type}: {s.description}")

    elif cmd == "report":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "text"
        report = scan_convergence()
        formatter = FORMATTERS.get(fmt, format_report_text)
        print(formatter(report))

    elif cmd == "triggers":
        history = load_trigger_history()
        if not history:
            print("No trigger history")
        else:
            for entry in history[-10:]:
                print(f"  [{entry.get('timestamp', '?')}] "
                      f"Score: {entry.get('score', 0):.0f} "
                      f"Trigger: {entry.get('trigger', 'none')}")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: scan, insights, report [text|slack|json], triggers")
        sys.exit(1)


if __name__ == "__main__":
    main()
