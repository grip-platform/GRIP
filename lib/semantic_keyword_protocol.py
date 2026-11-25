#!/usr/bin/env python3
"""Domain-agnostic protocol for measuring a semantic keyword floor against the
literal floor it would replace.

The capability under test is general (``lib/semantic_keyword_floor.py`` fits
any label set); only the measurement was ever written against one domain.
This module is that measurement with the domain lifted out. A ``Domain`` is
three things the brief names: the LITERAL floor callable, the LABELLED corpora
(a calibration set in memory plus named JSONL files), and the TARGET label.
Everything else is the protocol, and the protocol does not change between
domains:

  nested leave-one-out  score_calibration(): every calibration sentence is
                        classified by a floor that never saw it, with tau
                        calibrated inside the fold from the other sentences'
                        own leave-one-out margins.
  shipped floor         score_shipped(): the deployment-shaped floor (fitted on
                        the calibration set, tau from its LOO margins) scored on
                        a held-out corpus. This is where domain 1 died.
  retrained             score_retrained(): leave-one-out over calibration set
                        plus a corpus together, single-level or nested.
  the feature axis      score_feature_axis(): FEATURE_AXIS cells (word
                        unigrams, unigrams+bigrams, bigrams, min_df=2, char
                        3- and 4-grams) under the retrained protocol.
  frozen tau            score_frozen(): fit on calibration set plus named
                        corpora, FREEZE tau at that full-fit calibration, score
                        a FRESH corpus with no recalibration, and read the
                        false-positive count against a rule fixed before the
                        corpus existed (``Domain.reject_at``). This is the gate;
                        per-fold leave-one-out is optimistic (domain 1: 2 in 117
                        became 54 in 500 at frozen tau).
  paired McNemar        mcnemar_exact() / paired_mcnemar(): the positives are
                        the same items under both floors, so recall is compared
                        as discordant pairs with the exact two-sided test.

``run_protocol`` runs all of it for one domain and one configuration and
returns a JSON-ready dict a lane writes beside its verdict. Pure Python, no
network; everything a Row carries is re-derivable from the inputs.

Domain 1 (operator-close) is one ``Domain`` in ``lib/semantic_keyword_bench.py``,
whose wrappers reproduce the recorded numbers unchanged; the tests in
``tests/test_semantic_keyword_bench.py`` pin that.
"""

from __future__ import annotations

import json
import math
import sys
from functools import partial
from pathlib import Path
from typing import Callable, Mapping, NamedTuple, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from lib.confab_sigd_classifier import _CharNgram  # noqa: E402
from lib.semantic_keyword_floor import (  # noqa: E402
    Calibration,
    FloorVerdict,
    SemanticKeywordFloor,
    calibrate_tau,
    tokenize,
)


class Config(NamedTuple):
    """One floor configuration under test."""

    stopwords: bool
    evidence_weighted: bool
    sigma: float
    min_df: int = 1
    features: str = "unigram"   # unigram | uni+bigram | bigram | char3 | char4

    @property
    def name(self) -> str:
        return (f"{self.features} stop={int(self.stopwords)} ew={int(self.evidence_weighted)} "
                f"sigma={self.sigma:g} min_df={self.min_df}")


#: The shipped default: GRIP#6961's stopword-stripped plain mean, calibrated tau.
DEFAULT = Config(stopwords=True, evidence_weighted=False, sigma=0.0)

#: The feature axis: alternative L1 representations, same protocol, criterion fixed first.
FEATURE_AXIS = (
    Config(True, False, 0.0, 1, "unigram"),      # the shipped design, as the baseline row
    Config(True, False, 0.0, 1, "uni+bigram"),
    Config(True, False, 0.0, 1, "bigram"),
    Config(True, False, 0.0, 2, "unigram"),
    Config(True, False, 0.0, 1, "char3"),
    Config(True, False, 0.0, 1, "char4"),
)


class Domain(NamedTuple):
    """Everything that differs between two measurements of the same capability.

    ``positives``/``negatives`` are the calibration set the floor is fitted on;
    ``corpora`` maps a short name to a labelled JSONL file of ``{"text", "label"}``
    rows, where ``label`` is ``target`` or ``other``. ``novel`` holds recorded
    paraphrases scored by the full fit. ``reject_at`` is the pre-registered
    frozen-tau reading: that many false positives on the fresh corpus rejects
    a 1% rate.
    """

    name: str
    target: str
    other: str
    literal: Callable[[str], bool]
    positives: Sequence[str]
    negatives: Sequence[str]
    corpora: Mapping[str, Path]
    novel: Sequence[str] = ()
    reject_at: int = 7
    pos_split: str = "fire"
    neg_split: str = "quiet"


class Row(NamedTuple):
    """One scored sentence."""

    text: str
    label: str          # the domain's target (positive) or other (negative)
    split: str          # calibration split, "novel", or "<corpus>pos" / "<corpus>neg"
    literal: bool
    semantic: bool
    margin: float       # target-vs-rest margin from the model that scored it
    tau: float


class Summary(NamedTuple):
    """Totals for one configuration."""

    config: str
    positives: int
    negatives: int
    literal_recall: int
    semantic_recall: int
    union_recall: int
    literal_fp: int
    semantic_fp: int
    union_fp: int
    gap_closed: list       # positives literal missed and semantic caught
    fp_added: list         # negatives semantic fired on and literal did not
    confirmed: bool


# ---------------------------------------------------------------------------
# floors
# ---------------------------------------------------------------------------

def _bigrams(text: str) -> list[str]:
    """Adjacent word pairs over the FULL token stream (stopwords kept, so the
    phrase shape 'go to', 'needs you', 'done for' survives)."""
    toks = tokenize(text, stopwords=frozenset())
    return [f"{a}_{b}" for a, b in zip(toks, toks[1:])]


def _uni_plus_bigram(text: str) -> list[str]:
    return tokenize(text) + _bigrams(text)


class CharNgramFloor:
    """ConfabSIGD's char-n-gram cell behind the engine's fit/classify/margin_for
    contract, so the same leave-one-out machinery can score it. Score per class
    is the average log-probability of the text under that class's cell."""

    def __init__(self, order: int, tau: float = 0.0, k: float = 0.05) -> None:
        self.order, self.k, self.tau = order, k, float(tau)
        self._cells: dict = {}

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> "CharNgramFloor":
        by_class: dict = {}
        for text, lab in zip(texts, labels):
            by_class[lab] = by_class.get(lab, "") + " " + text.lower()
        self._cells = {lab: _CharNgram(body, self.order, self.k) for lab, body in by_class.items()}
        return self

    def scores(self, text: str) -> dict:
        return {lab: cell.avg_logprob(text.lower()) for lab, cell in self._cells.items()}

    def margin_for(self, text: str, label: str) -> float:
        s = self.scores(text)
        others = [v for c, v in s.items() if c != label]
        return s[label] - sum(others) / len(others)

    def classify(self, text: str) -> FloorVerdict:
        s = self.scores(text)
        top = max(s, key=lambda c: (s[c], repr(c)))
        others = [v for c, v in s.items() if c != top]
        margin = s[top] - sum(others) / len(others)
        return FloorVerdict(top if margin >= self.tau else None, top, margin, s, 1)


_TOKENIZERS = {"unigram": None, "uni+bigram": _uni_plus_bigram, "bigram": _bigrams}


def make_floor(cfg: Config):
    """An unfitted floor for ``cfg`` (semantic engine or the char-n-gram cell)."""
    if cfg.features.startswith("char"):
        return CharNgramFloor(order=int(cfg.features[4:]))
    tok = _TOKENIZERS[cfg.features]
    if tok is None:
        tok = tokenize if cfg.stopwords else partial(tokenize, stopwords=frozenset())
    return SemanticKeywordFloor(tau=0.0, min_df=cfg.min_df,
                                evidence_weighted=cfg.evidence_weighted, tokenizer=tok)


def loo_margins(texts: Sequence[str], labels: Sequence[str], cfg: Config,
                target: str) -> list[float]:
    """Honest target-vs-rest margin for every text, from a fit that excluded it."""
    out = []
    for i, text in enumerate(texts):
        rest_t = list(texts[:i]) + list(texts[i + 1:])
        rest_l = list(labels[:i]) + list(labels[i + 1:])
        out.append(make_floor(cfg).fit(rest_t, rest_l).margin_for(text, target))
    return out


def calibrated_floor(texts: Sequence[str], labels: Sequence[str], cfg: Config,
                     target: str) -> tuple[SemanticKeywordFloor, Calibration]:
    """Fit on ``texts`` with tau calibrated from their LOO margins."""
    margins = loo_margins(texts, labels, cfg, target)
    ins = [m for m, lab in zip(margins, labels) if lab == target]
    outs = [m for m, lab in zip(margins, labels) if lab != target]
    cal = calibrate_tau(ins, outs, sigma=cfg.sigma)
    floor = make_floor(cfg).fit(texts, labels)
    floor.tau = cal.tau
    return floor, cal


# ---------------------------------------------------------------------------
# corpora
# ---------------------------------------------------------------------------

def load_corpus(path: Path, target: str, other: str) -> tuple[list[str], list[str]]:
    """(negatives, positives) from a labelled JSONL corpus; a missing file is empty."""
    if not Path(path).exists():
        return [], []
    rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    return ([r["text"] for r in rows if r["label"] == other],
            [r["text"] for r in rows if r["label"] == target])


def _calibration(domain: Domain) -> tuple[list[str], list[str], list[str]]:
    """(texts, labels, splits) of the calibration set, positives first."""
    texts = list(domain.positives) + list(domain.negatives)
    labels = [domain.target] * len(domain.positives) + [domain.other] * len(domain.negatives)
    splits = [domain.pos_split] * len(domain.positives) + [domain.neg_split] * len(domain.negatives)
    return texts, labels, splits


def _corpus_rows(domain: Domain, name: str) -> tuple[list[str], list[str], list[str]]:
    """(texts, labels, splits) of a named corpus, positives first."""
    neg, pos = load_corpus(domain.corpora[name], domain.target, domain.other)
    return (pos + neg,
            [domain.target] * len(pos) + [domain.other] * len(neg),
            [f"{name}pos"] * len(pos) + [f"{name}neg"] * len(neg))


def _row(domain: Domain, floor, cal: Calibration, text: str, label: str, split: str) -> Row:
    return Row(text, label, split, bool(domain.literal(text)),
               floor.classify(text).label == domain.target,
               floor.margin_for(text, domain.target), cal.tau)


# ---------------------------------------------------------------------------
# the protocol
# ---------------------------------------------------------------------------

def score_calibration(domain: Domain, cfg: Config = DEFAULT) -> list[Row]:
    """Nested leave-one-out over the calibration set, plus ``novel`` against the full fit."""
    texts, labels, splits = _calibration(domain)
    rows: list[Row] = []
    for i, text in enumerate(texts):
        train_t, train_l = texts[:i] + texts[i + 1:], labels[:i] + labels[i + 1:]
        floor, cal = calibrated_floor(train_t, train_l, cfg, domain.target)
        rows.append(_row(domain, floor, cal, text, labels[i], splits[i]))
    floor, cal = calibrated_floor(texts, labels, cfg, domain.target)
    rows += [_row(domain, floor, cal, text, domain.target, "novel") for text in domain.novel]
    return rows


def score_shipped(domain: Domain, cfg: Config, corpus: str) -> list[Row]:
    """The deployment-shaped floor (fitted on the calibration set, tau from its
    LOO margins) scored on a held-out corpus -- the kill-condition measurement."""
    texts, labels, _ = _calibration(domain)
    floor, cal = calibrated_floor(texts, labels, cfg, domain.target)
    c_texts, c_labels, c_splits = _corpus_rows(domain, corpus)
    return [_row(domain, floor, cal, t, lab, sp) for t, lab, sp in zip(c_texts, c_labels, c_splits)]


def _fold_floor(texts, labels, i, cfg, target, margins):
    """The floor that scores fold ``i``: nested recalibration when ``margins``
    is None, else calibrated from the other sentences' single-level LOO margins."""
    train_t, train_l = texts[:i] + texts[i + 1:], labels[:i] + labels[i + 1:]
    if margins is None:
        return calibrated_floor(train_t, train_l, cfg, target)
    rest = margins[:i] + margins[i + 1:]
    cal = calibrate_tau([m for m, lab in zip(rest, train_l) if lab == target],
                        [m for m, lab in zip(rest, train_l) if lab != target], sigma=cfg.sigma)
    floor = make_floor(cfg).fit(train_t, train_l)
    floor.tau = cal.tau
    return floor, cal


def _joined(domain: Domain, corpora: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    """Calibration set followed by the named corpora, as one (texts, labels, splits)."""
    texts, labels, splits = _calibration(domain)
    for name in corpora:
        t, l, s = _corpus_rows(domain, name)
        texts, labels, splits = texts + t, labels + l, splits + s
    return texts, labels, splits


def score_retrained(domain: Domain, cfg: Config, corpus: str, nested: bool = False) -> list[Row]:
    """Leave-one-out over the calibration set and ``corpus`` together -- does a
    floor fitted on the deployment distribution hold? ``nested`` recalibrates
    tau inside every fold; the default calibrates each fold from the other
    sentences' single-level LOO margins and reaches the same verdict faster."""
    texts, labels, splits = _joined(domain, [corpus])
    margins = None if nested else loo_margins(texts, labels, cfg, domain.target)
    rows = []
    for i, text in enumerate(texts):
        floor, cal = _fold_floor(texts, labels, i, cfg, domain.target, margins)
        rows.append(_row(domain, floor, cal, text, labels[i], splits[i]))
    return rows


def score_feature_axis(domain: Domain, corpus: str, nested: bool = False,
                       axis: Sequence[Config] = FEATURE_AXIS) -> list[tuple[Config, Summary, list]]:
    """Every cell of the feature axis under the retrained protocol, plus
    ``novel`` from the full fit; returns (config, summary, novel_hits) per cell."""
    out = []
    texts, labels, _ = _joined(domain, [corpus])
    for cfg in axis:
        rows = score_retrained(domain, cfg, corpus, nested=nested)
        summary = summarise(rows, cfg, f"all-{len(texts)}", domain.target)
        floor, _cal = calibrated_floor(texts, labels, cfg, domain.target)
        novel = [t for t in domain.novel if floor.classify(t).label == domain.target]
        out.append((cfg, summary, novel))
    return out


def reading(fp: int, n: int, reject_at: int) -> str:
    """The pre-registered reading of a frozen-tau false-positive count."""
    if n == 0:
        return "no corpus"
    if fp == 0:
        return f"0 false positives in {n}: rate bounded below {3 / n:.1%} at 95% (rule of three)"
    if fp >= reject_at:
        return f"{fp} false positives in {n}: a 1% rate is rejected -- the lead is killed"
    return f"{fp} false positives in {n}: inside the band the clause left open; inconclusive"


def score_frozen(domain: Domain, cfg: Config, fit_corpora: Sequence[str], fresh: str) -> dict:
    """The falsification clause, run as written: fit ``cfg`` on the calibration
    set plus ``fit_corpora``, FREEZE tau at that full-fit calibration, score the
    ``fresh`` corpus with no per-fold recalibration, and read the count against
    ``domain.reject_at``. Returns a JSON-ready dict."""
    texts, labels, _ = _joined(domain, fit_corpora)
    floor, cal = calibrated_floor(texts, labels, cfg, domain.target)
    f_texts, f_labels, f_splits = _corpus_rows(domain, fresh)
    rows = [_row(domain, floor, cal, t, lab, sp) for t, lab, sp in zip(f_texts, f_labels, f_splits)]
    fp = [r for r in rows if r.label == domain.other and r.semantic]
    pos = [r for r in rows if r.label == domain.target]
    return {
        "domain": domain.name, "config": cfg.name, "frozen_tau": cal.tau,
        "fit_corpora": list(fit_corpora), "fit_size": len(texts), "fresh_corpus": fresh,
        "negatives": len(rows) - len(pos), "positives": len(pos),
        "literal_fp": sum(r.literal for r in rows if r.label == domain.other), "semantic_fp": len(fp),
        "semantic_recall": sum(r.semantic for r in pos), "literal_recall": sum(r.literal for r in pos),
        "fp_sentences": [r.text for r in fp], "fp_margins": [round(r.margin, 3) for r in fp],
        "reading": reading(len(fp), len(rows) - len(pos), domain.reject_at),
        "mcnemar": literal_vs_semantic(rows, domain.target),
        "rows": [r._asdict() for r in rows],
    }


# ---------------------------------------------------------------------------
# paired McNemar
# ---------------------------------------------------------------------------

def mcnemar_exact(b: int, c: int) -> dict:
    """Exact two-sided McNemar test on the discordant pairs of a paired
    comparison: ``b`` items caught by the second floor and missed by the first,
    ``c`` the other way. Under the null each discordant pair is a fair coin, so
    the p-value is the two-sided binomial tail at min(b, c) of n = b + c."""
    n = b + c
    if n == 0:
        return {"b": b, "c": c, "n": 0, "p_two_sided": 1.0}
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return {"b": b, "c": c, "n": n, "p_two_sided": min(1.0, 2 * tail)}


def paired_mcnemar(hits_a: Sequence[bool], hits_b: Sequence[bool]) -> dict:
    """McNemar over two hit vectors on the SAME items: b = B caught, A missed."""
    if len(hits_a) != len(hits_b):
        raise ValueError("paired comparison needs the same items in the same order")
    b = sum(1 for a, bb in zip(hits_a, hits_b) if bb and not a)
    c = sum(1 for a, bb in zip(hits_a, hits_b) if a and not bb)
    return mcnemar_exact(b, c)


def literal_vs_semantic(rows: Sequence[Row], target: str) -> dict:
    """Recall comparison on the positives: does the semantic floor catch
    positives the literal floor misses more often than the reverse?"""
    pos = [r for r in rows if r.label == target]
    return paired_mcnemar([r.literal for r in pos], [r.semantic for r in pos])


# ---------------------------------------------------------------------------
# summaries and the full run
# ---------------------------------------------------------------------------

def _hits(rows: Sequence[Row]) -> tuple[int, int, int]:
    """(literal, semantic, union) fire counts over ``rows``."""
    return (sum(r.literal for r in rows),
            sum(r.semantic for r in rows),
            sum(r.literal or r.semantic for r in rows))


def _semantic_only(rows: Sequence[Row]) -> list[str]:
    """Sentences the semantic floor fired on and the literal floor did not."""
    return [r.text for r in rows if r.semantic and not r.literal]


def summarise(rows: Sequence[Row], cfg: Config, name: str, target: str) -> Summary:
    """Totals over ``rows``; CONFIRM iff a gap is closed and no false positive is added."""
    pos = [r for r in rows if r.label == target]
    neg = [r for r in rows if r.label != target]
    lit_rec, sem_rec, uni_rec = _hits(pos)
    lit_fp, sem_fp, uni_fp = _hits(neg)
    gap, added = _semantic_only(pos), _semantic_only(neg)
    confirmed = bool(gap) and uni_fp == lit_fp
    return Summary(f"{name} {cfg.name}".strip(), len(pos), len(neg), lit_rec, sem_rec, uni_rec,
                   lit_fp, sem_fp, uni_fp, gap, added, confirmed)


def run_protocol(domain: Domain, cfg: Config = DEFAULT, *, fresh: Optional[str] = None,
                 fit_corpora: Sequence[str] = (), axis: Sequence[Config] = FEATURE_AXIS,
                 nested: bool = False) -> dict:
    """The whole protocol for one domain and one configuration, JSON-ready.

    ``calibration`` is the nested leave-one-out over the calibration set;
    ``shipped`` scores the calibration-fitted floor on every corpus that is
    not the fresh one; ``feature_axis`` runs the axis on the first such corpus;
    ``frozen`` is the gate, on ``fresh`` after fitting on ``fit_corpora``.
    """
    cal_rows = score_calibration(domain, cfg)
    held = [name for name in domain.corpora if name != fresh]
    shipped = {name: summarise(score_shipped(domain, cfg, name), cfg, f"{name} shipped-floor",
                               domain.target)._asdict() for name in held}
    out = {
        "domain": domain.name, "target": domain.target, "config": cfg.name,
        "calibration": summarise(cal_rows, cfg, "calibration", domain.target)._asdict(),
        "calibration_rows": [r._asdict() for r in cal_rows],
        "shipped": shipped,
        "mcnemar_calibration": literal_vs_semantic(cal_rows, domain.target),
    }
    if held:
        out["feature_axis"] = [{**sm._asdict(), "novel_caught": nv}
                               for _c, sm, nv in score_feature_axis(domain, held[0], nested, axis)]
    if fresh is not None:
        out["frozen"] = score_frozen(domain, cfg, fit_corpora, fresh)
    return out
