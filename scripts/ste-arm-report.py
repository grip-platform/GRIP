#!/usr/bin/env python3
"""scripts/ste-arm-report.py -- the verdict of the STE 2 by 2 trial, from the hook's log
and the pre-registration, and nothing else.

The trial (plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, stages S2 and S3) gives each
session two arm bits from its id (lib/ste_hook.py, arms_of). Factor A is an STE
instruction at session start; factor B is the score line handed to the model with
the next prompt. The Stop hook writes one row per turn with the arms, the structural
pass rate and the hidden counters (lib/ste_counters.py). This script reads those rows
and the pre-registration file (docs/measurements/2026-10-14-ste-trial-prereg.json)
and gives one verdict per factor:

  REFUSED       a lower-is-worse counter of the ON arm fell below the OFF arm by
                more than that counter's own noise floor: the arm bought its score
                with dropped articles, facts, reasons, hedges or prose
  CONFIRMED     no counter is worse, and the rise in the session-mean pass rate
                beats both the registered minimum and the noise floor
  INCONCLUSIVE  fewer sessions than registered, or a rise that beats neither

The unit is the session, not the turn: turns of one session are not independent
(intraclass correlation 0.46 to 0.60, measured 2026-10-10). The noise floor is the
95th percentile of the gap between the means of two random halves of the sessions,
over the registered number of splits with the registered seed, so the same log and
the same file always give the same verdict. Every number that decides a verdict is
printed. No turn text exists in the log, so none can appear here.

Usage:
    bin/grip-py scripts/ste-arm-report.py --prereg docs/measurements/2026-10-14-ste-trial-prereg.json
    bin/grip-py scripts/ste-arm-report.py --prereg <file> --log <jsonl> --json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))

from claude_dir import get_claude_dir  # noqa: E402

REFUSED, CONFIRMED, INCONCLUSIVE = "REFUSED", "CONFIRMED", "INCONCLUSIVE"


def _ts(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _row(line: str) -> dict | None:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def log_files(log_path: Path) -> list[Path]:
    """The active log and every rotated file beside it. lib/ste_hook.py starts a new
    file at 8 MB and keeps the old one as ``<stem>.<date>.jsonl`` for 90 days, so a
    trial that spans a rotation has rows in both; reading only the active file would
    drop sessions silently (a Codex finding on GRIP#7926). Oldest rotated file first."""
    rotated = sorted(log_path.parent.glob(f"{log_path.stem}.*{log_path.suffix}")) if log_path.parent.is_dir() else []
    return [*rotated, log_path]


def _lines_of(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def load_rows(log_path: Path) -> list[dict]:
    lines = [line for path in log_files(log_path) for line in _lines_of(path)]
    return [row for row in map(_row, lines) if row is not None]


def _in_window(row: dict, start: float | None, look: float | None) -> bool:
    ts = _ts(row.get("ts"))
    if ts is None:
        return False
    return (start is None or ts >= start) and (look is None or ts <= look)


def eligible(row: dict, prereg: dict, start: float | None, look: float | None) -> bool:
    """A trial row of the registered scorer and counters, a safe session, enough
    sentences, inside the window, and in a GRIP checkout when the file says so."""
    return (row.get("trial") is True
            and row.get("scorer_version") == prereg["scorer_version"]
            and row.get("counters_version") == prereg["counters_version"]
            and bool(row.get("session"))
            and isinstance(row.get("counters"), dict)
            and int(row.get("sentences") or 0) >= int(prereg["min_sentences"])
            and (not prereg.get("in_grip_checkout_only") or row.get("in_grip_checkout") is True)
            and _in_window(row, start, look))


def _session_record(session: str, turns: list[dict], counters: list[str]) -> dict:
    sentences = sum(int(t.get("sentences") or 0) for t in turns)
    semicolons = sum(int((t.get("per_rule") or {}).get("semicolon") or 0) for t in turns)
    return {
        "session": session,
        "arm_a": bool(turns[0].get("arm_a")),
        "arm_b": bool(turns[0].get("arm_b")),
        "turns": len(turns),
        "rate": statistics.fmean(float(t["structural_pass_rate"]) for t in turns),
        "counters": {name: statistics.fmean(float(t["counters"].get(name) or 0.0) for t in turns)
                     for name in counters},
        "semicolons_per_100": 100.0 * semicolons / sentences if sentences else 0.0,
    }


def session_means(rows: list[dict], counters: list[str]) -> list[dict]:
    """One record per session: its arms, its mean pass rate, the mean of each
    counter, and its semicolons per 100 sentences."""
    by_session: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_session[row["session"]].append(row)
    return [_session_record(session, turns, counters) for session, turns in sorted(by_session.items())]


def _half_gap(values: list[float], half: int, rng: random.Random) -> float:
    shuffled = values[:]
    rng.shuffle(shuffled)
    return abs(statistics.fmean(shuffled[:half]) - statistics.fmean(shuffled[half:2 * half]))


def floor_of(values: list[float], splits: int, rng: random.Random) -> float:
    """The 95th percentile of the absolute gap between the means of two random
    halves of ``values``: the gap chance alone makes between two arms of this size."""
    if len(values) < 4:
        return float("inf")
    half = len(values) // 2
    gaps = sorted(_half_gap(values, half, rng) for _ in range(splits))
    return gaps[min(len(gaps) - 1, int(round(0.95 * (len(gaps) - 1))))]


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _counter_check(name: str, means: list[dict], on: list[dict], off: list[dict], prereg: dict,
                   rng: random.Random) -> dict:
    on_mean, off_mean = _mean([m["counters"][name] for m in on]), _mean([m["counters"][name] for m in off])
    floor = floor_of([m["counters"][name] for m in means], int(prereg["floor"]["splits"]), rng)
    delta = None if on_mean is None or off_mean is None else on_mean - off_mean
    return {"counter": name, "on": on_mean, "off": off_mean, "delta": delta, "floor": floor,
            "worse": delta is not None and delta < -floor}


def factor_verdict(name: str, means: list[dict], prereg: dict) -> dict:
    """The verdict of one factor, with every number behind it."""
    spec = prereg["factors"][name]
    field = spec["arm_field"]
    on = [m for m in means if m[field]]
    off = [m for m in means if not m[field]]
    rng = random.Random(int(prereg["floor"]["seed"]))
    on_rate, off_rate = _mean([m["rate"] for m in on]), _mean([m["rate"] for m in off])
    rise = None if on_rate is None or off_rate is None else on_rate - off_rate
    floor = floor_of([m["rate"] for m in means], int(prereg["floor"]["splits"]), rng)
    checks = [_counter_check(counter, means, on, off, prereg, rng) for counter in prereg["counters_lower_is_worse"]]
    result = {"factor": name, "what": spec.get("what"), "sessions": len(means), "on_sessions": len(on),
              "off_sessions": len(off), "on_rate": on_rate, "off_rate": off_rate, "rise": rise,
              "floor": floor, "min_rise": spec["min_rise"], "counters": checks,
              "semicolons_per_100_on": _mean([m["semicolons_per_100"] for m in on]),
              "semicolons_per_100_max": spec.get("semicolons_per_100_sentences_max")}
    result["verdict"], result["because"] = _decide(result, int(prereg["min_sessions"]))
    return result


def _decide(result: dict, min_sessions: int) -> tuple[str, str]:
    if result["sessions"] < min_sessions:
        return INCONCLUSIVE, f"{result['sessions']} sessions; the registration needs {min_sessions}"
    if not result["on_sessions"] or not result["off_sessions"]:
        return INCONCLUSIVE, "one arm has no session"
    worse = [c["counter"] for c in result["counters"] if c["worse"]]
    if worse:
        return REFUSED, "the ON arm is worse on " + ", ".join(worse) + " by more than its floor"
    needed = max(float(result["min_rise"]), result["floor"])
    if result["rise"] <= needed:
        return INCONCLUSIVE, f"a rise of {result['rise']:.4f} does not beat {needed:.4f}"
    cap = result["semicolons_per_100_max"]
    if cap is not None and result["semicolons_per_100_on"] > float(cap):
        return INCONCLUSIVE, f"{result['semicolons_per_100_on']:.2f} semicolons per 100 sentences exceed {cap}"
    return CONFIRMED, f"a rise of {result['rise']:.4f} beats {needed:.4f} with no counter worse"


def _extended_look(prereg: dict) -> float | None:
    """The look plus the one registered extension, or None when the file names none."""
    look, days = _ts(prereg.get("look")), prereg.get("extension_days")
    return None if look is None or not days else look + float(days) * 86400.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report(prereg: dict, rows: list[dict]) -> dict:
    """The registered read. With fewer than min_sessions inside [start, look], the one
    registered extension is used: the window grows by extension_days once, and the
    output says so. Until GRIP#7989 the report read the fixed look only, so the
    extension the registration promised could never add a session (a Codex finding)."""
    start, look = _ts(prereg.get("start")), _ts(prereg.get("look"))
    counters = list(prereg["counters_lower_is_worse"])
    kept = [row for row in rows if eligible(row, prereg, start, look)]
    means = session_means(kept, counters)
    longer, extended = _extended_look(prereg), False
    if longer is not None and len(means) < int(prereg["min_sessions"]):
        kept = [row for row in rows if eligible(row, prereg, start, longer)]
        means, extended = session_means(kept, counters), True
    window = {"start": prereg.get("start"), "look": prereg.get("look"), "extended": extended,
              "look_used": _iso(longer) if extended else prereg.get("look")}
    return {"prereg": prereg.get("name"), "rows_read": len(rows), "rows_eligible": len(kept),
            "sessions": len(means), "window": window,
            "factors": [factor_verdict(name, means, prereg) for name in sorted(prereg["factors"])]}


def _fmt(value) -> str:
    return "n/a" if value is None else (f"{value:.4f}" if isinstance(value, float) else str(value))


def _print_counter(c: dict) -> None:
    flag = "WORSE" if c["worse"] else "ok"
    print(f"  {c['counter']:<20} on {_fmt(c['on'])}  off {_fmt(c['off'])}  delta {_fmt(c['delta'])}  "
          f"floor {_fmt(c['floor'])}  {flag}")


def _print_factor(f: dict) -> None:
    print(f"factor {f['factor']} ({f['what']}): {f['verdict']} -- {f['because']}")
    print(f"  sessions on/off {f['on_sessions']}/{f['off_sessions']}  rate on {_fmt(f['on_rate'])} "
          f"off {_fmt(f['off_rate'])}  rise {_fmt(f['rise'])}  floor {_fmt(f['floor'])}  min {f['min_rise']}")
    for c in f["counters"]:
        _print_counter(c)
    if f["semicolons_per_100_max"] is not None:
        print(f"  semicolons per 100 sentences, on arm: {_fmt(f['semicolons_per_100_on'])} "
              f"(max {f['semicolons_per_100_max']})")


def _print_text(out: dict) -> None:
    w = out["window"]
    note = f" (the one extension used, to {w['look_used']})" if w.get("extended") else ""
    print(f"STE trial {out['prereg']}: {out['rows_eligible']} eligible rows of {out['rows_read']} read, "
          f"{out['sessions']} sessions, window {w['start']} .. {w['look']}{note}")
    for f in out["factors"]:
        _print_factor(f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--prereg", required=True, help="the pre-registration JSON file")
    ap.add_argument("--log", help="the hook's JSONL log (default: <claude dir>/logs/ste-lint-hook.jsonl)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    try:
        prereg = json.loads(Path(args.prereg).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read the pre-registration: {exc}", file=sys.stderr)
        return 2
    log_path = Path(args.log) if args.log else Path(get_claude_dir()) / "logs" / "ste-lint-hook.jsonl"
    out = report(prereg, load_rows(log_path))
    out["measured_at"] = datetime.now(timezone.utc).isoformat()
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        _print_text(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
