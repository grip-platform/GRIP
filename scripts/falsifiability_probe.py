#!/usr/bin/env python3
"""scripts/falsifiability_probe.py -- the Phase 5.2 settling probe of Jev's
falsifiability Score, against the hypothesis registry itself.

lib/hypothesis_falsifiability.py shows Jev's falsifiability reading at CLI
registration (never gating it). Before anyone trusts the card, this script asks
whether the five-level scale is degenerate -- everything landing on one level --
by reading it back, retrospectively, over the most recently RESOLVED hypotheses
already in learning/hypotheses.jsonl: confirmed, refuted, inconclusive or expired.
"Resolved" rather than strictly "settled", because the report below gives the
settled-vs-ended-other split per level, which needs both kinds in the sample.

What this does NOT show: every hypothesis in the sample was worded by someone who
already knew lib.hypothesis_engine._reject_unmeasurable rejects an unfalsifiable
claim at registration, so this is not a neutral test of the scale against
arbitrary prose -- it is a test against GRIP's own, already-filtered population.
And the settlement outcome is retrospective: the card did not exist when any of
these were registered, so a level/settlement correlation found here is not
evidence that a FORWARD reading predicts settlement. That forward question is
H833's job, measured going forward from this PR.

Each hypothesis's own claim/metric/prediction/measure plan is run through the
privacy floor (lib.decision_privacy.may_send, via lib.hypothesis_falsifiability._floor)
INDIVIDUALLY before being included; a fragment the floor refuses is left out of the
batch entirely and the drop is recorded, never silently sent anyway. The survivors
are batched into as few Jev calls as the token limits allow: one shared state (each
hypothesis under its own "Hypothesis <id>:" heading) and one Score question per
hypothesis, reusing the exact level texts and reading logic production uses
(lib.hypothesis_falsifiability.LEVELS / ._read), so this measures the real card
rather than an approximation of it.

The live run goes through HAL and reaches TypeSafe, at real cost (well under a cent
for 60 items; measured at roughly 16k input tokens for the full registry). It is
skipped, never faked, when Jev cannot be reached -- the report says so and the
caller must not invent numbers.

Usage:
    bin/grip-py scripts/falsifiability_probe.py
    bin/grip-py scripts/falsifiability_probe.py --limit 60 --out <report.json>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lib.hypothesis_engine import _load_hypotheses  # noqa: E402
from lib.hypothesis_falsifiability import (  # noqa: E402
    LEVELS, _ENDED_OTHER, _SETTLED, _default_decide, _floor, _read, _state_text,
)


def _git_state() -> tuple:
    """``(revision, dirty)``; ``("", True)`` when git cannot answer -- unknown is
    reported as dirty, never as clean."""
    try:
        rev = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        status = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=10)
        if rev.returncode != 0 or status.returncode != 0:
            return "", True
        return rev.stdout.strip(), bool(status.stdout.strip())
    except Exception:  # noqa: BLE001 -- a probe report, not a gate
        return "", True


def select_recently_resolved(hypotheses: list, limit: int) -> list:
    """The ``limit`` most recently resolved (non-pending) hypotheses, newest first."""
    resolved = [h for h in hypotheses if h.status != "pending"]
    resolved.sort(key=lambda h: (h.verification_date or h.registered or ""), reverse=True)
    return resolved[:limit]


def build_batch(hypotheses: list) -> tuple:
    """``(state, questions, included_ids, dropped)``. A fragment the privacy floor
    refuses is left out of the batch and named in ``dropped``, never sent anyway."""
    blocks, questions, included, dropped = [], {}, [], []
    for h in hypotheses:
        fragment = _state_text(h.claim, h.metric, h.prediction, h.measure_plan)
        allowed, why = _floor(fragment, None)
        if not allowed:
            dropped.append({"id": h.id, "why": why})
            continue
        blocks.append(f"Hypothesis {h.id}:\n{fragment}")
        questions[h.id] = {
            "type": "score",
            "instructions": f"For Hypothesis {h.id} above, how easily could a "
                            f"named measurement prove it wrong?",
            "criteria": list(LEVELS),
        }
        included.append(h.id)
    return "\n\n".join(blocks), questions, included, dropped


def _empty_report(limit: int, selected: int, included: list, dropped: list) -> dict:
    revision, dirty = _git_state()
    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": revision, "git_dirty": dirty,
        "limit": limit, "selected": selected,
        "included": len(included), "dropped": dropped,
        "model": "", "available": False,
        "levels": list(LEVELS),
        "distribution": {}, "settled_vs_ended_other_per_level": {}, "readings": [],
    }


def run(limit: int = 60, decide=_default_decide) -> dict:
    """Score the most recently resolved ``limit`` hypotheses in one batched call.
    Never fabricates a reading: an unreachable Jev yields ``available: False``."""
    hypotheses = select_recently_resolved(_load_hypotheses(), limit)
    by_id = {h.id: h for h in hypotheses}
    state, questions, included, dropped = build_batch(hypotheses)
    report = _empty_report(limit, len(hypotheses), included, dropped)
    if not included:
        return report
    result = decide(state, questions)
    if not isinstance(result, dict) or not result.get("ok"):
        return report

    report["available"] = True
    report["model"] = str(result.get("model") or "")
    answers = result.get("answers") or {}
    level_counts: Counter = Counter()
    split = {level: {"settled": 0, "ended_other": 0, "other_status": 0} for level in LEVELS}
    for hid in included:
        h, read = by_id[hid], _read((answers.get(hid)))
        entry = {"id": hid, "status": h.status, "read": read is not None}
        if read is not None:
            level, value, confidence, _probs = read
            entry.update(level=level, value=value, confidence=confidence)
            level_counts[level] += 1
            bucket = ("settled" if h.status in _SETTLED else
                     "ended_other" if h.status in _ENDED_OTHER else "other_status")
            split[level][bucket] += 1
        report["readings"].append(entry)
    report["distribution"] = {level: level_counts.get(level, 0) for level in LEVELS}
    report["settled_vs_ended_other_per_level"] = split
    return report


def main(argv: Optional[list] = None, *, decide=_default_decide) -> int:
    """``decide`` is a parameter, not read from the module default inside ``run()``,
    so a caller (a test) can inject a stand-in without the no-op that monkeypatching
    ``_default_decide`` would otherwise be -- ``run``'s own default argument value is
    bound once, at module load, to the ORIGINAL function object."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    report = run(args.limit, decide=decide)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    if args.json or not args.out:
        print(json.dumps(report, indent=2))
    if not report["available"]:
        print("Jev was unavailable; nothing was measured.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
