#!/usr/bin/env python3
"""Jev's calibration on GRIP's own PRs -- the Jev plan's Phase 5.1, scoring half.

Reads ``state/jev-calibration.jsonl`` (written by ``lib.jev_verdict_predictor``),
joins each prediction with its latest settlement, and reports a Brier score per
question against a base-rate comparison (always predicting the observed MERGE
rate), plus reliability bins. The vendor publishes no calibration evidence for
Jev; this is the measurement GRIP runs on its own merge decisions instead.

Two questions, two denominators. "merge" settles for every prediction whose head
has ANY genuine AGORA verdict -- MERGE counts as 1, and DEFER CHANGES,
REQUEST_CHANGES, NO_QUORUM, JUDGE_UNAVAILABLE and any other token all count as
0 ("Count DEFER CHANGES as not MERGE" -- it is not a near-miss, it is simply not
the predicted outcome). "ci_pass" settles only for predictions whose required
checks were actually readable for that exact head (see
``lib.jev_verdict_predictor``'s module docstring for why that denominator is
narrower), so its ``n`` is reported honestly rather than padded with guesses.

Pure scoring + a thin CLI. No network, no gh, no HAL -- this only ever reads the
log ``lib.jev_verdict_predictor`` already wrote.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: (p_key on the prediction row, outcome_key on the settlement row, label).
QUESTIONS = (("p_merge", "actual_merge", "Will AGORA's verdict be MERGE?"),
            ("p_ci_pass", "actual_ci_pass", "Will every required check pass on this commit's first run?"))
N_BINS = 5

JoinedRow = Tuple[dict, Optional[dict]]


@dataclass(frozen=True)
class ReliabilityBin:
    lo: float
    hi: float
    n: int
    mean_predicted: Optional[float]
    observed_frequency: Optional[float]


@dataclass(frozen=True)
class QuestionScore:
    label: str
    n: int
    brier: Optional[float]
    base_rate: Optional[float]
    base_rate_brier: Optional[float]
    bins: Tuple[ReliabilityBin, ...] = field(default_factory=tuple)


def join_rows(rows: List[dict]) -> List[JoinedRow]:
    """Every prediction, in file order, paired with its LATEST settlement (or
    None). Duplicate prediction rows for the same (repo, pr, head) keep only
    the first -- a corrupted or doubly-written log cannot double-count a PR."""
    key = lambda r: (r.get("repo"), r.get("pr"), r.get("head_sha"))  # noqa: E731
    predictions: Dict[tuple, dict] = {}
    order: List[tuple] = []
    settlements: Dict[tuple, dict] = {}
    for row in rows:
        k = key(row)
        if row.get("type") == "prediction":
            if k not in predictions:
                predictions[k] = row
                order.append(k)
        elif row.get("type") == "settlement":
            settlements[k] = row  # later rows supersede -- see _settle's union logic
    return [(predictions[k], settlements.get(k)) for k in order]


def first_n_settled(joined: List[JoinedRow], n: Optional[int]) -> List[JoinedRow]:
    """The first ``n`` predictions that have ANY settlement, ordered by when
    they settled -- "the first 50 settled PRs" from the pre-registered
    hypothesis. ``n=None`` returns every settled row."""
    settled = [(p, s) for p, s in joined if s is not None]
    settled.sort(key=lambda ps: ps[1].get("ts") or "")
    return settled if n is None else settled[:n]


def _points(joined: List[JoinedRow], p_key: str, outcome_key: str) -> List[Tuple[float, float]]:
    pts = []
    for pred, settle in joined:
        if settle is None:
            continue
        actual = settle.get(outcome_key)
        p = pred.get(p_key)
        if actual is None or not isinstance(p, (int, float)):
            continue
        pts.append((float(p), float(bool(actual))))
    return pts


def _bin_index(p: float, n_bins: int) -> int:
    return min(int(p * n_bins), n_bins - 1)


def _reliability_bins(pts: List[Tuple[float, float]], n_bins: int) -> Tuple[ReliabilityBin, ...]:
    buckets: List[List[Tuple[float, float]]] = [[] for _ in range(n_bins)]
    for p, a in pts:
        buckets[_bin_index(p, n_bins)].append((p, a))
    out = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not bucket:
            out.append(ReliabilityBin(lo, hi, 0, None, None))
            continue
        mean_p = sum(p for p, _ in bucket) / len(bucket)
        freq = sum(a for _, a in bucket) / len(bucket)
        out.append(ReliabilityBin(lo, hi, len(bucket), mean_p, freq))
    return tuple(out)


def score_question(joined: List[JoinedRow], p_key: str, outcome_key: str, label: str,
                   *, n_bins: int = N_BINS) -> QuestionScore:
    """Jev's Brier score for one question, the base-rate Brier for comparison,
    and reliability bins. ``n=0`` (nothing settled yet) reports Nones, never a
    division by zero."""
    pts = _points(joined, p_key, outcome_key)
    if not pts:
        return QuestionScore(label, 0, None, None, None, ())
    n = len(pts)
    brier = sum((p - a) ** 2 for p, a in pts) / n
    base_rate = sum(a for _, a in pts) / n
    base_rate_brier = sum((base_rate - a) ** 2 for _, a in pts) / n
    return QuestionScore(label, n, brier, base_rate, base_rate_brier,
                         _reliability_bins(pts, n_bins))


def build_report(joined: List[JoinedRow], *, n_bins: int = N_BINS) -> Tuple[QuestionScore, ...]:
    return tuple(score_question(joined, p_key, outcome_key, label, n_bins=n_bins)
                for p_key, outcome_key, label in QUESTIONS)


#: H812's paired bootstrap (docs/jev-shadows.md), fixed so that anyone can rerun it.
BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED = 20000, 20261017


def _lead(pts: List[Tuple[float, float]]) -> float:
    """The base-rate Brier minus Jev's, with the base rate taken from ``pts``:
    positive when Jev forecasts better than always predicting that rate."""
    base_rate = sum(a for _, a in pts) / len(pts)
    return sum((base_rate - a) ** 2 - (p - a) ** 2 for p, a in pts) / len(pts)


def _groups_of(pts: List[Tuple[float, float]], clusters: Optional[list]) -> list:
    """The resampling units: each point alone, or the points sharing a cluster key."""
    if clusters is None:
        return [[pt] for pt in pts]
    groups: Dict[object, list] = {}
    for pt, key in zip(pts, clusters):
        groups.setdefault(key, []).append(pt)
    return list(groups.values())


def _resample(groups: list, rng) -> List[Tuple[float, float]]:
    from itertools import chain

    return list(chain.from_iterable(groups[i] for i in rng.choices(range(len(groups)), k=len(groups))))


def paired_bootstrap(pts: List[Tuple[float, float]], *, clusters: Optional[list] = None,
                     samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED) -> dict:
    """Jev's lead over the base rate, resampled with the base rate recomputed in each
    sample: the point lead, the 95% percentile interval, and the share of samples in
    which Jev is ahead. With ``clusters`` (one key per point), whole clusters are
    resampled together: a PR's several heads share one change and one review, so they
    are not independent (Codex on GRIP#8516). The same input and seed always give the
    same answer."""
    import random

    if not pts:
        return {"n": 0, "clusters": 0, "lead": None, "low": None, "high": None, "share_ahead": None}
    groups = _groups_of(pts, clusters)
    rng = random.Random(seed)
    leads = sorted(_lead(_resample(groups, rng)) for _ in range(samples))
    return {"n": len(pts), "clusters": len(groups), "lead": _lead(pts),
            "low": leads[int(0.025 * samples)], "high": leads[int(0.975 * samples) - 1],
            "share_ahead": sum(1 for x in leads if x > 0) / samples}


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def _fmt(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.4f}"


def _render_bins(bins: Tuple[ReliabilityBin, ...]) -> List[str]:
    out = []
    for b in bins:
        if b.n == 0:
            out.append(f"    [{b.lo:.1f}, {b.hi:.1f}): n=0")
        else:
            out.append(f"    [{b.lo:.1f}, {b.hi:.1f}): n={b.n}  "
                       f"mean_p={_fmt(b.mean_predicted)}  observed={_fmt(b.observed_frequency)}")
    return out


def _render_question(s: QuestionScore) -> List[str]:
    if s.n == 0:
        return [f"## {s.label}", "  n=0 -- nothing settled yet for this question.", ""]
    beats = "beats" if (s.brier is not None and s.base_rate_brier is not None
                        and s.brier < s.base_rate_brier) else "does not beat"
    return [
        f"## {s.label}",
        f"  n={s.n}  Jev Brier={_fmt(s.brier)}  "
        f"base-rate Brier={_fmt(s.base_rate_brier)} (base rate={_fmt(s.base_rate)})",
        f"  Jev {beats} the base rate (lower Brier is better).",
        "  reliability bins (predicted range -> n, mean predicted, observed frequency):",
        *_render_bins(s.bins),
        "",
    ]


def render_text(scores: Tuple[QuestionScore, ...], *, n_total: int, n_predicted_from: int) -> str:
    lines = [
        f"Jev verdict-prediction calibration -- {n_total} prediction(s) in the log, "
        f"{n_predicted_from} scored below.",
        "DEFER CHANGES, REQUEST_CHANGES, NO_QUORUM, JUDGE_UNAVAILABLE and any other "
        "verdict all count as NOT MERGE for the merge question -- none of them is a near-miss.",
        "",
    ]
    for s in scores:
        lines.extend(_render_question(s))
    return "\n".join(lines).rstrip() + "\n"


def report_to_dict(scores: Tuple[QuestionScore, ...]) -> dict:
    return {"questions": [
        {"label": s.label, "n": s.n, "brier": s.brier, "base_rate": s.base_rate,
         "base_rate_brier": s.base_rate_brier,
         "bins": [{"lo": b.lo, "hi": b.hi, "n": b.n, "mean_predicted": b.mean_predicted,
                   "observed_frequency": b.observed_frequency} for b in (s.bins or ())]}
        for s in scores
    ]}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _scored(rows: list) -> Tuple[list, list]:
    """The (prediction, outcome) pairs of the rows that hold both, and each pair's
    (repo, pr): the unit the bootstrap resamples."""
    def has_p(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    kept = [r for r in rows if has_p(r.get("p_merge")) and r.get("actual_merge") is not None]
    return ([(float(r["p_merge"]), float(bool(r["actual_merge"]))) for r in kept],
            [(r.get("repo"), r.get("pr")) for r in kept])


def _bootstrap_cmd(ns) -> dict:
    """The merge question's bootstrap over whole PRs, from a committed points file
    (``--points``, window ``--set``) or else from the log (window ``--first``)."""
    if ns.points:
        if ns.first is not None:
            raise SystemExit("--first applies to the log; choose a points file's window with --set")
        rows = json.loads(ns.points.read_text(encoding="utf-8"))[ns.set]
    else:
        from lib.jev_verdict_predictor import read_calibration_rows, calibration_path

        joined = first_n_settled(join_rows(read_calibration_rows(ns.path or calibration_path())), ns.first)
        rows = [{"repo": p.get("repo"), "pr": p.get("pr"), "p_merge": p.get("p_merge"),
                 "actual_merge": s.get("actual_merge")} for p, s in joined]
    pts, clusters = _scored(rows)
    return paired_bootstrap(pts, clusters=clusters)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    from lib.jev_verdict_predictor import read_calibration_rows, calibration_path

    ap = argparse.ArgumentParser(description="Jev verdict-prediction calibration report")
    ap.add_argument("cmd", choices=["report", "bootstrap"])
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--first", type=int, default=None,
                    help="only the first N settled predictions, by settlement order")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--points", type=Path, default=None,
                    help="bootstrap: read the pairs from this committed file instead of the log")
    ap.add_argument("--set", default="registered",
                    help="bootstrap: which list in --points to read (registered or all_settled)")
    ns = ap.parse_args(argv)
    if ns.cmd == "bootstrap":
        print(json.dumps(_bootstrap_cmd(ns), indent=2, sort_keys=True))
        return 0

    rows = read_calibration_rows(ns.path or calibration_path())
    joined = join_rows(rows)
    scored = first_n_settled(joined, ns.first)
    scores = build_report(scored)
    if ns.json:
        print(json.dumps(report_to_dict(scores), indent=2, sort_keys=True))
    else:
        print(render_text(scores, n_total=len(joined), n_predicted_from=len(scored)), end="")
    return 0


if __name__ == "__main__":
    from lib.pq_reexec import reexec_if_incapable
    reexec_if_incapable(Path(__file__))
    sys.exit(main())
