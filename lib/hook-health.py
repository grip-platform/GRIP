#!/usr/bin/env python3
"""Hook health check — reads sentinel log and reports hook execution status.

INS-05: Verifies that all expected hooks actually fired.

Usage:
    python3 ~/.claude/lib/hook-health.py [--window MINUTES] [--json]

Options:
    --window N    Only consider records from last N minutes (default: 60)
    --json        Output raw JSON instead of formatted report
"""

import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

SENTINEL_LOG = os.path.join(
    os.environ.get("HOME", "/tmp"), ".claude", "cache", "sentinel.jsonl"
)
CLASSIFICATION_PATH = os.path.join(
    os.environ.get("HOME", "/tmp"), ".claude", "config", "hook-classification.json"
)

EXPECTED_EVENTS = [
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "PreCompact",
    "PostCompact",
    "SessionEnd",
    "Stop",
    "TaskCompleted",
    "ConfigChange",
    "Notification",
    "Elicitation",
    "ElicitationResult",
]


def load_classification():
    """Load hook classification from config file."""
    if not os.path.exists(CLASSIFICATION_PATH):
        return {"hard": 0, "soft": 0, "hard_hooks": [], "soft_hooks": []}
    try:
        with open(CLASSIFICATION_PATH) as f:
            data = json.load(f)
        hard_hooks = list(data.get("hard", {}).get("hooks", {}).keys())
        soft_hooks = list(data.get("soft", {}).get("hooks", {}).keys())
        return {
            "hard": len(hard_hooks),
            "soft": len(soft_hooks),
            "hard_hooks": hard_hooks,
            "soft_hooks": soft_hooks,
        }
    except (json.JSONDecodeError, OSError):
        return {"hard": 0, "soft": 0, "hard_hooks": [], "soft_hooks": []}


def parse_args():
    window = 60
    output_json = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--window" and i + 1 < len(args):
            window = int(args[i + 1])
            i += 2
        elif args[i] == "--json":
            output_json = True
            i += 1
        else:
            i += 1
    return window, output_json


def load_records(window_minutes):
    """Load sentinel records within the time window."""
    if not os.path.exists(SENTINEL_LOG):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    records = []

    with open(SENTINEL_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                # Parse timestamp
                ts_str = rec.get("ts", "")
                if ts_str:
                    ts = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                    ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= cutoff:
                        records.append(rec)
            except (json.JSONDecodeError, ValueError):
                continue

    return records


def _tally(records):
    """Fold the record stream into counters: events, latency, sessions, tools.

    Also counts payload anomalies. A hook writes `field_missing` when the field
    it expects was absent from the payload, which means Claude Code changed the
    contract underneath it. Counting them here is what turns a quiet log line
    into a health signal somebody actually sees.
    """
    tally = {
        "events": Counter(),
        "latency": defaultdict(list),
        "sessions": set(),
        "tools": Counter(),
        "anomalies": Counter(),
    }

    for rec in records:
        event = rec.get("event", "unknown")
        tally["events"][event] += 1
        tally["latency"][event].append(rec.get("sentinel_ms", 0))
        tally["sessions"].add(rec.get("session", ""))

        ctx = rec.get("context", {})
        if ctx.get("tool"):
            tally["tools"][ctx["tool"]] += 1
        if ctx.get("field_missing"):
            tally["anomalies"][f"{event}.{ctx['field_missing']}"] += 1

    return tally


def analyse(records):
    """Analyse sentinel records for health metrics."""
    tally = _tally(records)
    event_counts = tally["events"]
    anomalies = tally["anomalies"]

    # Determine which expected events were seen
    missing = [e for e in EXPECTED_EVENTS if e not in set(event_counts)]
    # Some events are rare (Elicitation, ConfigChange) — separate them
    rare_events = {"Elicitation", "ElicitationResult", "ConfigChange", "Notification"}
    missing_critical = [e for e in missing if e not in rare_events]

    return {
        "total_records": len(records),
        "sessions": len(tally["sessions"]),
        "event_counts": dict(event_counts),
        "avg_latency_ms": {
            event: round(sum(lat) / len(lat), 1)
            for event, lat in tally["latency"].items()
        },
        "top_tools": dict(tally["tools"].most_common(10)),
        "missing_critical": missing_critical,
        "missing_rare": [e for e in missing if e in rare_events],
        "payload_anomalies": dict(anomalies),
        # A changed payload contract degrades telemetry exactly as a hook that
        # never fired does: records keep arriving, they just stop meaning what
        # they claim to mean.
        "health": "DEGRADED" if (missing_critical or anomalies) else "HEALTHY",
        "classification": load_classification(),
    }


def format_report(analysis, window):
    """Format human-readable health report."""
    lines = [
        f"GRIP Hook Health Report (last {window} min)",
        "=" * 45,
        f"Status: {analysis['health']}",
        f"Records: {analysis['total_records']} | Sessions: {analysis['sessions']}",
        "",
        "Event Firing Counts:",
    ]

    for event in EXPECTED_EVENTS:
        count = analysis["event_counts"].get(event, 0)
        latency = analysis["avg_latency_ms"].get(event, 0)
        marker = " " if count > 0 else "!"
        lines.append(f"  {marker} {event:<22} {count:>5}x  avg {latency:>5.1f}ms")

    if analysis["missing_critical"]:
        lines.append("")
        lines.append("MISSING (critical):")
        for e in analysis["missing_critical"]:
            lines.append(f"  ! {e}")

    if analysis.get("payload_anomalies"):
        lines.append("")
        lines.append("PAYLOAD ANOMALIES (Claude Code changed a field name):")
        for field, count in analysis["payload_anomalies"].items():
            lines.append(f"  ! {field} absent from {count} record(s)")

    if analysis["top_tools"]:
        lines.append("")
        lines.append("Top Tools:")
        for tool, count in list(analysis["top_tools"].items())[:5]:
            lines.append(f"  {tool:<30} {count:>5}x")

    cls = analysis.get("classification", {})
    if cls.get("hard") or cls.get("soft"):
        lines.append("")
        lines.append(f"Classification: {cls['hard']} hard gates, {cls['soft']} soft signals")

    return "\n".join(lines)


def main():
    window, output_json = parse_args()
    records = load_records(window)
    analysis = analyse(records)

    if output_json:
        print(json.dumps(analysis, indent=2))
    else:
        print(format_report(analysis, window))


if __name__ == "__main__":
    main()
