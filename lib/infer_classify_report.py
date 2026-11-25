"""H798's numbers from the classifier's live log: ``python -m lib.infer_classify_report``.

Reads ``logs/infer-classify-decide.jsonl`` (lib/infer_classify_telemetry.py)
over a window, ``--since`` a number of days (7 by default) or an ISO date, and
prints numbers only:

- ``calls``: inference calls in the window. A cache hit makes no call.
- ``decide_share``: the share of those calls that Jev decided.
- ``paired`` and ``agreement``: how many Jev-decided calls have a labelled
  shadow reading from the chat ladder, and the share whose labels agree.
- ``median_ms_decide``, ``median_ms_model`` and ``median_ms_shadow``: the
  median latency of each rung on the call rows, and of the shadow's ladder.
- ``cost_reported_calls`` and ``mean_cost_usd``: the calls with at least one
  reported rung cost, and their mean cost in US dollars.

A number that cannot be computed prints ``n/a``, never a made-up zero.
``--json`` prints the same numbers as one JSON object, with null for ``n/a``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Iterable, Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import infer_classify_telemetry as telemetry  # noqa: E402


def since(value: str) -> datetime:
    """``--since``: a number of days back from now, or an ISO date or time (UTC if bare)."""
    try:
        return datetime.now(timezone.utc) - timedelta(days=float(value))
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _row(line: str) -> Optional[dict]:
    try:
        row = json.loads(line)
        at = datetime.fromisoformat(row["time"])
    except (ValueError, KeyError, TypeError):
        return None
    row["_at"] = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return row


def read_rows(path: Path, start: datetime) -> list:
    """The log's rows at or after ``start``. A missing log or a bad line is skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = (_row(line) for line in lines if line.strip())
    return [row for row in rows if row is not None and row["_at"] >= start]


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _latencies(rows: Iterable[dict], rung: str) -> list:
    found = (((row.get("rungs") or {}).get(rung) or {}).get("latency_ms") for row in rows)
    return [ms for ms in found if _number(ms)]


def _call_cost(row: dict) -> Optional[float]:
    details = (row.get("rungs") or {}).values()
    costs = [d.get("cost_usd") for d in details if isinstance(d, dict) and _number(d.get("cost_usd"))]
    return sum(costs) if costs else None


def _ratio(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


def _median(values: list) -> Optional[float]:
    return median(values) if values else None


def _pairs(decided: list, shadows: list) -> list:
    """(Jev's label, the shadow ladder's label) for each decided call with a labelled shadow."""
    labelled = {r.get("call_id"): r["label"] for r in shadows if r.get("label") is not None}
    return [(r.get("label"), labelled[r["call_id"]])
            for r in decided if r.get("call_id") in labelled]


def summarize(rows: Sequence[dict]) -> dict:
    """The report's numbers over ``rows``."""
    calls = [r for r in rows if r.get("kind") == "call"]
    shadows = [r for r in rows if r.get("kind") == "shadow"]
    decided = [r for r in calls if r.get("rung") == "decide"]
    pairs = _pairs(decided, shadows)
    costs = [c for c in map(_call_cost, calls) if c is not None]
    return {
        "calls": len(calls),
        "decide_share": _ratio(len(decided), len(calls)),
        "paired": len(pairs),
        "agreement": _ratio(sum(a == b for a, b in pairs), len(pairs)),
        "median_ms_decide": _median(_latencies(calls, "decide")),
        "median_ms_model": _median(_latencies(calls, "model")),
        "median_ms_shadow": _median(_latencies(shadows, "model")),
        "cost_reported_calls": len(costs),
        "mean_cost_usd": _ratio(sum(costs), len(costs)),
    }


def _text(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6g}" if isinstance(value, float) else str(value)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="H798's numbers from the classifier's log.")
    parser.add_argument("--since", default="7", type=since,
                        help="a number of days back (default 7), or an ISO date or time")
    parser.add_argument("--log", type=Path, default=None, help="another log file to read")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    numbers = summarize(read_rows(args.log or telemetry.log_path(), args.since))
    if args.json:
        print(json.dumps(numbers, sort_keys=True))
    else:
        print("\n".join(f"{name} {_text(value)}" for name, value in numbers.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
