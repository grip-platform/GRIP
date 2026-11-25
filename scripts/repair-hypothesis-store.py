#!/usr/bin/env python3
"""Repair GRIP's hypothesis store (learning/hypotheses.jsonl).

Concurrent sessions appended to an append-only JSONL while holding DIFFERENT
locks: lib/file_lock.py derives the lock path from the data file's own path
(``<data path>.lock``), so a worktree and the shared tree never contend. Both
computed the same "next" id and git unioned the results. Three defects follow,
repaired here in a fixed order:

  1. ID collisions   One id naming two genuinely different hypotheses. The
                     EARLIER-registered claim keeps the id; the later one is
                     reassigned a fresh id above the current maximum.
  2. Duplicate rows  Rows that are identical for the same id collapse to one.
                     Rows differing in ANY field are deliberately left alone --
                     choosing a winner between two different statuses is a
                     judgement call, not a mechanical repair.
  3. Field shift     Records whose values all sit one field out of place, so
                     ``claim`` holds a bare label, ``metric`` holds the claim,
                     and ``baseline`` holds the deadline.

Idempotent by construction: every action is conditional on its defect still
being present, so a second run over repaired output plans nothing. Rows that
need no change are written back VERBATIM, so untouched bytes stay untouched.

Usage:
    python3 scripts/repair-hypothesis-store.py --dry-run
    python3 scripts/repair-hypothesis-store.py --apply
    python3 scripts/repair-hypothesis-store.py --dry-run --store /path/to.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The store is written with compact separators and ASCII escaping. Matching it
# keeps re-serialised rows visually identical to their neighbours in the diff.
_SEPARATORS = (",", ":")

_ID_RE = re.compile(r"^H(\d+)$")
_BARE_LABEL_RE = re.compile(r"^H-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_DATE_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def canonical(rec: dict[str, Any]) -> str:
    """Order-insensitive content key, for spotting identical records."""
    return json.dumps(rec, sort_keys=True, separators=_SEPARATORS)


@dataclass
class Row:
    """One line of the store, keeping the raw text so it can be echoed back."""

    lineno: int
    raw: str
    rec: dict[str, Any]

    @property
    def hid(self) -> str:
        return str(self.rec.get("id", ""))

    @property
    def claim(self) -> str:
        return str(self.rec.get("claim", ""))

    @property
    def registered(self) -> str:
        return str(self.rec.get("registered", ""))

    def canonical(self) -> str:
        return canonical(self.rec)


@dataclass
class Plan:
    """Everything the run intends to do, computed before anything is written."""

    remap: dict[tuple[str, str], str] = field(default_factory=dict)
    splits: list[tuple[str, str, str]] = field(default_factory=list)
    dropped_lines: set[int] = field(default_factory=set)
    dropped: list[Row] = field(default_factory=list)
    shifted: set[str] = field(default_factory=set)
    conflicts: list[tuple[str, str, list[str]]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.splits or self.dropped or self.shifted)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def load_rows(path: Path) -> list[Row]:
    """Parse the store. A malformed line aborts the run rather than being skipped."""
    rows: list[Row] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno}: malformed JSON, refusing to continue: {exc}")
        rows.append(Row(lineno=lineno, raw=raw, rec=rec))
    return rows


def max_id_number(rows: list[Row]) -> int:
    """Highest numeric id in the store, so fresh ids can be allocated above it."""
    numbers = [int(m.group(1)) for r in rows if (m := _ID_RE.match(r.hid))]
    return max(numbers) if numbers else 0


def group_by_id(rows: list[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[row.hid].append(row)
    return grouped


# --------------------------------------------------------------------------
# Defect 3: field shift
# --------------------------------------------------------------------------
def is_field_shifted(rec: dict[str, Any]) -> bool:
    """True when every field of a record sits one position out of place.

    All five conditions must hold. Each on its own matches innocent records
    (``prediction`` is a bare identifier in 92 rows of the real store), so the
    conjunction is what makes the test discriminating.
    """
    deadline = str(rec.get("deadline", ""))
    registered = str(rec.get("registered", ""))
    pr_number = str(rec.get("pr_number", "0"))
    return all(
        (
            bool(_BARE_LABEL_RE.match(str(rec.get("claim", "")))),
            bool(deadline) and deadline == registered[:10],
            bool(_DATE_LIKE_RE.match(str(rec.get("baseline", "")))),
            bool(_IDENTIFIER_RE.match(str(rec.get("prediction", "")))),
            not pr_number.lstrip("-").isdigit(),
        )
    )


def repair_field_shift(rec: dict[str, Any]) -> dict[str, Any]:
    """Move each value back to the field it belongs in.

    The label that was occupying ``claim`` is kept as a ``LABEL: claim`` prefix,
    which is how the other labelled records in the store already carry it, so no
    information is dropped. ``pr_number`` and ``baseline`` return to the defaults
    every well-formed record uses; the values that were sitting in them move on
    to ``prediction`` and ``deadline`` respectively.
    """
    out = dict(rec)
    label = str(rec.get("claim", ""))
    out["claim"] = f"{label}: {rec.get('metric', '')}"
    out["metric"] = rec.get("prediction", "")
    out["prediction"] = rec.get("pr_number", "")
    out["deadline"] = str(rec.get("baseline", ""))[:10]
    out["baseline"] = 0.0
    out["pr_number"] = 0
    return out


# --------------------------------------------------------------------------
# Defect 1: id collisions
# --------------------------------------------------------------------------
def colliding_ids(rows: list[Row]) -> dict[str, list[str]]:
    """Ids that name more than one distinct claim, with claims oldest-first.

    Ordered by the earliest ``registered`` timestamp seen for each claim, which
    is what decides who keeps the id.
    """
    first_seen: dict[str, dict[str, str]] = defaultdict(dict)
    for row in rows:
        seen = first_seen[row.hid].get(row.claim)
        if seen is None or row.registered < seen:
            first_seen[row.hid][row.claim] = row.registered
    return {
        hid: sorted(claims, key=lambda c: (claims[c], c))
        for hid, claims in first_seen.items()
        if len(claims) > 1
    }


def displaced_claims(collisions: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Flatten to one (id, claim) per claim that is NOT the earliest under its id.

    Sorted, so allocation of fresh ids is deterministic for a given input.
    """
    pairs: list[tuple[str, str]] = []
    for hid in sorted(collisions):
        pairs.extend((hid, claim) for claim in collisions[hid][1:])
    return pairs


def plan_splits(rows: list[Row]) -> tuple[list[tuple[str, str, str]], dict[tuple[str, str], str]]:
    """Assign a fresh id, above the current maximum, to every displaced claim."""
    ceiling = max_id_number(rows)
    splits: list[tuple[str, str, str]] = []
    remap: dict[tuple[str, str], str] = {}
    for offset, (hid, claim) in enumerate(displaced_claims(colliding_ids(rows)), start=1):
        new_id = f"H{ceiling + offset:03d}"
        remap[(hid, claim)] = new_id
        splits.append((hid, new_id, claim))
    return splits, remap


# --------------------------------------------------------------------------
# Planning and rendering
# --------------------------------------------------------------------------
def transform(
    rec: dict[str, Any], remap: dict[tuple[str, str], str]
) -> tuple[dict[str, Any], bool, bool]:
    """Apply the id remap then the field-shift repair.

    Returns ``(record, changed, was_shifted)``.
    """
    out = dict(rec)
    changed = False
    new_id = remap.get((str(rec.get("id", "")), str(rec.get("claim", ""))))
    if new_id:
        out["id"] = new_id
        changed = True
    shifted = is_field_shifted(out)
    if shifted:
        out = repair_field_shift(out)
        changed = True
    return out, changed, shifted


def build_plan(rows: list[Row]) -> Plan:
    """Decide every change up front. Nothing here mutates the store."""
    splits, remap = plan_splits(rows)
    plan = Plan(remap=remap, splits=splits)

    seen: set[str] = set()
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        rec, _, shifted = transform(row.rec, remap)
        if shifted:
            plan.shifted.add(str(rec.get("id", "")))
        key = canonical(rec)
        if key in seen:
            plan.dropped.append(row)
            plan.dropped_lines.add(row.lineno)
            continue
        seen.add(key)
        grouped[(str(rec.get("id", "")), str(rec.get("claim", "")))].append(
            str(rec.get("status", ""))
        )

    ambiguous = [(k, v) for k, v in sorted(grouped.items()) if len(v) > 1]
    plan.conflicts = [(hid, claim, sorted(set(st))) for (hid, claim), st in ambiguous]
    return plan


def render(rows: list[Row], plan: Plan) -> list[str]:
    """Produce the repaired file as a list of lines.

    A row that needs no change is emitted as its ORIGINAL text, so the diff
    contains only rows that genuinely changed.
    """
    out: list[str] = []
    for row in rows:
        if row.lineno in plan.dropped_lines:
            continue
        rec, changed, _ = transform(row.rec, plan.remap)
        out.append(json.dumps(rec, separators=_SEPARATORS) if changed else row.raw)
    return out


# --------------------------------------------------------------------------
# Verification -- each check raises AssertionError on failure
# --------------------------------------------------------------------------
def _check_count(before: list[Row], after: list[Row]) -> str:
    was = {(r.hid, r.claim) for r in before}
    now = {(r.hid, r.claim) for r in after}
    assert len(now) == len(was), f"hypothesis count changed: {len(was)} -> {len(now)}"
    return f"distinct (id, claim) preserved: {len(was)}"


def _claim_metric_pairs(rows: list[Row], shifted: set[str]) -> set[tuple[str, str]]:
    return {
        (r.claim, str(r.rec.get("metric", ""))) for r in rows if r.hid not in shifted
    }


def _check_pairs(before: list[Row], after: list[Row], shifted: set[str]) -> str:
    was = _claim_metric_pairs(before, shifted)
    now = _claim_metric_pairs(after, shifted)
    lost, invented = was - now, now - was
    assert not lost, f"{len(lost)} (claim, metric) pairs lost, e.g. {sorted(lost)[:2]}"
    assert not invented, f"{len(invented)} (claim, metric) pairs invented, e.g. {sorted(invented)[:2]}"
    return f"(claim, metric) set identical for all {len(was)} unshifted hypotheses"


def _check_shifted(before: list[Row], after: list[Row], shifted: set[str]) -> str:
    """A repaired record must be a pure rearrangement of its own former values."""
    was = {r.hid: r.rec for r in before}
    checked = 0
    for row in after:
        if row.hid not in shifted:
            continue
        old, new = was[row.hid], row.rec
        assert new["claim"] == f"{old['claim']}: {old['metric']}", f"{row.hid}: claim not recovered"
        assert new["metric"] == old["prediction"], f"{row.hid}: metric not recovered"
        assert new["prediction"] == old["pr_number"], f"{row.hid}: prediction not recovered"
        assert new["deadline"] == str(old["baseline"])[:10], f"{row.hid}: deadline not recovered"
        checked += 1
    assert checked == len(shifted), f"expected {len(shifted)} shifted records, verified {checked}"
    return f"all {checked} field-shifted records provably rearranged, nothing dropped"


def _check_dropped(after: list[Row], plan: Plan) -> str:
    """Every removed row must still be present, identically, among the survivors."""
    kept = {r.canonical() for r in after}
    for row in plan.dropped:
        rec, _, _ = transform(row.rec, plan.remap)
        assert canonical(rec) in kept, (
            f"line {row.lineno} ({row.hid}) was dropped without an identical survivor"
        )
    return f"all {len(plan.dropped)} removed rows have an identical surviving twin"


def _check_ids(before: list[Row], after: list[Row]) -> str:
    was = {r.hid for r in before}
    now = {r.hid for r in after}
    assert was <= now, f"ids vanished: {sorted(was - now)}"
    return f"no id vanished ({len(was)} -> {len(now)} distinct ids)"


def verify(before: list[Row], after: list[Row], plan: Plan) -> list[str]:
    """Assert no hypothesis was lost, merged, or silently altered."""
    return [
        _check_count(before, after),
        _check_pairs(before, after, plan.shifted),
        _check_shifted(before, after, plan.shifted),
        _check_dropped(after, plan),
        _check_ids(before, after),
    ]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def _dedupe_summary(plan: Plan) -> list[str]:
    per_id: dict[str, int] = defaultdict(int)
    for row in plan.dropped:
        per_id[row.hid] += 1
    header = f"2. DEDUPE  {len(plan.dropped)} identical row(s) removed, across {len(per_id)} id(s)"
    if not per_id:
        return [header]
    shown = sorted(per_id.items())[:12]
    tail = " ..." if len(per_id) > 12 else ""
    return [header, "     " + ", ".join(f"{h} x{n}" for h, n in shown) + tail]


def describe(rows: list[Row], plan: Plan) -> str:
    grouped = group_by_id(rows)
    multi = sum(1 for v in grouped.values() if len(v) > 1)
    lines = [
        "Hypothesis store repair plan",
        "=" * 70,
        f"rows                 {len(rows)}",
        f"distinct ids         {len(grouped)}",
        f"ids on >1 row        {multi}",
        f"distinct (id, claim) {len({(r.hid, r.claim) for r in rows})}",
        "",
        f"1. SPLIT   {len(plan.splits)} colliding id(s) -- later claim gets a fresh id",
    ]
    lines += [f"     {old} -> {new}  {claim[:60]}" for old, new, claim in plan.splits]
    lines += [""] + _dedupe_summary(plan)
    lines += ["", f"3. UNSHIFT {len(plan.shifted)} field-shifted record(s)"]
    if plan.shifted:
        lines.append("     " + ", ".join(sorted(plan.shifted)))
    lines += ["", f"4. LEFT ALONE  {len(plan.conflicts)} ambiguous group(s) -- operator decision"]
    lines += [f"     {hid} statuses={st} {claim[:48]}" for hid, claim, st in plan.conflicts]
    return "\n".join(lines)


def write_atomically(path: Path, text: str) -> None:
    """Write via a temporary file in the same directory, then rename over the target.

    A direct overwrite leaves a truncated, unparseable store if the process dies
    mid-write -- which is the very failure this tool exists to repair. os.replace
    is atomic within a filesystem, so the store is either fully old or fully new,
    never half of each. The temporary file is created alongside the target so the
    rename cannot cross a filesystem boundary.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
    except BaseException:
        Path(tmp.name).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    default_store = Path(__file__).resolve().parent.parent / "learning" / "hypotheses.jsonl"
    parser.add_argument("--store", type=Path, default=default_store)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    mode.add_argument("--apply", action="store_true", help="write the repaired store")
    args = parser.parse_args(argv)

    if not args.store.exists():
        print(f"store not found: {args.store}", file=sys.stderr)
        return 2

    rows = load_rows(args.store)
    plan = build_plan(rows)
    print(describe(rows, plan))

    rendered = render(rows, plan)
    after = [Row(lineno=i, raw=line, rec=json.loads(line)) for i, line in enumerate(rendered, 1)]
    print("\nVerification")
    print("-" * 70)
    for check in verify(rows, after, plan):
        print(f"  ok  {check}")
    print(
        f"\nrows {len(rows)} -> {len(after)}"
        f"   distinct ids {len(group_by_id(rows))} -> {len(group_by_id(after))}"
    )

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    if plan.is_empty():
        print("\nstore is already repaired; nothing written.")
        return 0
    write_atomically(args.store, "\n".join(rendered) + "\n")
    print(f"\nwrote {args.store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
