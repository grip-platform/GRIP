"""Congruent Continuity — the thesis trio: CF-IOF vs Naive Bayes vs Logistic Regression.

Clean-room, stdlib-only classifiers sized for tiny corpora, re-running the
operator's 2011 thesis methodology (Scheepers 2011, Stellenbosch: CF-IOF
~76.05%, NB 74.8%, LR 77.89% on name->language classification) on a new
domain: predicting congruence verdicts (1 = wrong-track/INCONGRUENT,
0 = held/CONGRUENT) from deterministic trajectory features
(``lib.continuity_features.extract_features`` rows).

The CF-IOF lift follows ``gridlm_sigd``'s discriminative-routing shape — the
second lift of the 2011 idea and the relation the Gentner gate licenses:
score each class by feature weights that are high when a feature is UNIQUE
to that class (class frequency over overall frequency), take the margin of
the best class over the cross-class mean, and ABSTAIN when the margin is
below tau — a calibrated "I don't know" by construction. Abstention maps to
the UNVERIFIABLE verdict / observe-only brake tier (CONGRUENCE.md).

All three share one API:
    fit(rows: list[dict[str, float]], labels: list[int]) -> self
    predict(row: dict[str, float]) -> {"label": 0 | 1 | None, "score": float}
(``label is None`` only from CF-IOF's abstention.)

``benchmark(rows, labels, k_folds=5, seed=0)`` is the head-to-head harness:
per-model accuracy-on-decided, abstain_rate, and sparse_class_recall — the
same comparison table shape as the thesis, including its observation that
CF-IOF's strength is sparse-class recall.
"""
from __future__ import annotations

import math
import random
from statistics import median
from typing import Any

__all__ = ["CFIOF", "BernoulliNB", "LogisticSGD", "benchmark"]


def _validate(rows: list[dict[str, float]], labels: list[int]) -> list[str]:
    if not rows or not labels or len(rows) != len(labels):
        raise ValueError("rows and labels must be non-empty and equal length")
    if any(lab not in (0, 1) for lab in labels):
        raise ValueError("labels must be 0 or 1")
    if len(set(labels)) < 2:
        raise ValueError("need both classes present to fit")
    keys = sorted(rows[0])
    if any(sorted(r) != keys for r in rows):
        raise ValueError("all rows must share one feature-key set")
    return keys


def _binarise_thresholds(rows: list[dict[str, float]], keys: list[str]) -> dict[str, float]:
    return {k: median(r[k] for r in rows) for k in keys}


def _class_midpoint_thresholds(
    rows: list[dict[str, float]], labels: list[int], keys: list[str]
) -> dict[str, float]:
    """Supervised discretiser for CF-IOF: threshold = midpoint of the two
    class-conditional means. The unsupervised median splits the SAMPLE in
    half, which lands inside the majority class's range whenever classes are
    imbalanced — upper-tail majority rows then activate the discriminative
    features and are confidently mislabelled (measured: 0.625 train accuracy
    on separable synthetic data). The midpoint tracks the class geometry
    instead; labels are available at fit time, so this is legitimate."""
    out: dict[str, float] = {}
    for k in keys:
        m1 = [r[k] for r, lab in zip(rows, labels) if lab == 1]
        m0 = [r[k] for r, lab in zip(rows, labels) if lab == 0]
        out[k] = ((sum(m1) / len(m1)) + (sum(m0) / len(m0))) / 2.0
    return out


def _binary(row: dict[str, float], thresholds: dict[str, float]) -> dict[str, int]:
    return {k: 1 if row.get(k, t) > t else 0 for k, t in thresholds.items()}


class CFIOF:
    """Class Frequency-Inverse Overall Frequency with margin/tau abstention."""

    def __init__(self, tau: float = 0.08, k: float = 0.5) -> None:
        self.tau = tau
        self.k = k
        self._w: dict[int, dict[str, float]] = {}
        self._thresholds: dict[str, float] = {}

    def fit(self, rows: list[dict[str, float]], labels: list[int]) -> "CFIOF":
        keys = _validate(rows, labels)
        self._thresholds = _class_midpoint_thresholds(rows, labels, keys)
        binary = [_binary(r, self._thresholds) for r in rows]
        n = len(binary)
        overall = {f: (sum(b[f] for b in binary) + self.k) / (n + 2 * self.k) for f in keys}
        self._w = {}
        for c in (0, 1):
            members = [b for b, lab in zip(binary, labels) if lab == c]
            m = len(members)
            self._w[c] = {
                f: ((sum(b[f] for b in members) + self.k) / (m + 2 * self.k)) / overall[f]
                for f in keys
            }
        return self

    def _scores(self, row: dict[str, float]) -> dict[int, float]:
        b = _binary(row, self._thresholds)
        active = [f for f, v in b.items() if v == 1]
        if not active:
            return {0: 0.0, 1: 0.0}
        return {c: sum(self._w[c][f] for f in active) / len(active) for c in (0, 1)}

    def predict(self, row: dict[str, float]) -> dict[str, Any]:
        s = self._scores(row)
        best = max(s, key=lambda c: s[c])
        mean = (s[0] + s[1]) / 2.0
        margin = s[best] - mean  # sigd shape: best over the cross-class mean
        if margin < self.tau:
            return {"label": None, "score": margin}
        return {"label": best, "score": margin}


class BernoulliNB:
    """Bernoulli Naive Bayes with add-k smoothing over median-binarised features."""

    def __init__(self, k: float = 0.5) -> None:
        self.k = k
        self._prior: dict[int, float] = {}
        self._p: dict[int, dict[str, float]] = {}
        self._thresholds: dict[str, float] = {}

    def fit(self, rows: list[dict[str, float]], labels: list[int]) -> "BernoulliNB":
        keys = _validate(rows, labels)
        self._thresholds = _binarise_thresholds(rows, keys)
        binary = [_binary(r, self._thresholds) for r in rows]
        n = len(binary)
        for c in (0, 1):
            members = [b for b, lab in zip(binary, labels) if lab == c]
            m = len(members)
            self._prior[c] = m / n
            self._p[c] = {
                f: (sum(b[f] for b in members) + self.k) / (m + 2 * self.k) for f in keys
            }
        return self

    def predict(self, row: dict[str, float]) -> dict[str, Any]:
        b = _binary(row, self._thresholds)
        logp = {}
        for c in (0, 1):
            total = math.log(self._prior[c])
            for f, v in b.items():
                p = self._p[c][f]
                total += math.log(p if v == 1 else 1.0 - p)
            logp[c] = total
        best = max(logp, key=lambda c: logp[c])
        # posterior margin in probability space (stable via max-shift)
        mx = max(logp.values())
        exp = {c: math.exp(v - mx) for c, v in logp.items()}
        post = exp[best] / (exp[0] + exp[1])
        return {"label": best, "score": post}


class LogisticSGD:
    """Binary logistic regression: standardised features, plain SGD, L2, fixed seed."""

    def __init__(self, lr: float = 0.1, epochs: int = 200, l2: float = 1e-3, seed: int = 0) -> None:
        self.lr, self.epochs, self.l2, self.seed = lr, epochs, l2, seed
        self._w: dict[str, float] = {}
        self._b = 0.0
        self._mu: dict[str, float] = {}
        self._sd: dict[str, float] = {}

    def _z(self, row: dict[str, float]) -> dict[str, float]:
        return {k: (row.get(k, self._mu[k]) - self._mu[k]) / self._sd[k] for k in self._mu}

    def fit(self, rows: list[dict[str, float]], labels: list[int]) -> "LogisticSGD":
        keys = _validate(rows, labels)
        n = len(rows)
        self._mu = {k: sum(r[k] for r in rows) / n for k in keys}
        self._sd = {}
        for k in keys:
            var = sum((r[k] - self._mu[k]) ** 2 for r in rows) / n
            self._sd[k] = math.sqrt(var) or 1.0
        z = [self._z(r) for r in rows]
        self._w = {k: 0.0 for k in keys}
        self._b = 0.0
        rng = random.Random(self.seed)
        order = list(range(n))
        for _ in range(self.epochs):
            rng.shuffle(order)
            for i in order:
                pred = self._sigmoid(sum(self._w[k] * z[i][k] for k in keys) + self._b)
                err = pred - labels[i]
                for k in keys:
                    self._w[k] -= self.lr * (err * z[i][k] + self.l2 * self._w[k])
                self._b -= self.lr * err
        return self

    @staticmethod
    def _sigmoid(x: float) -> float:
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        e = math.exp(x)
        return e / (1.0 + e)

    def predict(self, row: dict[str, float]) -> dict[str, Any]:
        z = self._z(row)
        p = self._sigmoid(sum(self._w[k] * z[k] for k in self._w) + self._b)
        return {"label": 1 if p > 0.5 else 0, "score": p}


def _folds(n: int, k_folds: int, seed: int) -> list[list[int]]:
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    buckets: list[list[int]] = [[] for _ in range(k_folds)]
    for pos, i in enumerate(idx):
        buckets[pos % k_folds].append(i)
    return buckets

def _fold_metrics(model_cls, rows, labels, test_idx, train_idx):
    model = model_cls().fit([rows[i] for i in train_idx], [labels[i] for i in train_idx])
    decided = correct = abstained = sparse_hits = sparse_total = 0
    for i in test_idx:
        out = model.predict(rows[i])
        if out["label"] is None:
            abstained += 1
            continue
        decided += 1
        correct += int(out["label"] == labels[i])
        if labels[i] == 1:
            sparse_total += 1
            sparse_hits += int(out["label"] == 1)
    return decided, correct, abstained, sparse_hits, sparse_total


def benchmark(rows: list[dict[str, float]], labels: list[int], *, k_folds: int = 5, seed: int = 0) -> dict[str, dict[str, float]]:
    """Head-to-head k-fold table: accuracy on decided, abstain rate, sparse-class recall."""
    _validate(rows, labels)
    k_folds = min(k_folds, len(rows))
    buckets = _folds(len(rows), k_folds, seed)
    table: dict[str, dict[str, float]] = {}
    for name, cls in (("cfiof", CFIOF), ("naive_bayes", BernoulliNB), ("logreg", LogisticSGD)):
        decided = correct = abstained = sparse_hits = sparse_total = 0
        for f in range(k_folds):
            test_idx = buckets[f]
            train_idx = [i for g, b in enumerate(buckets) if g != f for i in b]
            if len({labels[i] for i in train_idx}) < 2 or not test_idx:
                continue
            d, c, a, sh, st = _fold_metrics(cls, rows, labels, test_idx, train_idx)
            decided += d; correct += c; abstained += a
            sparse_hits += sh; sparse_total += st
        table[name] = {
            "accuracy": (correct / decided) if decided else 0.0,
            "abstain_rate": abstained / max(1, decided + abstained),
            "sparse_class_recall": (sparse_hits / sparse_total) if sparse_total else 0.0,
        }
    return table
