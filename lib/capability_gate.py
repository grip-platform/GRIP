#!/usr/bin/env python3
"""Retain/discard on a measured capability score — the loop's closing element.

GRIP has generated change for years and kept almost all of it. That is a
RATCHET, not recursive self-improvement: an RSI machine needs (1) a capability
metric, (2) a generator, (3) an exogenous verifier, (4) **a retain/discard
decision driven by the metric**, and (5) measured recursion. Element 4 had been
built three separate times and none of the three ever ran, so nothing GRIP
produced was ever discarded on evidence.

This compares the judgement benchmark's current score against the last
COMPARABLE run and reports a verdict. It decides nothing by itself and blocks
nothing: it returns a verdict object, and arming it is a separate, measured
step (see ``arming_blocked_reason``).

Three refusals are the substance here, because each is a way a comparison can
look successful while meaning nothing:

* **An absent baseline is never a pass.** "I could not compare this" and "I
  compared this and it improved" are different answers and stay different.
  Tonight an external verifier exited 1 for a missing anchor, and 1 was its
  code for "forged" — an unverifiable state laundered into a verdict. Here it
  is ``INSUFFICIENT_BASELINE``, which is not a regression and not a pass.
* **Runs scored under different conditions are not comparable.** The first ten
  rows in the live corpus were scored before rule-suppression was enforced and
  before the vote key was fixed, so the respondent could see the rules it was
  being tested on and every margin was structurally zero. Those rows score
  10/10 and mean nothing. Comparing against them would manufacture a
  catastrophic regression out of a methodology fix.
* **Only items present in BOTH runs are compared.** The corpus grows, and a
  changed denominator moves the pass rate on its own. A delta computed over a
  different item set measures the corpus, not GRIP.

Metric, from the 2026-09-13 measurement: pass rate is usable again (13/19
overall) but coarse, while vote margin spans the full -4..+4 and separates
cohorts that pass rate does not — rule-derived items mean +1.56, memory-derived
+2.60. Both are reported; margin is the sensitive one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: A regression must clear BOTH a pass-rate drop and a margin drop before it is
#: called one. Either alone is inside the noise a 19-item pilot can produce.
#: These are starting values, not measured thresholds — see arming_blocked_reason.
PASS_RATE_DROP = 0.10
MARGIN_DROP = 0.50

#: Below this many shared items a delta is arithmetic, not evidence.
MIN_SHARED_ITEMS = 8

VERDICTS = ("IMPROVED", "STEADY", "REGRESSION", "INSUFFICIENT_BASELINE")


@dataclass(frozen=True)
class Verdict:
    """A comparison outcome. ``verdict`` is one of VERDICTS."""

    verdict: str
    reason: str
    shared_items: int = 0
    current_sha: str = ""
    baseline_sha: str = ""
    current_pass_rate: float = 0.0
    baseline_pass_rate: float = 0.0
    current_mean_margin: float = 0.0
    baseline_mean_margin: float = 0.0
    per_kind: dict = field(default_factory=dict)

    @property
    def is_regression(self) -> bool:
        return self.verdict == "REGRESSION"

    @property
    def comparable(self) -> bool:
        return self.verdict != "INSUFFICIENT_BASELINE"


def load_rows(path: "str | Path") -> list:
    """Every result row, malformed lines skipped rather than raising."""
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(row, dict) and row.get("item_id") and row.get("grip_sha"):
            rows.append(row)
    return rows


def _conditions(row: dict) -> tuple:
    """The scoring conditions a row was produced under.

    Two runs are comparable only when these match. ``margin is None`` is part
    of the identity on purpose: a run that recorded no margin was scored by a
    different mechanism, whatever else it shares.
    """
    return (row.get("respondent") or "", row.get("margin") is None)


def _runs(rows: list, split: str) -> list:
    """Rows grouped into runs of (sha, conditions), newest last.

    Deduplicated on (sha, item_id): re-running an unchanged tree is idempotent
    and must not double-count.
    """
    groups: dict = {}
    for row in rows:
        if split != "all" and row.get("split") != split:
            continue
        key = (row.get("grip_sha"), _conditions(row))
        groups.setdefault(key, {})[row["item_id"]] = row
    return [(sha, cond, items) for (sha, cond), items in groups.items()]


def _rate(rows: list) -> float:
    return sum(1 for r in rows if r.get("pass")) / len(rows) if rows else 0.0


def _mean_margin(rows: list) -> float:
    vals = [r["margin"] for r in rows if r.get("margin") is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _by_kind(cur: list, base: list) -> dict:
    """Per-kind deltas, so a cohort can regress inside a steady total."""
    out = {}
    for kind in sorted({r.get("kind") for r in cur if r.get("kind")}):
        c = [r for r in cur if r.get("kind") == kind]
        b = [r for r in base if r.get("kind") == kind]
        if not c or not b:
            continue
        out[kind] = {
            "current_pass_rate": _rate(c),
            "baseline_pass_rate": _rate(b),
            "current_mean_margin": _mean_margin(c),
            "baseline_mean_margin": _mean_margin(b),
            "shared": len(c),
        }
    return out


def _pick_baseline(runs: list, current: tuple) -> "tuple | None":
    """The most recent EARLIER run scored under the current conditions."""
    sha, cond, _ = current
    candidates = [r for r in runs if r[1] == cond and r[0] != sha]
    return candidates[-1] if candidates else None


def compare(results_path: "str | Path", *, split: str = "dev") -> Verdict:
    """Compare the newest run against the last comparable one."""
    runs = _runs(load_rows(results_path), split)
    if not runs:
        return Verdict("INSUFFICIENT_BASELINE", "no results recorded")
    current = runs[-1]
    baseline = _pick_baseline(runs, current)
    if baseline is None:
        return Verdict(
            "INSUFFICIENT_BASELINE",
            "no earlier run scored under the same conditions "
            f"(respondent + margin recording); {len(runs)} run(s) present",
            current_sha=current[0] or "",
        )
    return _compare_runs(current, baseline)


def _compare_runs(current: tuple, baseline: tuple) -> Verdict:
    """Score two comparable runs over the items they share."""
    cur_sha, _, cur_items = current
    base_sha, _, base_items = baseline
    shared = sorted(set(cur_items) & set(base_items))
    if len(shared) < MIN_SHARED_ITEMS:
        return Verdict(
            "INSUFFICIENT_BASELINE",
            f"only {len(shared)} shared item(s); {MIN_SHARED_ITEMS} needed "
            "before a delta is evidence rather than arithmetic",
            shared_items=len(shared),
            current_sha=cur_sha or "", baseline_sha=base_sha or "",
        )
    cur = [cur_items[i] for i in shared]
    base = [base_items[i] for i in shared]
    cur_rate, base_rate = _rate(cur), _rate(base)
    cur_margin, base_margin = _mean_margin(cur), _mean_margin(base)
    regressed = (base_rate - cur_rate) >= PASS_RATE_DROP and \
                (base_margin - cur_margin) >= MARGIN_DROP
    improved = cur_rate > base_rate or cur_margin > base_margin
    verdict = "REGRESSION" if regressed else ("IMPROVED" if improved else "STEADY")
    return Verdict(
        verdict,
        f"{len(shared)} shared items: pass {base_rate:.0%} -> {cur_rate:.0%}, "
        f"mean margin {base_margin:+.2f} -> {cur_margin:+.2f}",
        shared_items=len(shared),
        current_sha=cur_sha or "", baseline_sha=base_sha or "",
        current_pass_rate=cur_rate, baseline_pass_rate=base_rate,
        current_mean_margin=cur_margin, baseline_mean_margin=base_margin,
        per_kind=_by_kind(cur, base),
    )


def arming_blocked_reason(results_path: "str | Path", *,
                          split: str = "dev") -> "str | None":
    """Why this gate may not be promoted to DENY yet, or None when it may.

    The criterion is mechanical rather than a later judgement call, because
    "we'll decide when it feels ready" is how a gate arms on an unmeasured
    false-positive rate. knowledge-maturation-functor.md wants a measured rate
    under 10% first, and that needs comparable runs to measure it ON.

    It returns a REASON rather than a boolean so the caller can print why,
    which is the difference between a gate that refuses and one that sulks.
    """
    runs = _runs(load_rows(results_path), split)
    comparable = [r for r in runs if not _conditions(r[0] if False else next(iter(r[2].values())))[1]]
    if len(comparable) < 3:
        return (f"only {len(comparable)} comparable run(s) recorded; a measured "
                "false-positive rate needs at least 3 before DENY is defensible")
    return None
