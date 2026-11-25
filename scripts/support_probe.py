#!/usr/bin/env python3
"""scripts/support_probe.py -- the Phase 3.4 probe of the Jev support reading, against the
pre-registered corpus and nothing else.

lib/prove_it_decide.py asks a decision model (TypeSafe's Jev, through HAL decide)
whether a quote supports a claim, contradicts it, or says nothing about it, and lets
a confident reading settle a citation. Whether that reading can be trusted for
internal use is a measurement, not an assumption. This script runs
prove_it_decide.support_reading over every pair in
docs/measurements/2026-10-11-support-probe-corpus.json, whose labels are fixed by
construction, and reports:

  accuracy          the share of all pairs whose most probable verdict equals the
                    label; a pair with no reading counts as wrong
  review rate       the share of all pairs that are NOT a confident "supports";
                    also per label. This is the registered metric: how often the
                    reading declines to vouch for a quote. It is NOT the share
                    production would send to the council: lib/prove_it_decide.py
                    settles every confident verdict (a confident contradicts or
                    says_nothing becomes "unsupported"), so the council sees only
                    the unconfident readings, reported separately as
  council rate      the share of all pairs with no confident verdict at all, which
                    is what production would still hand to the council
  miss rate         among pairs labelled contradicts or says_nothing, the share that
                    come back as a confident "supports": the direction in which a
                    wrong reading does harm

and the verdict of the rule the corpus file registered before any live run:
PROCEED (internal use, labelled "precision unmeasured") only if the review rate is
between its two bounds inclusive AND the miss rate is at most its bound; otherwise
CALIBRATE_FIRST. A corpus registered against a different question configuration,
or a different review line (REVIEW_BELOW), than the module now uses gives
QUESTION_CHANGED and no rule verdict, because the rule was not registered for
that configuration.

Every report carries what a later reader needs to replay its verdict: corpus_sha256
(the SHA-256 of the exact corpus bytes read), git_revision (the checkout's commit, or
"" when git cannot say), git_dirty (whether that checkout had uncommitted changes, or
null when unknown; read before and after the implementation is imported and again
after the run, and reported as unknown -- revision "" -- if any reading differs) and model (the answering model id or ids). The printed summary
shows the first 16 hex characters of the corpus digest.

The live run goes through HAL and reaches TypeSafe, at real cost, after the
decision-model privacy floor; it never runs while the test suite's fence
(GRIP_CLASSIFY_NO_DECIDE=1) is set. Tests inject ``decide`` and never reach a model.

Usage:
    bin/grip-py scripts/support_probe.py
    bin/grip-py scripts/support_probe.py --corpus <file> --out <report.json> --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))


def git_revision(cwd: Path = _ROOT) -> str:
    """``git rev-parse HEAD`` of the checkout the probe ran from, or "" when unavailable."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True,
                             text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    rev = out.stdout.strip()
    return rev if out.returncode == 0 and len(rev) in (40, 64) else ""


def git_dirty(cwd: Path = _ROOT) -> Optional[bool]:
    """Whether the checkout has uncommitted changes (``git status --porcelain`` prints
    anything), or None when git cannot say. A dirty tree means git_revision does not
    name the code that ran."""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=str(cwd), capture_output=True,
                             text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


def source_state(cwd: Path = _ROOT) -> tuple:
    """(git_revision, git_dirty), read together. The module reads it before and after
    importing prove_it_decide, and run() reads it again after the model calls: the
    commit a report names must be the one whose code ran, so a sync between any two
    readings makes the report say "unknown" rather than name the wrong commit, and a
    dirty tree is recorded rather than passed off as the commit (Codex, GRIP#8000 and
    GRIP#8006)."""
    return git_revision(cwd), git_dirty(cwd)


#: An unknown checkout state: what a report carries when the readings disagree.
UNKNOWN_STATE = ("", None)


def settled(*states: tuple) -> tuple:
    """The shared state when every reading agrees, else UNKNOWN_STATE. A sync can land
    between any two readings, so a report may name a commit only when the checkout was
    the same before the implementation was imported, after it, and after the run
    (Codex, GRIP#8006)."""
    return states[0] if states and all(st == states[0] for st in states) else UNKNOWN_STATE


#: The checkout's (revision, dirty) read before AND after the implementation below is
#: imported, settled: the commit when both agree, unknown when a sync landed in between.
_STATE_BEFORE_IMPORT = source_state()

import prove_it_decide as pd  # noqa: E402

SOURCE_STATE_AT_LOAD = settled(_STATE_BEFORE_IMPORT, source_state())

DEFAULT_CORPUS = _ROOT / "docs" / "measurements" / "2026-10-11-support-probe-corpus.json"
PROCEED, CALIBRATE_FIRST, QUESTION_CHANGED = "PROCEED", "CALIBRATE_FIRST", "QUESTION_CHANGED"
LABELS = ("supports", "contradicts", "says_nothing")
_FENCE = ("GRIP_CLASSIFY_NO_DECIDE", "1")


def read_corpus(path: Path) -> tuple:
    """The validated corpus and the SHA-256 of the exact bytes it was parsed from.

    The name alone cannot replay a stored verdict: a corpus edited after the run keeps
    its name, so every report also carries this digest (Codex, GRIP#7995)."""
    raw = Path(path).read_bytes()
    return _validated(json.loads(raw.decode("utf-8")), path), hashlib.sha256(raw).hexdigest()


def load_corpus(path: Path) -> dict:
    return read_corpus(path)[0]


def _validated(corpus: dict, path) -> dict:
    pairs = corpus.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError(f"{path}: no pairs")
    for p in pairs:
        missing = [k for k in ("id", "claim", "quote", "source_url", "label") if not p.get(k)]
        if missing or p["label"] not in LABELS:
            raise ValueError(f"{path}: pair {p.get('id')!r} is malformed ({missing or p['label']!r})")
    rule = corpus.get("decision_rule") or {}
    for key in ("review_rate_min", "review_rate_max", "miss_rate_max"):
        if not isinstance(rule.get(key), (int, float)):
            raise ValueError(f"{path}: decision_rule.{key} is not registered")
    return corpus


def _read(pair: dict, decide: Callable, cwd: Optional[str]) -> dict:
    reading = pd.support_reading(pair["claim"], pair["quote"], cwd=cwd, decide=decide)
    confident_supports = reading.confident and reading.verdict == "supports"
    return {"id": pair["id"], "label": pair["label"], "construction": pair.get("construction", ""),
            "verdict": reading.verdict, "confident": reading.confident,
            "probabilities": dict(reading.probabilities), "model": reading.model,
            "skipped": reading.skipped, "correct": reading.verdict == pair["label"],
            "review": not confident_supports,
            "council": not reading.confident,
            "miss": pair["label"] != "supports" and confident_supports}


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def _share(rows: list, flag: str) -> Optional[float]:
    return _rate(sum(r[flag] for r in rows), len(rows))


def _per_label(rows: list, flag: str) -> Dict[str, Optional[float]]:
    return {label: _share([r for r in rows if r["label"] == label], flag) for label in LABELS}


def _verdict(rule: dict, registered: bool, review_rate: float, miss_rate: float) -> str:
    """The registered rule, or no rule verdict when the registration no longer matches."""
    if not registered:
        return QUESTION_CHANGED
    within = rule["review_rate_min"] <= review_rate <= rule["review_rate_max"]
    return PROCEED if within and miss_rate <= rule["miss_rate_max"] else CALIBRATE_FIRST


def run(corpus: dict, decide: Callable = pd._default_decide, cwd: Optional[str] = None, *,
        corpus_sha256: str = "", revision: Optional[str] = None,
        dirty: Optional[bool] = None) -> dict:
    """Read every pair with ``decide`` and apply the corpus's registered rule.

    ``corpus_sha256`` is the digest of the corpus bytes (read_corpus). ``revision`` and
    ``dirty`` describe the checkout; when ``revision`` is not given they come from
    SOURCE_STATE_AT_LOAD and are read once more after the run, because prove_it_decide
    imports further modules lazily during it. Any disagreement makes them unknown."""
    measured = revision is None
    rows = [_read(p, decide, cwd) for p in corpus["pairs"]]
    if measured:
        revision, dirty = settled(SOURCE_STATE_AT_LOAD, source_state())
    models = sorted({r["model"] for r in rows if r["model"]})
    harmful = [r for r in rows if r["label"] != "supports"]
    rule = corpus["decision_rule"]
    review_rate, miss_rate = _share(rows, "review"), _share(harmful, "miss")
    question_matches = corpus.get("question_sha256") == pd.QUESTION_SHA256
    # The review line is part of the registered configuration too: the same
    # probabilities classify differently at 0.70, and the bounds were not
    # registered for that (Codex, GRIP#7995).
    review_below_matches = corpus.get("review_below") == pd.REVIEW_BELOW
    return {
        "corpus": corpus.get("name", ""),
        "corpus_sha256": corpus_sha256,
        "git_revision": revision,
        "git_dirty": dirty,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question_sha256": pd.QUESTION_SHA256,
        "question_matches_corpus": question_matches,
        "review_below": pd.REVIEW_BELOW,
        "review_below_matches_corpus": review_below_matches,
        "pairs": len(rows),
        "models": models,
        "model": ",".join(models),
        "no_reading": sum(1 for r in rows if not r["verdict"]),
        "skipped": dict(Counter(r["skipped"] for r in rows if r["skipped"])),
        "accuracy": _share(rows, "correct"),
        "accuracy_per_label": _per_label(rows, "correct"),
        "review_rate": review_rate,
        "review_rate_per_label": _per_label(rows, "review"),
        "council_rate": _share(rows, "council"),
        "miss_rate": miss_rate,
        "miss_rate_denominator": len(harmful),
        "rule": {k: rule[k] for k in ("review_rate_min", "review_rate_max", "miss_rate_max")},
        "verdict": _verdict(rule, question_matches and review_below_matches, review_rate, miss_rate),
        "precision_ref": "unmeasured",
        "readings": rows,
    }


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(report: dict) -> str:
    rule = report["rule"]
    registered = report["question_matches_corpus"] and report["review_below_matches_corpus"]
    lines = [
        f"support probe: {report['pairs']} pairs, corpus {report['corpus']} "
        f"(sha256 {report['corpus_sha256'][:16] or 'unrecorded'}), "
        f"revision {report['git_revision'][:12] or 'unknown'}"
        f"{' (DIRTY tree)' if report['git_dirty'] else (' (tree state unknown)' if report['git_dirty'] is None else '')}, "
        f"question {report['question_sha256'][:16]} at review line {report['review_below']:.2f} "
        f"({'matches' if registered else 'DIFFERS FROM'} the corpus registration)",
        f"  models: {', '.join(report['models']) or 'none answered'}; no reading: {report['no_reading']}"
        + (f" ({report['skipped']})" if report["skipped"] else ""),
        f"  accuracy:    {_pct(report['accuracy'])}  "
        + "  ".join(f"{k} {_pct(v)}" for k, v in report["accuracy_per_label"].items()),
        f"  review rate: {_pct(report['review_rate'])}  (rule: {_pct(rule['review_rate_min'])} to {_pct(rule['review_rate_max'])})  "
        + "  ".join(f"{k} {_pct(v)}" for k, v in report["review_rate_per_label"].items()),
        f"  council rate: {_pct(report['council_rate'])}  (no confident verdict; what production would still send to the council)",
        f"  miss rate:   {_pct(report['miss_rate'])} of {report['miss_rate_denominator']} harmful-direction pairs  "
        f"(rule: at most {_pct(rule['miss_rate_max'])})",
        f"  verdict: {report['verdict']}  (precision {report['precision_ref']})",
    ]
    return "\n".join(lines)


def _fenced() -> bool:
    return os.environ.get(_FENCE[0], "") == _FENCE[1]


def main(argv: Optional[list] = None, decide: Optional[Callable] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--out", type=Path, help="write the full report, readings included, as JSON")
    ap.add_argument("--json", action="store_true", help="print the report as JSON instead of text")
    args = ap.parse_args(argv)
    if decide is None and _fenced():
        print(f"support probe: {_FENCE[0]}={_FENCE[1]} is set, so no live decide call is made; "
              "unset it to run the probe", file=sys.stderr)
        return 2
    corpus, digest = read_corpus(args.corpus)
    report = run(corpus, decide=decide or pd._default_decide, corpus_sha256=digest)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["verdict"] == PROCEED else 1


if __name__ == "__main__":
    sys.exit(main())
