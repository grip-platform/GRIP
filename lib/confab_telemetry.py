"""Confabulation-contradiction telemetry — make the self-report failure rate measurable.

Background (plain language): the model sometimes states things about its own
behaviour — reasons, observations, "X does not exist" negatives — that an
external check later contradicts. Hypothesis H798 (learning/hypotheses.jsonl)
predicts this happens at a high rate ("external-contradiction-rate": >=40%
over the next 10 externally-checked, decision-gating self-reports).

This module is the measurement for that hypothesis:

- ``log_contradiction(record)`` appends one externally-checked self-report
  (contradicted or not) to ``learning/confab-contradictions.jsonl``.
- ``rate(window_days)`` computes {n, contradicted, rate} over the log.
- ``h798_snapshot()`` shapes those numbers for the hypothesis engine, reading
  H798's own ``metric`` field live from the hypotheses ledger so the two can
  never silently drift apart.

Pure library: no side effects at import, every path injectable, so tests run
against a temporary directory. CLI: ``python -m lib.confab_telemetry log|rate|h798``.

Feed the hypothesis engine like this::

    from lib.confab_telemetry import h798_snapshot
    from lib.hypothesis_engine import verify
    snap = h798_snapshot()
    verify("H798", measure_fn=lambda metric: snap["actual_value"])
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

__all__ = [
    "log_contradiction",
    "rate",
    "h798_snapshot",
    "default_log_path",
    "default_hypotheses_path",
    "main",
]

H798_ID = "H798"
# Verified verbatim against the live ledger entry for H798 on 2026-07-15
# (learning/hypotheses.jsonl: "metric":"external-contradiction-rate").
# h798_snapshot() still re-reads the ledger at call time; this constant is
# only the fallback when the ledger or the H798 row is unavailable.
H798_METRIC = "external-contradiction-rate"
# H798's prediction window is count-based: "over next 10 decision-gating
# self-reports subjected to external check, >=40% are contradicted".
H798_MIN_SAMPLE = 10

_REQUIRED_FIELDS = ("self_report", "external_check", "contradicted", "source")


def default_log_path() -> Path:
    """Where contradiction records land unless a caller injects a path."""
    return get_claude_dir() / "learning" / "confab-contradictions.jsonl"


def default_hypotheses_path() -> Path:
    """The live hypothesis ledger H798 is registered in."""
    return get_claude_dir() / "learning" / "hypotheses.jsonl"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validate(record: dict) -> None:
    """Reject a record a first-time reader could not interpret later."""
    missing = [k for k in _REQUIRED_FIELDS if k not in record]
    if missing:
        raise ValueError(
            "contradiction record is missing required field(s) "
            f"{missing} — needs all of {list(_REQUIRED_FIELDS)}"
        )
    if not isinstance(record["contradicted"], bool):
        raise ValueError(
            "'contradicted' must be a real true/false boolean, got "
            f"{type(record['contradicted']).__name__} — pass True when the "
            "external check refuted the self-report, False when it agreed"
        )


def _append_atomic(path: Path, line: str) -> None:
    """Append one line with a single O_APPEND write (atomic on POSIX).

    One write() call to an O_APPEND file descriptor cannot interleave with
    another process's write, so concurrent sessions never corrupt the log.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def log_contradiction(record: dict, path: Path | str | None = None) -> dict:
    """Append one externally-checked self-report to the contradiction log.

    Args:
        record: must carry ``self_report`` (what the model asserted),
            ``external_check`` (what the ground-truth test showed),
            ``contradicted`` (bool — did the check refute the self-report?),
            and ``source`` (where the check lives: command, file, PR, seal).
            Optional ``ts`` (ISO-8601) and ``session`` are honoured when
            supplied; otherwise filled from the clock and the harness
            session id (``CLAUDE_CODE_SESSION_ID``).
        path: log file override (tests inject a tmp path).

    Returns:
        The full entry exactly as written.
    """
    _validate(record)
    entry = {
        "ts": record.get("ts") or _utc_now().isoformat(),
        "session": record.get("session")
        or os.environ.get("CLAUDE_CODE_SESSION_ID", "unknown"),
        "self_report": record["self_report"],
        "external_check": record["external_check"],
        "contradicted": record["contradicted"],
        "source": record["source"],
    }
    target = Path(path) if path else default_log_path()
    _append_atomic(target, json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _read_entries(path: Path) -> list[dict]:
    """Parse the log fail-soft: a malformed line never breaks the metric."""
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            entries.append(row)
    return entries


def _in_window(entry: dict, cutoff: datetime) -> bool:
    """True when the entry's timestamp is provably inside the window.

    An entry with a missing or unparseable ``ts`` is excluded from windowed
    counts — we cannot prove it belongs, so it must not inflate the rate.
    """
    try:
        ts = datetime.fromisoformat(str(entry.get("ts", "")))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def rate(window_days: int | None = 30, path: Path | str | None = None) -> dict:
    """Contradiction rate over the log.

    Args:
        window_days: only count entries newer than this many days;
            ``None`` means all-time (H798's count-based window).
        path: log file override.

    Returns:
        ``{"n": total checked, "contradicted": how many were refuted,
        "rate": contradicted/n (0.0 when empty), "window_days": window}``.
    """
    entries = _read_entries(Path(path) if path else default_log_path())
    if window_days is not None:
        cutoff = _utc_now() - timedelta(days=window_days)
        entries = [e for e in entries if _in_window(e, cutoff)]
    n = len(entries)
    contradicted = sum(1 for e in entries if e.get("contradicted") is True)
    return {
        "n": n,
        "contradicted": contradicted,
        "rate": (contradicted / n) if n else 0.0,
        "window_days": window_days,
    }


def _load_h798(hypotheses_path: Path) -> dict | None:
    """Find H798's row in the hypothesis ledger, fail-soft on any damage."""
    for row in _read_entries(hypotheses_path):
        if row.get("id") == H798_ID:
            return row
    return None


def h798_snapshot(
    path: Path | str | None = None,
    hypotheses_path: Path | str | None = None,
) -> dict:
    """Shape the contradiction metric for hypothesis-engine H798 verification.

    Reads H798's own ``metric`` field live from the ledger so this module and
    the registered hypothesis cannot drift apart silently; falls back to the
    canonical constant (with ``hypothesis_found: False``) when the ledger or
    the row is missing. ``actual_value`` is the all-time contradiction rate —
    H798's prediction window is "the next 10 checked self-reports", a count,
    not a date range. ``sample_sufficient`` says whether that count is met.
    """
    stats = rate(window_days=None, path=path)
    ledger = Path(hypotheses_path) if hypotheses_path else default_hypotheses_path()
    row = _load_h798(ledger)
    return {
        "id": H798_ID,
        "metric": row.get("metric", H798_METRIC) if row else H798_METRIC,
        "actual_value": stats["rate"],
        "n": stats["n"],
        "contradicted": stats["contradicted"],
        "sample_sufficient": stats["n"] >= H798_MIN_SAMPLE,
        "prediction": row.get("prediction", "") if row else "",
        "deadline": row.get("deadline", "") if row else "",
        "hypothesis_found": row is not None,
    }


def _cmd_log(args: argparse.Namespace) -> int:
    record = {
        "self_report": args.self_report,
        "external_check": args.external_check,
        "contradicted": args.contradicted == "true",
        "source": args.source,
    }
    if args.session:
        record["session"] = args.session
    try:
        entry = log_contradiction(record, path=args.path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def _cmd_rate(args: argparse.Namespace) -> int:
    window = None if args.window_days in (None, "all") else int(args.window_days)
    print(json.dumps(rate(window_days=window, path=args.path)))
    return 0


def _cmd_h798(args: argparse.Namespace) -> int:
    snap = h798_snapshot(path=args.path, hypotheses_path=args.hypotheses_path)
    print(json.dumps(snap, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m lib.confab_telemetry",
        description="Log and measure externally-checked self-reports (H798).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    log_p = sub.add_parser("log", help="append one externally-checked self-report")
    log_p.add_argument("--self-report", required=True, dest="self_report",
                       help="what the model asserted about its own behaviour")
    log_p.add_argument("--external-check", required=True, dest="external_check",
                       help="what the external ground-truth check showed")
    log_p.add_argument("--contradicted", required=True, choices=("true", "false"),
                       help="did the external check refute the self-report?")
    log_p.add_argument("--source", required=True,
                       help="where the check lives (command, file, PR, council seal)")
    log_p.add_argument("--session", default="", help="session id override")
    log_p.add_argument("--path", default=None, help="log file override")
    log_p.set_defaults(func=_cmd_log)

    rate_p = sub.add_parser("rate", help="contradiction rate over the log")
    rate_p.add_argument("--window-days", default=30, dest="window_days",
                        help="day window, or 'all' for all-time (default 30)")
    rate_p.add_argument("--path", default=None, help="log file override")
    rate_p.set_defaults(func=_cmd_rate)

    h798_p = sub.add_parser("h798", help="H798-shaped snapshot for the hypothesis engine")
    h798_p.add_argument("--path", default=None, help="log file override")
    h798_p.add_argument("--hypotheses-path", default=None, dest="hypotheses_path",
                        help="hypotheses ledger override")
    h798_p.set_defaults(func=_cmd_h798)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
