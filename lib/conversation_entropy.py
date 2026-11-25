#!/usr/bin/env python3
"""Conversation Entropy — information-theoretic session quality signal.

Novel contribution: applying Shannon entropy to tool usage patterns as a
proxy for session diversity and cognitive engagement.

Theory:
- High entropy = diverse tool usage, varied topics, exploration
- Low entropy = repetitive patterns, stuck in loops, single-tool grinding
- Optimal entropy is NOT maximum — it follows an inverted-U curve:
  Too low = stuck. Too high = chaotic. Peak = productive exploration.

The inverted-U prediction is FALSIFIABLE: if entropy doesn't correlate
with session quality (DELIGHT composite), this signal is noise.

Signals computed:
- tool_entropy: Shannon entropy of tool usage distribution
- event_entropy: Shannon entropy of hook event distribution
- temporal_entropy: how evenly distributed are actions over time
- composite_entropy: weighted combination normalised to [0, 1]

Usage:
    python3 lib/conversation_entropy.py [--window MINUTES] [--json]
"""

import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
SENTINEL_LOG = CLAUDE_DIR / "cache" / "sentinel.jsonl"


def shannon_entropy(counts: dict) -> float:
    """Compute Shannon entropy (bits) from a frequency distribution.

    H = -sum(p_i * log2(p_i)) for all i where p_i > 0.
    Returns 0.0 for empty or single-element distributions.
    """
    total = sum(counts.values())
    if total == 0 or len(counts) <= 1:
        return 0.0

    entropy = 0.0
    for count in counts.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)

    return entropy


def normalised_entropy(counts: dict) -> float:
    """Entropy normalised to [0, 1] by dividing by max possible entropy.

    Max entropy = log2(N) where N is the number of unique categories.
    """
    n = len([c for c in counts.values() if c > 0])
    if n <= 1:
        return 0.0
    max_entropy = math.log2(n)
    if max_entropy == 0:
        return 0.0
    return shannon_entropy(counts) / max_entropy


def temporal_entropy(timestamps: list, n_bins: int = 10) -> float:
    """Compute temporal entropy: how evenly distributed are actions over time.

    Divides the time window into n_bins equal segments and measures
    the entropy of the count distribution across bins.

    Returns normalised entropy [0, 1]:
    - 1.0 = perfectly uniform activity (no bursts/gaps)
    - 0.0 = all activity in a single burst
    """
    if len(timestamps) < 2:
        return 0.0

    sorted_ts = sorted(timestamps)
    t_min = sorted_ts[0]
    t_max = sorted_ts[-1]
    span = (t_max - t_min).total_seconds()

    if span == 0:
        return 0.0

    bin_size = span / n_bins
    bin_counts = Counter()
    for ts in sorted_ts:
        bin_idx = min(int((ts - t_min).total_seconds() / bin_size), n_bins - 1)
        bin_counts[bin_idx] += 1

    return normalised_entropy(dict(bin_counts))


def inverted_u_score(entropy_value: float, peak: float = 0.7) -> float:
    """Apply inverted-U curve: peak entropy is optimal, extremes are bad.

    Uses a Gaussian-like curve centred at `peak` with sigma=0.3.
    Returns [0, 1] where 1.0 = optimal entropy.

    The peak value (0.7) is a hypothesis:
    "Sessions at 70% normalised entropy are most productive."
    This is falsifiable by correlating with DELIGHT composite.
    """
    sigma = 0.3
    return math.exp(-((entropy_value - peak) ** 2) / (2 * sigma ** 2))


def compute_session_entropy(window_minutes: int = 120) -> dict:
    """Compute all entropy signals from sentinel data.

    Returns:
        tool_entropy: normalised entropy of tool usage
        event_entropy: normalised entropy of event types
        temporal_entropy: how evenly spread actions are in time
        composite: inverted-U score of combined entropy
        raw_tool_entropy_bits: raw Shannon entropy in bits
        unique_tools: number of unique tools used
        total_events: total sentinel records in window
    """
    if not SENTINEL_LOG.exists():
        return _empty_result()

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    tool_counts = Counter()
    event_counts = Counter()
    timestamps = []

    try:
        for line in SENTINEL_LOG.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("ts", "")[:19]
                ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue

                timestamps.append(ts)
                event = rec.get("event", "unknown")
                event_counts[event] += 1

                ctx = rec.get("context", {})
                tool = ctx.get("tool", "")
                if tool:
                    tool_counts[tool] += 1
            except (json.JSONDecodeError, ValueError):
                continue
    except (OSError, UnicodeDecodeError):
        return _empty_result()

    if not timestamps:
        return _empty_result()

    t_ent = normalised_entropy(dict(tool_counts))
    e_ent = normalised_entropy(dict(event_counts))
    temp_ent = temporal_entropy(timestamps)

    # Weighted combination (tools matter most for diversity)
    combined = t_ent * 0.5 + e_ent * 0.2 + temp_ent * 0.3
    iu_score = inverted_u_score(combined)

    return {
        "tool_entropy": round(t_ent, 4),
        "event_entropy": round(e_ent, 4),
        "temporal_entropy": round(temp_ent, 4),
        "combined_entropy": round(combined, 4),
        "inverted_u_score": round(iu_score, 4),
        "raw_tool_entropy_bits": round(shannon_entropy(dict(tool_counts)), 3),
        "unique_tools": len(tool_counts),
        "total_events": len(timestamps),
        "top_tools": dict(tool_counts.most_common(5)),
        "hypothesis": {
            "claim": "Sessions at ~70% normalised entropy are most productive",
            "falsified_by": "No correlation between inverted_u_score and DELIGHT composite",
            "peak_assumption": 0.7,
        },
    }


def _empty_result() -> dict:
    return {
        "tool_entropy": 0.0,
        "event_entropy": 0.0,
        "temporal_entropy": 0.0,
        "combined_entropy": 0.0,
        "inverted_u_score": 0.0,
        "raw_tool_entropy_bits": 0.0,
        "unique_tools": 0,
        "total_events": 0,
        "top_tools": {},
        "hypothesis": {
            "claim": "Sessions at ~70% normalised entropy are most productive",
            "falsified_by": "No correlation between inverted_u_score and DELIGHT composite",
            "peak_assumption": 0.7,
        },
    }


def main():
    window = 120
    output_json = False
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--window" and i + 1 < len(sys.argv):
            window = int(sys.argv[i + 1])
        elif arg == "--json":
            output_json = True

    result = compute_session_entropy(window)

    if output_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Conversation Entropy (last {window} min)")
        print("=" * 40)
        print(f"  Tool entropy:     {result['tool_entropy']:.3f} ({result['unique_tools']} unique)")
        print(f"  Event entropy:    {result['event_entropy']:.3f}")
        print(f"  Temporal entropy: {result['temporal_entropy']:.3f}")
        print(f"  Combined:         {result['combined_entropy']:.3f}")
        print(f"  Inverted-U score: {result['inverted_u_score']:.3f} (peak at 0.70)")
        print(f"  Total events:     {result['total_events']}")
        if result["top_tools"]:
            print(f"  Top tools:        {result['top_tools']}")


if __name__ == "__main__":
    main()
