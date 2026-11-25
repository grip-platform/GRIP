#!/usr/bin/env python3
"""Compute a mutation score from mutmut 3.x's machine-readable CI/CD stats.

WHY THIS EXISTS
---------------
The previous CI step regexed mutmut's console output for ``Killed N`` /
``Survived N``. mutmut 3.x never emits those strings — its summary line is
emoji-formatted (``mutmut/stats.py::print_stats``)::

    {checked}/{total}  🎉 {killed} 🫥 {no_tests}  ⏰ {timeout}  🤔 {suspicious} ...

so both regexes missed, ``k`` and ``s`` stayed 0, and the score was forced to
0% on every run. Parsing decorative console output is what caused that bug, so
this module reads the JSON that ``mutmut export-cicd-stats`` writes instead.

THE SCORE FORMULA IS VENDOR-CANONICAL
-------------------------------------
It is copied from mutmut's own ``badge`` command so this gate and a mutmut
badge can never disagree (mutmut 3.8.0, ``mutmut/__main__.py``)::

    tested = int(stats.get("total", 0)) - int(stats.get("skipped", 0))
    score = 0.0 if tested <= 0 else (
        (int(stats.get("killed", 0)) + int(stats.get("timeout", 0))) / tested
    ) * 100

A timeout counts as a kill: the mutant changed behaviour enough to hang the
test, which is the test noticing. ``skipped`` is removed from the denominator
rather than counted as survived, because a skipped mutant was never tested —
counting it either way would be a claim the run did not measure.

EXIT CODES — a crash must never look like a low score
-----------------------------------------------------
``0`` score met the threshold; ``1`` score below threshold (a real, measured
verdict); ``2`` the stats file is missing or unreadable, meaning mutmut did not
produce a measurement at all. The old workflow collapsed 2 into 1 and reported
"0%", which read as "the tests are terrible" when it actually meant "mutmut
never ran". Those are opposite conclusions and the caller must be able to tell
them apart.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

DEFAULT_STATS_PATH = Path("mutants/mutmut-cicd-stats.json")

EXIT_OK = 0
EXIT_BELOW_THRESHOLD = 1
EXIT_NO_MEASUREMENT = 2


class NoMeasurement(Exception):
    """mutmut produced no usable stats — distinct from a poor score."""


def _as_int(stats: Mapping[str, Any], key: str) -> int:
    """Read one counter, treating a missing key as 0 but a junk value as fatal.

    A missing key is normal: mutmut omits counters it never incremented. A
    present-but-non-numeric value means the file is not the schema we think it
    is, and silently coercing it to 0 would manufacture a score out of garbage.
    """
    value = stats.get(key, 0)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise NoMeasurement(f"stats key {key!r} is not a number: {value!r}") from exc


def score_from_stats(stats: Mapping[str, Any]) -> float:
    """Return the mutation score percentage for a mutmut cicd-stats mapping."""
    tested = _as_int(stats, "total") - _as_int(stats, "skipped")
    if tested <= 0:
        return 0.0
    caught = _as_int(stats, "killed") + _as_int(stats, "timeout")
    return (caught / tested) * 100


def load_stats(path: Path = DEFAULT_STATS_PATH) -> dict[str, Any]:
    """Load mutmut's cicd-stats JSON, raising NoMeasurement if it is unusable."""
    if not path.exists():
        raise NoMeasurement(
            f"{path} not found — mutmut did not produce stats. "
            "Check that 'mutmut run' succeeded and 'mutmut export-cicd-stats' ran."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NoMeasurement(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise NoMeasurement(f"{path} does not contain a JSON object")
    return data


def summarise(stats: Mapping[str, Any], threshold: float) -> tuple[float, str, str]:
    """Return (score, status, one-line human summary)."""
    score = score_from_stats(stats)
    total = _as_int(stats, "total")
    skipped = _as_int(stats, "skipped")
    tested = total - skipped
    status = "PASS" if score >= threshold else "FAIL"
    line = (
        f"Mutation score: {score:.1f}% ({status}, threshold {threshold:g}%) — "
        f"killed={_as_int(stats, 'killed')} timeout={_as_int(stats, 'timeout')} "
        f"survived={_as_int(stats, 'survived')} no_tests={_as_int(stats, 'no_tests')} "
        f"suspicious={_as_int(stats, 'suspicious')} skipped={skipped} "
        f"tested={tested} total={total}"
    )
    return score, status, line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--stats", type=Path, default=DEFAULT_STATS_PATH)
    parser.add_argument("--threshold", type=float, default=60.0)
    parser.add_argument(
        "--github-output",
        type=Path,
        default=None,
        help="Append score=/status= lines here (GitHub Actions $GITHUB_OUTPUT).",
    )
    args = parser.parse_args(argv)

    try:
        stats = load_stats(args.stats)
        score, status, line = summarise(stats, args.threshold)
    except NoMeasurement as exc:
        # Loud and distinct: this is "no measurement", never "score 0".
        print(f"NO MEASUREMENT: {exc}", file=sys.stderr)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as handle:
                handle.write("score=\nstatus=NO_MEASUREMENT\n")
        return EXIT_NO_MEASUREMENT

    print(line)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"score={score:.1f}\nstatus={status}\n")
    return EXIT_OK if status == "PASS" else EXIT_BELOW_THRESHOLD


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
