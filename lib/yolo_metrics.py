"""Parse ~/.claude/logs/yolo-mode.log and aggregate rolling-window usage.

The log is a tab-separated audit trail written by lib/gates/yolo_mode.py::
_log_yolo. Each line has the shape::

    <iso_ts>\\t<event>\\t<subject>\\t<detail>

where ``event`` is one of ACTIVATE / DEACTIVATE / BYPASS / EXPIRED / SWEPT,
``subject`` is the activator for ACTIVATE/DEACTIVATE/EXPIRED and the GATE
name for BYPASS, and ``detail`` carries event-specific fields. For ACTIVATE
lines, the TTL is embedded in detail as ``ttl=<seconds>s``.

This parser is dependency-free (stdlib only) so /evo can render it without
importing anything from the wider GRIP runtime.
"""
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

_LOG_PATH = os.path.expanduser("~/.claude/logs/yolo-mode.log")
_ALERTING_THRESHOLD_PER_HOUR = 50.0
_TTL_PATTERN = re.compile(r"ttl=(\d+)s")


@dataclass
class YoloMetrics:
    """Aggregated usage over a rolling window.

    ``total_ttl_granted_s`` is the sum of TTLs across ACTIVATE events in the
    window — not wall-clock time spent in yolo mode. "Active hours" for the
    alerting ratio is derived from granted TTL, so the ratio answers
    "bypass fires per allocated autonomous hour".
    """
    activations: int = 0
    bypass_fires: int = 0
    total_ttl_granted_s: float = 0.0
    activator_counts: dict = field(default_factory=dict)
    bypass_gate_counts: dict = field(default_factory=dict)

    @property
    def bypass_per_activation(self) -> float:
        return (self.bypass_fires / self.activations) if self.activations else 0.0

    @property
    def avg_ttl_granted_s(self) -> float:
        return (self.total_ttl_granted_s / self.activations) if self.activations else 0.0

    @property
    def bypass_per_active_hour(self) -> float:
        hours = self.total_ttl_granted_s / 3600.0
        return (self.bypass_fires / hours) if hours > 0 else 0.0

    @property
    def alerting(self) -> bool:
        return self.bypass_per_active_hour > _ALERTING_THRESHOLD_PER_HOUR


def _parse_line(line: str) -> Optional[dict]:
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 3:
        return None
    try:
        ts = datetime.fromisoformat(parts[0])
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    detail = parts[3] if len(parts) >= 4 else ""
    return {"ts": ts, "event": parts[1], "subject": parts[2], "detail": detail}


def _extract_ttl_seconds(detail: str) -> float:
    m = _TTL_PATTERN.search(detail)
    return float(m.group(1)) if m else 0.0


def _iter_events(log_path: str, cutoff: datetime) -> Iterator[dict]:
    if not os.path.isfile(log_path):
        return
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            event = _parse_line(line)
            if event and event["ts"] >= cutoff:
                yield event


_EVENT_HANDLERS = {}


def _handle_activate(metrics: "YoloMetrics", event: dict) -> None:
    metrics.activations += 1
    metrics.total_ttl_granted_s += _extract_ttl_seconds(event["detail"])
    key = event["subject"] or "?"
    metrics.activator_counts[key] = metrics.activator_counts.get(key, 0) + 1


def _handle_bypass(metrics: "YoloMetrics", event: dict) -> None:
    metrics.bypass_fires += 1
    gate = event["subject"] or "?"
    metrics.bypass_gate_counts[gate] = metrics.bypass_gate_counts.get(gate, 0) + 1


_EVENT_HANDLERS["ACTIVATE"] = _handle_activate
_EVENT_HANDLERS["BYPASS"] = _handle_bypass


def load(window_days: int = 7, log_path: str = _LOG_PATH,
         now: Optional[datetime] = None) -> YoloMetrics:
    """Load metrics across a rolling window.

    ``now`` is injectable so tests can pin the cutoff to the fixture's
    synthetic timestamps; production callers leave it as ``None``.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=window_days)
    metrics = YoloMetrics()
    for event in _iter_events(log_path, cutoff):
        handler = _EVENT_HANDLERS.get(event["event"])
        if handler:
            handler(metrics, event)
    return metrics


def _format_activator_lines(counts: dict, label: str) -> list:
    if not counts:
        return []
    lines = [f"  {label}:"]
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {name}: {count}")
    return lines


def render_evo_tile(metrics: YoloMetrics) -> str:
    lines = [
        "Yolo Usage (7d)",
        f"  Activations: {metrics.activations}",
        f"  Bypass fires: {metrics.bypass_fires} "
        f"({metrics.bypass_per_activation:.1f}/activation)",
        f"  Avg TTL granted: {metrics.avg_ttl_granted_s / 60:.1f} min",
        f"  Per active hour: {metrics.bypass_per_active_hour:.1f}",
    ]
    lines.extend(_format_activator_lines(metrics.activator_counts, "By activator"))
    lines.extend(_format_activator_lines(metrics.bypass_gate_counts, "By gate"))
    if metrics.alerting:
        lines.append("  ALERT: bypass-per-hour above threshold — investigate")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_evo_tile(load()))
