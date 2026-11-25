"""Congruent Continuity — the honest precision measurement (build-order D3).

``docs/congruent-continuity-genesis.md:81`` (D3) says the pre-hoc watcher
ships WARN-only until its precision is MEASURED on a post-hoc corpus, and
``rules/congruent-continuity.md`` falsifies the whole primitive if that
precision "proves no better than the self-assessed-confidence baseline". This
module computes the number and the interval around it, and it refuses to
compute one from rows that cannot fail:

- **Only paired rows count** (``partition``): a (feature, label) row from
  ``lib/continuity_pairing.py``. A label with no vector — the whole
  hypothesis ledger, every git proxy whose creating transcript is not on this
  machine — is BASE-RATE evidence and is reported as such, separately.
- **Cross-validated flag precision** (``flag_precision``): stratified k-fold,
  seeded, one model at a time from ``lib/continuity_trio.py``. Precision is
  ``tp / (tp + fp)`` over the rows the model FLAGGED as wrong-track
  (label 1); an abstention is neither. Fewer than two rows of either class
  makes the measurement NOT ESTABLISHABLE, and the result says so instead of
  printing a number.
- **A Wilson interval, never a bare point** (``wilson``): at small n the
  interval is wide by construction, and stating it wide is the finding.
- **The bar** (``clears_bar``): a predictor beats the only baseline this
  corpus can supply — the paired base rate, i.e. the precision of flagging
  everything — when the interval's LOWER bound exceeds it. The
  self-assessed-confidence baseline the rule names cannot be measured from
  this corpus: no commitment record carries the agent's confidence at the
  time of the claim, so that comparison is reported UNMEASURED, not
  approximated.

Zero LLM calls, zero network, no wall clock: same rows and seed, same table.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from typing import Callable, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.continuity_trio import CFIOF, BernoulliNB, LogisticSGD  # noqa: E402

__all__ = [
    "MODELS",
    "Z95",
    "wilson",
    "base_rate",
    "partition",
    "stratified_folds",
    "flag_precision",
    "clears_bar",
    "measure",
]

MODELS: dict[str, Callable[[], object]] = {
    "cfiof": CFIOF,
    "naive_bayes": BernoulliNB,
    "logreg": LogisticSGD,
}
Z95 = 1.959964
MIN_PER_CLASS = 2


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for ``k`` successes in ``n`` trials. ``n == 0``
    yields the uninformative ``(0.0, 1.0)`` rather than a division error."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, (centre - half) / denom), min(1.0, (centre + half) / denom))


def base_rate(labels: Sequence[int]) -> dict:
    """Refutation rate with its Wilson interval — the prior, and the precision
    of the trivial predictor that flags every row."""
    n = len(labels)
    k = sum(1 for lab in labels if lab == 1)
    return {"n": n, "positives": k, "rate": (k / n) if n else None, "ci95": wilson(k, n)}


def partition(label_rows: Sequence[dict], paired: Sequence[object]) -> dict:
    """Split the label corpus into PAIRED (has a vector) and LABEL-ONLY.

    ``paired`` rows carry ``source`` and ``ref``; a label row is paired iff a
    paired row with the same ``(source, ref)`` exists. The hypothesis ledger
    can never be paired (a hypothesis carries no transcript) and is counted
    under ``label_only`` by construction — this function has no code path
    that moves a ``hypothesis`` row into the paired count.
    """
    paired_keys = {(getattr(p, "source", None), getattr(p, "ref", None)) for p in paired}
    out = {"paired": 0, "label_only": 0, "by_source": {}}
    for row in label_rows:
        source = row["source"]
        bucket = out["by_source"].setdefault(source, {"paired": 0, "label_only": 0})
        if source != "hypothesis" and (source, row["ref"]) in paired_keys:
            bucket["paired"] += 1
            out["paired"] += 1
        else:
            bucket["label_only"] += 1
            out["label_only"] += 1
    return out


def stratified_folds(labels: Sequence[int], k: int, seed: int) -> list[list[int]]:
    """``k`` folds, each holding a seeded shuffle of every class distributed
    round-robin, so every fold sees both classes when the corpus allows it."""
    rng = random.Random(seed)
    folds: list[list[int]] = [[] for _ in range(k)]
    for cls in (0, 1):
        idx = [i for i, lab in enumerate(labels) if lab == cls]
        rng.shuffle(idx)
        for pos, i in enumerate(idx):
            folds[pos % k].append(i)
    return folds


def flag_precision(
    rows: Sequence[dict[str, float]],
    labels: Sequence[int],
    model_factory: Callable[[], object],
    *,
    k: int = 5,
    seed: int = 0,
) -> dict:
    """Cross-validated precision of the model's WRONG-TRACK flags.

    Returns ``establishable=False`` with the reason when either class has
    fewer than ``MIN_PER_CLASS`` rows: a fold cannot then hold out a positive
    while still training on one, so any number printed would be an artefact
    of the split, not of the predictor."""
    n = len(rows)
    positives = sum(1 for lab in labels if lab == 1)
    negatives = n - positives
    if positives < MIN_PER_CLASS or negatives < MIN_PER_CLASS:
        return {
            "establishable": False,
            "reason": (f"need at least {MIN_PER_CLASS} rows of each class to cross-validate; "
                       f"have {positives} wrong-track and {negatives} held"),
            "n": n, "positives": positives, "negatives": negatives,
        }
    folds_k = max(2, min(k, positives, negatives))
    folds = stratified_folds(labels, folds_k, seed)
    tp = fp = fn = tn = abstain = skipped = 0
    for fold in folds:
        held = set(fold)
        train_rows = [rows[i] for i in range(n) if i not in held]
        train_labels = [labels[i] for i in range(n) if i not in held]
        if len(set(train_labels)) < 2:
            skipped += 1
            continue
        model = model_factory().fit(train_rows, train_labels)
        for i in fold:
            predicted = model.predict(rows[i])["label"]
            if predicted is None:
                abstain += 1
            elif predicted == 1:
                tp += labels[i] == 1
                fp += labels[i] == 0
            else:
                fn += labels[i] == 1
                tn += labels[i] == 0
    flagged = tp + fp
    return {
        "establishable": True,
        "n": n, "positives": positives, "negatives": negatives,
        "folds": folds_k, "folds_skipped": skipped,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn, "abstain": abstain, "flagged": flagged,
        "precision": (tp / flagged) if flagged else None,
        "ci95": wilson(tp, flagged),
        "recall": (tp / (tp + fn)) if (tp + fn) else None,
    }


def clears_bar(result: dict, baseline_rate: float | None) -> bool | None:
    """``True`` when the flag-precision interval's lower bound exceeds the
    baseline; ``False`` when it does not; ``None`` when the measurement is not
    establishable or no row was flagged (nothing to compare)."""
    if not result.get("establishable") or result.get("flagged", 0) == 0 or baseline_rate is None:
        return None
    return result["ci95"][0] > baseline_rate


def measure(paired: Sequence[object], *, k: int = 5, seed: int = 0) -> dict:
    """The full table for one paired corpus: base rate plus, per trio model,
    cross-validated flag precision and the bar verdict."""
    rows = [p.features for p in paired]
    labels = [int(p.label) for p in paired]
    prior = base_rate(labels)
    table = {}
    for name, factory in MODELS.items():
        result = flag_precision(rows, labels, factory, k=k, seed=seed)
        result["clears_bar"] = clears_bar(result, prior["rate"])
        table[name] = result
    return {"paired_n": len(paired), "base_rate": prior, "models": table,
            "confidence_baseline": "UNMEASURED: no commitment record carries the agent's self-assessed confidence"}
