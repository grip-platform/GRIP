"""Detect telemetry fields that are recorded but always empty.

A hook that reads the wrong key from its payload does not fail. It writes a
row every time, exits zero, and the field it was supposed to capture is blank
in every single record. Nothing downstream errors — the data is simply absent,
and the dashboards keep looking healthy.

That happened for real: sentinel-hook.py read Claude Code's Notification event
as `type` when the payload calls it `notification_type`, and logged 2,241
consecutive records with nothing in them before anyone noticed. This module is
the mechanical check that would have caught it in the first hundred.

Pure and dependency-free so it can be unit-tested against fabricated records;
the I/O and the reporting live with the caller.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

# Below this many observations a field being empty proves nothing — plenty of
# events legitimately omit an optional field a handful of times. A field blank
# across this many records is a different claim entirely.
#
# Lowered from 50 after a review pointed out that a field emitted a few times a
# day would never reach 50 within a useful window, so a low-frequency defect
# could hide below the floor indefinitely. The risk of lowering it is noise, so
# it was measured rather than guessed: on 134,708 real records the floor makes
# no difference at all — every (event, field) pair in that telemetry has at
# least 1,788 samples, and sweeping the floor from 1 to 50 yields zero false
# positives at every value. 10 keeps a guard against a brand-new event whose
# first couple of rows happen to be blank, while catching a rare defect roughly
# five times sooner than 50 did.
DEFAULT_MIN_SAMPLES = 10


@dataclass(frozen=True)
class EmptyFieldFinding:
    """One (event, field) pair that has never once carried a value."""

    event: str
    field_name: str
    samples: int

    def describe(self) -> str:
        return (
            f"{self.event}.{self.field_name} empty in all {self.samples} records "
            f"— the signature of a payload key that does not exist"
        )


def _is_empty(value: object) -> bool:
    """Empty means absent, not falsy.

    `False` and `0` are real observations — a boolean flag that is usually
    False is working correctly, and treating it as missing would bury the
    signal this module exists to raise.
    """
    if value is None or value == "":
        return True
    return isinstance(value, (list, dict, tuple, set)) and len(value) == 0


def find_always_empty_fields(
    records: list[dict],
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> list[EmptyFieldFinding]:
    """Report every (event, context field) that is empty in every observation.

    Args:
        records: Telemetry rows, each with an "event" name and a "context"
            mapping. Rows missing either are skipped rather than raising —
            a health check must not fall over on a malformed line.
        min_samples: How many observations a field needs before its emptiness
            counts as evidence.

    Returns:
        Findings ordered by sample count, largest first, so the most
        thoroughly-evidenced problem is read first.
    """
    seen: dict[tuple[str, str], int] = defaultdict(int)
    filled: set[tuple[str, str]] = set()

    for record in records:
        event = record.get("event")
        context = record.get("context")
        if not isinstance(event, str) or not isinstance(context, dict):
            continue
        for key, value in context.items():
            seen[(event, key)] += 1
            if not _is_empty(value):
                filled.add((event, key))

    findings = [
        EmptyFieldFinding(event=event, field_name=key, samples=count)
        for (event, key), count in seen.items()
        if count >= min_samples and (event, key) not in filled
    ]
    findings.sort(key=lambda f: f.samples, reverse=True)
    return findings
