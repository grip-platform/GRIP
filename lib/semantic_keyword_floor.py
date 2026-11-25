"""Semantic keyword floor — CF-IOF discriminative weighting with margin/tau abstention.

The literal keyword floors in this tree (``re.compile`` sites, Jaccard overlap)
decide on keyword PRESENCE: a sentence fires when it contains a listed word or
shape. That is precise and blind to paraphrase. This module lifts the floor from
literal to semantic-by-statistics without leaving the standard library:

  * each token is weighted by how UNIQUELY it discriminates a class — the
    class's document frequency over the overall document frequency (CF-IOF,
    Scheepers 2011, Stellenbosch);
  * a text is scored per class by the mean weight of its KNOWN tokens
    (unknown tokens carry no evidence and are skipped);
  * the verdict goes to the top class only when its score clears the mean of
    the other classes by at least ``tau`` (the SIGD uniqueness margin);
  * otherwise the floor ABSTAINS — a calibrated "I don't know" by construction.

Abstention is the precision guarantee a gate needs: a balanced or unseen text
yields no verdict rather than a wrong one, and ``calibrate_tau`` places the
threshold from MEASURED margins on labelled probes, never from assertion.

Third lift of one shape — extracted at N=3 per YSH (abstract after three):

  lib/continuity_trio.py::CFIOF               numeric feature rows, two classes
  lib/confab_sigd_classifier.py::ConfabSIGD   char n-gram cells, confab-vs-rest
  ~/.hal/examples/gridlm/gridlm_sigd.py       SIGD-Route expert routing + null cell

Two faces, one engine. ``SemanticKeywordFloor`` is the engine: any label set,
signed margins, calibration, a verdict that says whether it abstained.
``CFIOFKeywordRouter`` and ``CharNgramRouter`` keep the binary
``fit(docs, labels) / predict(text) -> {"label", "score"}`` contract GRIP#6961
shipped, so nothing written against that contract changes. It composes with
an LLM recall leg in ``lib/semantic_keyword_augment.py`` per
``rules/llm-augmentation-pattern.md``.

What "semantic" means here, and what it does not: no model, no embeddings, no
network. Deterministic is not the same as provably sound: the floor's precision
is an EMPIRICAL property of the corpus it was fitted on, reported by
``lib/semantic_keyword_bench.py`` as held-out numbers against a real literal
floor. CF-IOF is a published 2011 method, not a moat; the only claim made for
this module is a measured recall gain over a literal floor on a named corpus,
with false positives unchanged, and the bench is where that claim lives or dies.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, NamedTuple, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from lib.confab_sigd_classifier import _CharNgram  # noqa: E402 — one char-n-gram cell, not two

__all__ = [
    "DEFAULT_STOPWORDS",
    "DEFAULT_TAU",
    "Calibration",
    "CFIOFKeywordRouter",
    "CharNgramRouter",
    "FloorVerdict",
    "SemanticKeywordFloor",
    "calibrate_tau",
    "cfiof_weights",
    "tokenize",
]

#: Placeholder threshold on the SIGD margin scale (top class minus the mean of
#: the others; a token exclusive to one of two classes lands near 2.0, a shared
#: token near 0). It is deliberately conservative; the published shape is to
#: replace it with ``calibrate_tau`` from labelled probes.
DEFAULT_TAU = 0.5

#: Function words stripped before weighting (the GRIP#6961 list, kept verbatim).
#: Measured on the operator-close corpus: stripping them lifts held-out recall
#: from 7/17 to 12/17 at zero false positives, because on a small corpus a
#: one-off function word otherwise carries a whole sentence's label.
DEFAULT_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are",
    "was", "were", "with", "this", "that", "it", "as", "by", "be", "at",
})

_CONTRACTIONS = (
    (re.compile(r"\b(\w+)n't\b"), r"\1 not"),
    (re.compile(r"\b(\w+)'ve\b"), r"\1 have"),
    (re.compile(r"\b(\w+)'re\b"), r"\1 are"),
    (re.compile(r"\b(\w+)'ll\b"), r"\1 will"),
    (re.compile(r"\b(\w+)'d\b"), r"\1 would"),
    (re.compile(r"\b(\w+)'m\b"), r"\1 am"),
    (re.compile(r"\b(\w+)'s\b"), r"\1"),
)
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUM_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_TOKEN_RE = re.compile(r"<time>|<num>|[a-z]+")


def tokenize(text: str, stopwords: frozenset = DEFAULT_STOPWORDS) -> list[str]:
    """Lowercase word tokens with light normalisation, stopword-light.

    Contractions are expanded (``you've`` -> ``you have``) so a paraphrase
    shares tokens with its long form; a clock reading becomes ``<time>`` and
    any other number ``<num>``, because the floor cares that a time was
    named, not which one. Punctuation is dropped, then ``stopwords`` (pass an
    empty set to keep them). Deterministic, total.
    """
    body = text.lower() if isinstance(text, str) else ""
    for pattern, repl in _CONTRACTIONS:
        body = pattern.sub(repl, body)
    body = _TIME_RE.sub(" <time> ", body)
    body = _NUM_RE.sub(" <num> ", body)
    return [tok for tok in _TOKEN_RE.findall(body) if tok not in stopwords]


class FloorVerdict(NamedTuple):
    """Outcome of classifying one text.

    ``label`` is None when the floor abstains. ``margin`` is the SIGD
    uniqueness margin of ``top`` over the mean of the other classes.
    ``active`` is how many of the text's tokens the floor had evidence for —
    zero means every token was unseen and the verdict is an abstention by
    construction.
    """

    label: Optional[Any]
    top: Optional[Any]
    margin: float
    scores: dict
    active: int

    @property
    def abstained(self) -> bool:
        return self.label is None


class Calibration(NamedTuple):
    """Result of ``calibrate_tau``: the threshold and the evidence behind it.

    ``ucl`` is the Shewhart upper control limit of the out-of-class margins
    (mean + ``sigma`` standard deviations); ``tau`` is never below it.
    """

    tau: float
    separable: bool
    lo_in: float
    hi_out: float
    ucl: float


def _upper_control_limit(values: Sequence[float], sigma: float) -> float:
    """Shewhart UCL: mean + sigma * sample standard deviation (the mean alone when n < 2)."""
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean + sigma * math.sqrt(var)


def calibrate_tau(in_margins: Iterable[float], out_margins: Iterable[float],
                  sigma: float = 0.0) -> Calibration:
    """Place tau from MEASURED margins (SIGD ``calibrate_tau`` contract).

    ``in_margins`` are target-vs-rest margins of texts that ARE the target
    class; ``out_margins`` the same quantity for texts that are not. When the
    two populations separate, tau is the midpoint; when they overlap it is
    just above the highest out-of-class margin, so no calibration negative
    can fire. ``sigma`` > 0 additionally raises tau to at least the
    negatives' Shewhart upper control limit (mean + ``sigma`` SD). It is off
    by default: a threshold on the worst observed negative is crossed by
    roughly one fresh negative in n+1, which is a real cost, but on a
    one-sided margin distribution the 3-sigma limit priced recall away
    entirely (see ``docs/measurements/`` under ``semantic-keyword``).

    The overlap branch deliberately differs from ``ConfabSIGD`` (which falls
    back to the lowest in-class margin): inside a gate the deterministic floor
    is the zero-false-positive layer, so the conservative direction is MORE
    abstention, never a threshold a known negative already clears.
    """
    ins = [float(m) for m in in_margins]
    outs = [float(m) for m in out_margins]
    if not ins or not outs:
        raise ValueError("calibrate_tau needs at least one in-class and one out-of-class margin")
    lo_in, hi_out = min(ins), max(outs)
    separable = lo_in > hi_out
    base = (lo_in + hi_out) / 2.0 if separable else math.nextafter(hi_out, math.inf)
    ucl = _upper_control_limit(outs, sigma)
    return Calibration(max(base, ucl), separable, lo_in, hi_out, ucl)


def _class_weights(class_df: Counter, class_size: int, overall_df: Counter,
                   n_docs: int, k: float) -> dict[str, float]:
    """One class's weight table: smoothed P(token | class) / P(token)."""
    denom_class = class_size + 2 * k
    denom_all = n_docs + 2 * k
    return {
        tok: ((class_df[tok] + k) / denom_class) / ((df + k) / denom_all)
        for tok, df in overall_df.items()
    }


def _cfiof_tables(docs: Sequence[str], labels: Sequence[Any], k: float, min_df: int,
                  tokenizer: Callable[[str], Sequence[str]],
                  ) -> tuple[dict[Any, dict[str, float]], dict[str, int]]:
    """``({label: {token: weight}}, {token: df})`` — the engine's two tables."""
    classes = sorted(set(labels), key=repr)
    overall: Counter = Counter()
    per_class: dict[Any, Counter] = {c: Counter() for c in classes}
    sizes: Counter = Counter(labels)
    for text, lab in zip(docs, labels):
        toks = set(tokenizer(text))
        overall.update(toks)
        per_class[lab].update(toks)
    overall = Counter({tok: df for tok, df in overall.items() if df >= min_df})
    weights = {c: _class_weights(per_class[c], sizes[c], overall, len(docs), k) for c in classes}
    return weights, dict(overall)


def cfiof_weights(docs: Sequence[str], labels: Sequence[Any], k: float = 0.5,
                  min_df: int = 1, tokenizer: Callable[[str], Sequence[str]] = tokenize,
                  ) -> dict[Any, dict[str, float]]:
    """CF-IOF weights per class over DOCUMENT frequency: ``{label: {token: weight}}``.

    A token counts once per text (CFIOF's binarised features). A keyword that
    appears in one class only weighs above 1 there and below 1 elsewhere; one
    spread at its base rate weighs about 1 everywhere and so carries no
    evidence. Tokens seen in fewer than ``min_df`` texts are dropped. This is
    the GRIP#6961 function, generalised from {0, 1} to any label set.
    """
    return _cfiof_tables(docs, labels, k, min_df, tokenizer)[0]


class SemanticKeywordFloor:
    """CF-IOF token weights per class, SIGD margin, tau abstention — the engine.

    ``fit(texts, labels)`` learns one weight per (class, token) (the
    ``cfiof_weights`` tables); scoring is the mean of those weights over the text's
    known tokens. With ``evidence_weighted`` each token counts in proportion to
    its training document frequency (inverse-variance weighting). Measured on
    the operator-close corpus that option is net-negative once stopwords are
    stripped (recall 7/17 against 12/17), so it is off by default and kept only
    so the measurement record's rows stay re-derivable.
    """

    def __init__(self, tau: float = DEFAULT_TAU, k: float = 0.5, min_df: int = 1,
                 evidence_weighted: bool = False,
                 tokenizer: Callable[[str], Sequence[str]] = tokenize) -> None:
        if k <= 0:
            raise ValueError("smoothing k must be positive")
        if min_df < 1:
            raise ValueError("min_df must be at least 1")
        self.tau = float(tau)
        self.k = float(k)
        self.min_df = int(min_df)
        self.evidence_weighted = bool(evidence_weighted)
        self.tokenizer = tokenizer
        self._weights: dict[Any, dict[str, float]] = {}
        self._labels: tuple = ()
        self._df: dict[str, int] = {}

    # -- fitting -----------------------------------------------------------

    def fit(self, texts: Sequence[str], labels: Sequence[Any]) -> "SemanticKeywordFloor":
        if len(texts) != len(labels):
            raise ValueError("texts and labels must have the same length")
        if len(set(labels)) < 2:
            raise ValueError("a discriminative margin needs at least two classes")
        self._weights, self._df = _cfiof_tables(texts, labels, self.k, self.min_df, self.tokenizer)
        self._labels = tuple(self._weights)
        return self

    @property
    def labels(self) -> tuple:
        return self._labels

    def weight(self, label: Any, token: str) -> Optional[float]:
        """The learned CF-IOF weight, or None for an unseen token."""
        return self._weights.get(label, {}).get(token)

    # -- scoring -----------------------------------------------------------

    def _active_tokens(self, text: str) -> list[str]:
        if not self._weights:
            raise RuntimeError("SemanticKeywordFloor.fit() must run before scoring")
        return [tok for tok in set(self.tokenizer(text)) if tok in self._df]

    def scores(self, text: str) -> dict[Any, float]:
        """Per-class (optionally evidence-weighted) mean weight over known tokens; 0.0 when none."""
        active = self._active_tokens(text)
        if not active:
            return {c: 0.0 for c in self._labels}
        mass = {tok: (self._df[tok] if self.evidence_weighted else 1) for tok in active}
        total = sum(mass.values())
        return {
            c: sum(self._weights[c][tok] * mass[tok] for tok in active) / total
            for c in self._labels
        }

    def margin_for(self, text: str, label: Any) -> float:
        """``label``'s score minus the mean of the other classes' scores."""
        s = self.scores(text)
        if label not in s:
            raise KeyError(f"unknown label {label!r}; fitted labels are {self._labels}")
        others = [v for c, v in s.items() if c != label]
        return s[label] - (sum(others) / len(others))

    def classify(self, text: str) -> FloorVerdict:
        """Top class iff its uniqueness margin clears tau; otherwise abstain."""
        s = self.scores(text)
        active = len(self._active_tokens(text))
        if active == 0:
            return FloorVerdict(None, None, 0.0, s, 0)
        top = max(s, key=lambda c: (s[c], repr(c)))
        others = [v for c, v in s.items() if c != top]
        margin = s[top] - (sum(others) / len(others))
        label = top if margin >= self.tau else None
        return FloorVerdict(label, top, margin, s, active)


# ── the GRIP#6961 binary faces ───────────────────────────────────────────────

class CFIOFKeywordRouter:
    """Binary ``fit(docs, labels in {0,1}) / predict(text)`` face over the engine.

    ``predict`` returns ``{"label": 0 | 1 | None, "score": margin}`` with the
    margin on the CFIOF half-scale (``|s1 - s0| / 2``, best over the
    cross-class mean), so ``tau`` keeps its GRIP#6961 meaning. Unseen tokens
    no longer dilute the mean — they carry no evidence either way.
    """

    def __init__(self, tau: float = 0.08, k: float = 0.5) -> None:
        self.tau = tau
        self.k = k
        self._floor: Optional[SemanticKeywordFloor] = None

    def fit(self, docs: Sequence[str], labels: Sequence[int]) -> "CFIOFKeywordRouter":
        if not docs or not labels or len(docs) != len(labels):
            raise ValueError("docs and labels must be non-empty and equal length")
        if any(lab not in (0, 1) for lab in labels):
            raise ValueError("labels must be 0 or 1")
        self._floor = SemanticKeywordFloor(tau=2.0 * self.tau, k=self.k).fit(docs, labels)
        return self

    def predict(self, text: str) -> dict[str, Any]:
        if self._floor is None:
            raise RuntimeError("fit before predict")
        v = self._floor.classify(text)
        return {"label": v.label, "score": v.margin / 2.0}


class CharNgramRouter:
    """SIGD char-n-gram cells behind the same binary ``fit/predict`` contract.

    The cell is ``confab_sigd_classifier._CharNgram`` itself — one char-n-gram
    model in the tree, not a re-derivation — scored as ``ConfabSIGD`` scores
    it (average log-probability per class), routed by the CFIOF half-scale
    margin with tau abstention. For short spans where word tokens are not the
    right surface.
    """

    def __init__(self, order: int = 3, k: float = 0.05, tau: Optional[float] = None) -> None:
        self.order = order
        self.k = k
        self.tau = 0.1 if tau is None else tau
        self._cells: dict[int, _CharNgram] = {}

    def fit(self, docs: Sequence[str], labels: Sequence[int]) -> "CharNgramRouter":
        if not docs or not labels or len(docs) != len(labels):
            raise ValueError("docs and labels must be non-empty and equal length")
        if len(set(labels)) < 2:
            raise ValueError("need both classes present to fit")
        by_class: dict[int, str] = {}
        for doc, lab in zip(docs, labels):
            by_class[lab] = by_class.get(lab, "") + " " + doc
        self._cells = {lab: _CharNgram(text, self.order, self.k) for lab, text in by_class.items()}
        return self

    def predict(self, text: str) -> dict[str, Any]:
        if not self._cells:
            raise RuntimeError("fit before predict")
        s0 = self._cells[0].avg_logprob(text)
        s1 = self._cells[1].avg_logprob(text)
        margin = abs(s1 - s0) / 2.0
        if margin < self.tau:
            return {"label": None, "score": margin}
        return {"label": 1 if s1 > s0 else 0, "score": margin}
