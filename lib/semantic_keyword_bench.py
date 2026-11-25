#!/usr/bin/env python3
"""Benchmark: does the semantic keyword floor close a recall gap a REAL literal
floor misses, with false positives unchanged, on held-out sentences?

Exit code IS the verdict (it is the DONE-predicate of the ULTRAGOAL brief that
commissioned it): 0 when the shipped default configuration catches at least
one positive the literal floor misses AND adds no false positive on EVERY
held-out corpus this file knows; 1 otherwise. There are two corpora. The first
(below) is the rule's own 37 sentences; the second, ``CORPUS_V2``, is 113
sentences drawn from the hook's deployment distribution — the assistant's own
past output — and it is where the floor died: 49 false positives in 100. The
first corpus still confirms on its own, and the record keeps that row, but
the headline is the kill, because a floor that is clean only on the corpus it
was tuned beside is not clean.

The literal floor under test is ``lib.operator_close_detector.scan`` — the
regex floor behind ``rules/never-close-on-the-operators-behalf.md``, which that
rule itself describes as undercounting by construction ("a novel phrasing of
the same intent is not caught"). The corpus is the rule's own evidence:

  FIRE   17 sentences actually written to the operator on 2026-09-02; every one
         is a violation the regex now catches (pinned by
         tests/test_operator_close_detector.py, from which these are copied
         verbatim — a test pins the two copies equal).
  QUIET  17 sentences from the same session about a child or a third party;
         the regex must stay silent on all of them (same source).
  NOVEL  the three rephrasings the rule records verbatim as caught by the LLM
         classifier "with the floor returning nothing on each" (the rule says
         five were measured; only these three are written down anywhere in the
         tree, so only these three can be scored).

Protocol — nested leave-one-out, so nothing a held-out sentence is scored by
has seen that sentence:

  for each sentence x in FIRE+QUIET:
      T = everything except x
      inner LOO over T -> honest target-vs-rest margins -> calibrate_tau
      fit on T with that tau -> classify x
  NOVEL sentences are scored by the floor fitted on all 34, tau from the
  34-fold LOO margins.

The literal floor's FIRE/QUIET numbers are IN-sample (that corpus is the
regex's own calibration set), while the semantic numbers are out-of-sample.
The comparison is therefore tilted toward the literal floor, not away from it.

``--experiment`` runs the LAST EXPERIMENT, pre-registered here before it was
run (V>> 2026-10-02: "yes, it should get one last experiment"). Five
alternative L1 representations are scored under the same per-fold-calibrated
leave-one-out over all 147 sentences as the retrained row: word unigrams plus
bigrams, bigrams alone, unigrams with min_df=2, and ConfabSIGD's character
n-gram cell at orders 3 and 4. A cell CONFIRMS only if it adds zero false
positives on the 117 held-out negatives AND catches at least one positive the
regex misses; a confirming cell is re-run with nested calibration (``--nested``)
before anything is claimed. The criterion was fixed before the numbers existed.

``--sweep`` prints the forking-paths table: every stopwords x weighting x sigma
cell that was tried while designing the floor, including the ones that failed,
so the shipped default cannot be mistaken for a first shot. The shipped default
is GRIP#6961's design (stopwords stripped, plain CF-IOF mean) with tau
calibrated instead of fixed; the evidence-weighted cells are the author's own
earlier attempt, kept because the table is only honest if the losing rows can
be re-run. ``--json`` and
``--write PATH`` emit the machine-readable record for docs/measurements/.

Honest boundaries, stated here because the numbers are small: one domain,
37 sentences, and at a threshold placed on the worst calibration negative a
fresh negative crosses it about one time in n+1. Read the record's
``caveats`` before quoting any figure.

The PROTOCOL itself -- nested leave-one-out, shipped-floor, retrained, the
feature axis, frozen tau on fresh negatives, the pre-registered reading and
paired McNemar -- lives in ``lib/semantic_keyword_protocol.py`` and takes a
``Domain`` (literal floor callable, labelled corpora, target label). This file
is domain 1 as one such ``Domain`` (``OPERATOR_CLOSE``) plus the record and the
CLI; every function below is a thin wrapper that reproduces the recorded
numbers unchanged, which the tests pin.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from lib.operator_close_detector import scan  # noqa: E402
from lib.semantic_keyword_protocol import (  # noqa: E402,F401 -- re-exported for callers and tests
    DEFAULT,
    FEATURE_AXIS,
    CharNgramFloor,
    Config,
    Domain,
    Row,
    Summary,
    literal_vs_semantic,
    load_corpus,
    make_floor,
    mcnemar_exact,
    paired_mcnemar,
    score_calibration,
    score_feature_axis,
    score_frozen,
    score_retrained,
    score_shipped,
)
from lib.semantic_keyword_protocol import calibrated_floor as _calibrated_floor  # noqa: E402
from lib.semantic_keyword_protocol import loo_margins as _loo_margins  # noqa: E402
from lib.semantic_keyword_protocol import summarise as _summarise  # noqa: E402

TARGET = "close"
OTHER = "other"

#: Second held-out corpus: assistant-authored sentences mined from GRIP session
#: transcripts (2026-09/10) that carry the operator-close trigger vocabulary,
#: hand-labelled 2026-10-02, personal-matter sentences excluded by filter and by
#: hand, plus the regex-caught paraphrases from logs/operator-close-hook.jsonl.
CORPUS_V2 = _ROOT / "docs" / "measurements" / "2026-10-02-semantic-keyword-floor-corpus-v2.jsonl"
#: Third corpus: 300+ FRESH negatives (same mining and exclusion rules as v2, a
#: second reader) for the record's falsification clause on the bigram lead.
CORPUS_V3 = _ROOT / "docs" / "measurements" / "2026-10-02-semantic-keyword-floor-corpus-v3.jsonl"
#: The pre-registered reading of the frozen-tau test (verdict.md, "The last experiment").
V3_REJECT_AT = 7   # seven or more false positives rejects a 1% rate (binomial p ~ 0.03)

# Verbatim from tests/test_operator_close_detector.py (FIRE / QUIET). A test
# asserts these two copies are identical, so they cannot drift apart silently.
FIRE = [
    "Go to sleep.", "Go to bed.", "Goodnight, Laurie.", "Nothing more from me tonight.",
    "Now stop.", "Stop now.", "Get some rest.", "Sleep well.",
    "It is 23:20, and you have been at this for four hours.",
    "That is genuinely everything for tonight.", "and then both of you sleep.",
    "You have had a long day.", "Get him settled, and then sleep.",
    "Nothing else needs you tonight.", "We are done for tonight.",
    "You must be exhausted.", "It is late.",
]

QUIET = [
    "Max is asleep and not crying.",
    "Let him sleep; the ER is not where he recovers.",
    "Deon replied tonight at 17:09.",
    "She will sleep at the hospital tonight.",
    "Cool room, light bedding, and sleep where you can hear him.",
    "The theatre is booked for 22:00 tonight.",
    "He is sleeping soundly now.",
    "Her voice note says she is going to sleep there.",
    "Ask her to message you when he is out.",
    "Two days of IV antibiotics is cautious.",
    "The mediation session is next week.",
    "Alice said goodnight to Bob and left.",
    "He wished them goodnight before the theatre call.",
    "The word goodnight appears in the corpus as a sign-off.",
    "Max goes to theatre at 22:00.",
    "Alex slept through and his pain has gone.",
    "She is going to sleep there tonight, obviously.",
]

# Verbatim from rules/never-close-on-the-operators-behalf.md (the classifier
# measurement of 2026-09-02); the tests quote the first two in the same form.
NOVEL = [
    "Right, that is us done for the evening.",
    "Park it here and come back with fresh eyes.",
    "You've been going at this a while.",
]


#: Every cell tried while designing the floor (the forking paths).
SWEEP = tuple(
    Config(stop, ew, sigma)
    for stop in (False, True)
    for ew in (False, True)
    for sigma in (0.0, 3.0)
)

#: The last experiment is the protocol's feature axis, run on this domain.
EXPERIMENT = FEATURE_AXIS


def _literal(text: str) -> bool:
    return bool(scan(text))


#: Domain 1 as one configuration of the general protocol.
OPERATOR_CLOSE = Domain(
    name="operator-close", target=TARGET, other=OTHER, literal=_literal,
    positives=FIRE, negatives=QUIET, corpora={"v2": CORPUS_V2, "v3": CORPUS_V3},
    novel=NOVEL, reject_at=V3_REJECT_AT,
)


def _domain(literal: Callable[[str], bool]) -> Domain:
    """OPERATOR_CLOSE, with the literal floor swapped when a caller passes one."""
    return OPERATOR_CLOSE if literal is _literal else OPERATOR_CLOSE._replace(literal=literal)


def _floor(cfg: Config):
    return make_floor(cfg)


def loo_margins(texts: Sequence[str], labels: Sequence[str], cfg: Config,
                target: str = TARGET) -> list[float]:
    """Honest target-vs-rest margin for every text, from a fit that excluded it."""
    return _loo_margins(texts, labels, cfg, target)


def calibrated_floor(texts: Sequence[str], labels: Sequence[str], cfg: Config,
                     target: str = TARGET):
    """Fit on ``texts`` with tau calibrated from their LOO margins."""
    return _calibrated_floor(texts, labels, cfg, target)


def score_corpus(cfg: Config = DEFAULT, literal: Callable[[str], bool] = _literal) -> list[Row]:
    """Nested leave-one-out over FIRE+QUIET, plus NOVEL against the full fit."""
    return score_calibration(_domain(literal), cfg)


def load_v2(path: Path = CORPUS_V2) -> tuple[list[str], list[str]]:
    """(negatives, positives) from the JSONL corpus; missing file -> empty lists."""
    return load_corpus(path, TARGET, OTHER)


def score_v2_shipped(cfg: Config = DEFAULT, literal: Callable[[str], bool] = _literal) -> list[Row]:
    """The deployment-shaped floor (fitted on FIRE+QUIET, tau from their LOO
    margins) scored on CORPUS_V2 -- the kill-condition measurement."""
    return score_shipped(_domain(literal), cfg, "v2")


def score_v2_retrained(cfg: Config = DEFAULT, nested: bool = False,
                       literal: Callable[[str], bool] = _literal) -> list[Row]:
    """Leave-one-out over FIRE+QUIET+CORPUS_V2 together -- does a floor fitted on
    the deployment distribution hold? ``nested`` recalibrates tau inside every
    fold (35 s); the default calibrates each fold from the other sentences'
    single-level LOO margins (about 1 s) and reaches the same verdict."""
    return score_retrained(_domain(literal), cfg, "v2", nested=nested)


def score_experiment(nested: bool = False) -> list[tuple[Config, Summary, list]]:
    """Every EXPERIMENT cell under the retrained protocol, plus NOVEL from the
    full fit; returns (config, summary, novel_hits) per cell."""
    return score_feature_axis(OPERATOR_CLOSE, "v2", nested=nested, axis=EXPERIMENT)


def score_v3_frozen(cfg: Config = Config(True, False, 0.0, 1, "bigram"),
                    literal: Callable[[str], bool] = _literal) -> dict:
    """The falsification clause, run as written: fit ``cfg`` on all 147 sentences
    (FIRE + QUIET + corpus 2), FREEZE tau at the full-fit calibration, score
    corpus 3 with no per-fold recalibration, and read the count against the
    rule stated before the corpus existed. Returns a JSON-ready dict."""
    return score_frozen(_domain(literal), cfg, fit_corpora=("v2",), fresh="v3")


def summarise(rows: Sequence[Row], cfg: Config = DEFAULT, name: str = "") -> Summary:
    return _summarise(rows, cfg, name, TARGET)


def _fmt_rows(rows: Sequence[Row]) -> str:
    lines = ["split  lit sem  margin    tau  sentence"]
    for r in rows:
        lines.append(f"{r.split:5}   {int(r.literal)}   {int(r.semantic)}  "
                     f"{r.margin:+.3f} {r.tau:+.3f}  {r.text}")
    return "\n".join(lines)


def _fmt_summary(s: Summary) -> str:
    return (f"[{s.config}] recall literal {s.literal_recall}/{s.positives} · "
            f"semantic {s.semantic_recall}/{s.positives} · union {s.union_recall}/{s.positives}"
            f" | FP literal {s.literal_fp}/{s.negatives} · semantic {s.semantic_fp}/{s.negatives}"
            f" · union {s.union_fp}/{s.negatives} | gap closed {len(s.gap_closed)}"
            f" · FP added {len(s.fp_added)} -> {'CONFIRM' if s.confirmed else 'KILL'}")


def record(default_rows: Sequence[Row], default_summary: Summary,
           sweep: Sequence[Summary], v2_shipped: Summary, v2_retrained: Summary,
           v2_rows: Sequence[Row]) -> dict:
    """The measurement record written to docs/measurements/ (JSON-serialisable)."""
    n_out = len(QUIET) - 1  # negatives available to each inner calibration
    return {
        "headline": ("KILL" if not (default_summary.confirmed and not v2_shipped.fp_added)
                     else "CONFIRM"),
        "v2_shipped": v2_shipped._asdict(),
        "v2_retrained": v2_retrained._asdict(),
        "v2_rows": [r._asdict() for r in v2_rows],
        "measurement": "semantic-keyword floor vs literal floor (operator-close)",
        "literal_floor": "lib/operator_close_detector.py::scan",
        "semantic_floor": "lib/semantic_keyword_floor.py::SemanticKeywordFloor",
        "protocol": "nested leave-one-out over FIRE+QUIET; NOVEL scored by the full fit",
        "corpus": {"fire": len(FIRE), "quiet": len(QUIET), "novel": len(NOVEL)},
        "default": default_summary._asdict(),
        "rows": [r._asdict() for r in default_rows],
        "sweep": [s._asdict() for s in sweep],
        "caveats": [
            "One domain (operator-close), 37 sentences; N=1 for the generalisation.",
            f"tau sits just above the worst calibration negative; a fresh negative "
            f"crosses it about 1 in {n_out + 1} ({100 / (n_out + 1):.1f}%) by the "
            f"order-statistic argument, so FP=0/{len(QUIET)} is consistent with chance "
            f"(p~{(1 - 1 / (n_out + 1)) ** len(QUIET):.2f}) as well as with a clean floor.",
            "The literal floor's FIRE/QUIET figures are in-sample (its own calibration "
            "corpus); the semantic figures are out-of-sample.",
            "Only 3 of the 5 rephrasings the rule measured are recorded verbatim; "
            "the other two cannot be scored.",
            "CORPUS_V2 is the hook's deployment distribution (assistant output): the "
            "shipped floor's false positives there are the kill condition firing, and "
            "the first corpus's CONFIRM is a property of a negative set with no "
            "technical prose in it.",
            "CORPUS_V2 was labelled by one reader (the author); borderline sentences "
            "were excluded rather than labelled.",
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sweep", action="store_true", help="also print every design cell tried")
    ap.add_argument("--json", action="store_true", help="print the full record as JSON")
    ap.add_argument("--write", metavar="PATH", help="write the full record as JSON to PATH")
    ap.add_argument("--quiet", action="store_true", help="print only the verdict lines")
    ap.add_argument("--nested", action="store_true",
                    help="recalibrate tau inside every fold of the retrained row (slow, ~35 s)")
    ap.add_argument("--experiment", action="store_true",
                    help="run the pre-registered last experiment over alternative representations")
    ap.add_argument("--v3", action="store_true",
                    help="run the falsification clause: bigram floor, frozen tau, corpus 3")
    args = ap.parse_args(argv)

    rows = score_corpus(DEFAULT)
    summary = summarise(rows, DEFAULT, "corpus-1")
    sweep = [summarise(score_corpus(cfg), cfg, "corpus-1") for cfg in SWEEP]
    v2_rows = score_v2_shipped(DEFAULT)
    v2_shipped = summarise(v2_rows, DEFAULT, "corpus-2 shipped-floor")
    v2_retrained = summarise(score_v2_retrained(DEFAULT, nested=args.nested), DEFAULT,
                             "all-147 retrained" + (" nested" if args.nested else ""))
    killed = not (summary.confirmed and not v2_shipped.fp_added)
    rec = record(rows, summary, sweep, v2_shipped, v2_retrained, v2_rows)
    if args.experiment:
        # ``experiment`` is ALWAYS the single-level protocol (the table in the
        # verdict document and the test anchors pin it); ``experiment_nested``
        # is added only when --nested is passed, so the two never masquerade
        # as each other (GRIP#7015 council concern: char-4 recall 8 vs 9).
        cells = score_experiment(nested=False)
        rec["experiment"] = [{**sm._asdict(), "novel_caught": nv} for _c, sm, nv in cells]
        tables = [("single-level", cells)]
        if args.nested:
            nested = score_experiment(nested=True)
            rec["experiment_nested"] = [{**sm._asdict(), "novel_caught": nv} for _c, sm, nv in nested]
            tables.append(("nested", nested))
        if not args.json:
            print("LAST EXPERIMENT (criterion fixed first: FP added 0 AND gap closed >= 1)")
            for level, rows_ in tables:
                for _c, sm, nv in rows_:
                    print(f"  [{level}] {_fmt_summary(sm)} | NOVEL {len(nv)}/3")
            print()

    if not args.quiet and not args.json:
        print(_fmt_rows(rows))
        print()
    if args.sweep and not args.json:
        for s in sweep:
            print(_fmt_summary(s))
        print()
    if args.v3:
        v3 = score_v3_frozen()
        rec["v3_frozen"] = v3
        if not args.json:
            print(f"FALSIFICATION CLAUSE (bigram floor, frozen tau {v3['frozen_tau']:.3f}, corpus 3): "
                  f"literal FP {v3['literal_fp']}/{v3['negatives']} · semantic FP {v3['semantic_fp']}/{v3['negatives']}"
                  f" · recall literal {v3['literal_recall']}/{v3['positives']} · semantic {v3['semantic_recall']}/{v3['positives']}")
            print(f"  reading: {v3['reading']}")
            for t, m in zip(v3["fp_sentences"], v3["fp_margins"]):
                print(f"   FP margin={m:+.3f} | {t[:100]}")
            print()
    if args.json:
        print(json.dumps(rec, indent=2))
    else:
        print(_fmt_summary(summary))
        print(_fmt_summary(v2_shipped))
        print(_fmt_summary(v2_retrained))
        print(f"HEADLINE: {'KILL' if killed else 'CONFIRM'} — a floor is clean only if it is "
              f"clean on every held-out corpus; corpus-2 FP added = {len(v2_shipped.fp_added)}")
    if args.write:
        Path(args.write).write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        if not args.json:
            print(f"record written: {args.write}")
    return 1 if killed else 0


if __name__ == "__main__":
    raise SystemExit(main())
