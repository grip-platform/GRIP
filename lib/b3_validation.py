#!/usr/bin/env python3
"""
b3_validation.py -- B3 contraction rate statistical validation.

Validates whether converge loops genuinely contract (L < 1) using
one-sample t-tests with confidence intervals. Tracks progress toward
the N=100 target for publishable statistical significance.

Usage:
    python3 lib/b3_validation.py report    # terminal report with ETA
    python3 lib/b3_validation.py status    # machine-readable JSON
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
CONVERGE_METRICS_PATH = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"


def load_contraction_rates() -> list[dict]:
    """Load contraction rate data from converge metrics JSONL."""
    if not CONVERGE_METRICS_PATH.exists():
        return []

    rates = []
    with open(CONVERGE_METRICS_PATH) as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "converge_end":
                continue
            cr = event.get("contraction_rate", {})
            mean_rate = cr.get("mean_rate")
            if mean_rate is not None and mean_rate > 0:
                rates.append(event)
    return rates


def one_sample_t_test(values: list[float], mu_0: float = 1.0) -> dict:
    """One-sample t-test: H0: mean >= mu_0, H1: mean < mu_0 (left-tail).

    Uses scipy if available, otherwise returns None for p-value.
    """
    n = len(values)
    if n < 2:
        return {"t_stat": None, "p_value": None, "n": n, "method": "insufficient"}

    mean_val = sum(values) / n
    variance = sum((x - mean_val) ** 2 for x in values) / (n - 1)
    std_err = (variance / n) ** 0.5

    if std_err == 0:
        return {"t_stat": None, "p_value": None, "n": n, "method": "zero_variance"}

    t_stat = round((mean_val - mu_0) / std_err, 4)

    try:
        from scipy import stats
        p_value = round(stats.t.cdf(t_stat, df=n - 1), 6)
        method = "scipy"
    except ImportError:
        p_value = None
        method = "no_scipy"

    return {"t_stat": t_stat, "p_value": p_value, "n": n, "method": method}


def progress_toward_n100(current_n: int, target_n: int = 100) -> dict:
    """Track progress toward N=100 statistical target."""
    pct = round(min(current_n / target_n * 100, 100), 1)
    return {
        "current_n": current_n,
        "target_n": target_n,
        "pct_complete": pct,
        "sufficient": current_n >= target_n,
    }


def _estimate_weeks_to_target(
    current_n: int, target_n: int = 100
) -> float | None:
    """Estimate weeks to reach target N based on JSONL timestamps."""
    if not CONVERGE_METRICS_PATH.exists() or current_n < 5:
        return None

    timestamps: list[str] = []
    with open(CONVERGE_METRICS_PATH) as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "converge_end":
                continue
            cr = event.get("contraction_rate", {})
            if cr.get("mean_rate") is not None and cr["mean_rate"] > 0:
                ts = event.get("timestamp", "")
                if ts:
                    timestamps.append(ts)

    if len(timestamps) < 5:
        return None

    try:
        first = datetime.datetime.fromisoformat(timestamps[0])
        last = datetime.datetime.fromisoformat(timestamps[-1])
        span_days = (last - first).total_seconds() / 86400
        if span_days < 1:
            return None
        rate_per_day = len(timestamps) / span_days
        remaining = target_n - current_n
        days_remaining = remaining / rate_per_day
        return round(days_remaining / 7, 1)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def generate_report() -> dict:
    """Generate B3 validation report with verdict and statistics."""
    events = load_contraction_rates()
    n = len(events)

    report: dict = {"progress": progress_toward_n100(n)}

    if n == 0:
        report["verdict"] = "NO_DATA"
        report["message"] = "No converge runs with contraction data found."
        return report

    l_values = [e["contraction_rate"]["mean_rate"] for e in events]
    mean_l = round(sum(l_values) / n, 4)
    contracting = sum(1 for v in l_values if v < 1.0)

    t_result = one_sample_t_test(l_values)
    ci_95 = _confidence_interval(l_values)

    report["statistics"] = {
        "mean_L": mean_l,
        "contracting_pct": round(contracting / n * 100, 1),
        "t_test": t_result,
        "ci_95": ci_95,
    }

    # Verdict logic
    if t_result.get("p_value") is not None and t_result["p_value"] < 0.05:
        report["verdict"] = "CONFIRMED" if n >= 100 else "PROMISING"
    elif n < 10:
        report["verdict"] = "EARLY"
    else:
        report["verdict"] = "INCONCLUSIVE"

    # Run rate estimation
    if n < 100 and n >= 5:
        report["estimated_weeks"] = _estimate_weeks_to_target(n)

    return report


def _confidence_interval(
    values: list[float], confidence: float = 0.95
) -> list[float] | None:
    """Compute confidence interval for mean. Returns [lower, upper]."""
    n = len(values)
    if n < 2:
        return None

    mean_val = sum(values) / n
    variance = sum((x - mean_val) ** 2 for x in values) / (n - 1)
    std_err = (variance / n) ** 0.5

    try:
        from scipy import stats
        t_crit = stats.t.ppf((1 + confidence) / 2, df=n - 1)
    except ImportError:
        t_crit = 1.96  # z-approximation fallback

    margin = t_crit * std_err
    return [round(mean_val - margin, 4), round(mean_val + margin, 4)]


def _format_report(report: dict) -> str:
    """Format report for terminal display."""
    lines = []
    W = 50
    lines.append("B3 Contraction Rate Validation")
    lines.append("=" * W)

    v = report["verdict"]
    p = report["progress"]
    lines.append(f"  Verdict:  {v}")
    lines.append(
        f"  Progress: {p['current_n']}/{p['target_n']} ({p['pct_complete']}%)"
    )

    if "statistics" in report:
        s = report["statistics"]
        lines.append(f"  Mean L:   {s.get('mean_L', 'N/A')}")
        t = s.get("t_test", {})
        if t.get("p_value") is not None:
            lines.append(
                f"  p-value:  {t['p_value']} (t={t['t_stat']}, n={t['n']})"
            )
        if "ci_95" in s and s["ci_95"] is not None:
            lines.append(f"  95% CI:   [{s['ci_95'][0]}, {s['ci_95'][1]}]")

    if "estimated_weeks" in report and report["estimated_weeks"] is not None:
        lines.append(f"  ETA:      ~{report['estimated_weeks']} weeks to N=100")

    if "message" in report:
        lines.append(f"  Note:     {report['message']}")

    lines.append("=" * W)
    return "\n".join(lines)


def main():
    """CLI entry point."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"

    if cmd == "report":
        report = generate_report()
        print(_format_report(report))
    elif cmd == "status":
        report = generate_report()
        print(json.dumps(report, indent=2))
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python3 lib/b3_validation.py [report|status]")
        sys.exit(1)


if __name__ == "__main__":
    main()
