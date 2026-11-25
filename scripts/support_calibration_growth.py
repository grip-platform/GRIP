#!/usr/bin/env python3
"""scripts/support_calibration_growth.py -- H815: measure the live support-reading
configuration ONCE on a grown held-out set.

H810 (lib/prove_it_decide.py, unchanged) measured confident-"supports" precision at
15/15 on its held-out half, but with only 15 pairs the Wilson 95% lower bound was
0.7961 -- thin evidence. This script does not search for a configuration: H810's
result already fixed it (the live question, options and review line). It reads the
grown held-out corpus (docs/measurements/2026-10-12-support-calibration-growth-corpus.json
-- 75 supports, 75 overstates, 15 contradicts, 15 says_nothing, no development half) and
measures that one configuration once, reusing scripts/support_calibration.py's reading,
metrics and Wilson-interval helpers rather than reimplementing them.

Usage:
    bin/grip-py scripts/support_calibration_growth.py
    bin/grip-py scripts/support_calibration_growth.py --corpus <file> --out <report.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import prove_it_decide as pd  # noqa: E402  -- read-only: QUESTIONS/OPTIONS/REVIEW_BELOW
import support_calibration as sc  # noqa: E402  -- reused: raw_readings_for/metrics_for/wilson_interval

DEFAULT_CORPUS = _ROOT / "docs" / "measurements" / "2026-10-12-support-calibration-growth-corpus.json"
_FENCE = ("GRIP_CLASSIFY_NO_DECIDE", "1")
_TARGETS = {"precision_wilson_lower_min": 0.95, "contradicts_top_verdict_min": 0.85,
            "miss_rate_max": 0.10, "review_rate_min": 0.10, "review_rate_max": 0.90}

Decide = Callable[[str, dict], Optional[dict]]


def read_corpus(path: Path) -> tuple:
    raw = path.read_bytes()
    corpus = json.loads(raw.decode("utf-8"))
    pairs = corpus.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError(f"{path}: no pairs")
    if any(p.get("split") != "held_out" for p in pairs):
        raise ValueError(f"{path}: H815 is held-out only; a dev-split pair would mean a "
                         "configuration could be chosen on this data, which it must not be")
    return corpus, hashlib.sha256(raw).hexdigest()


def _targets_met(lo: float, m: dict) -> bool:
    c, miss, rev = m["contradicts_top_verdict"], m["miss_rate"], m["review_rate"]
    return (lo >= _TARGETS["precision_wilson_lower_min"]
            and c is not None and c >= _TARGETS["contradicts_top_verdict_min"]
            and miss is not None and miss <= _TARGETS["miss_rate_max"]
            and rev is not None and _TARGETS["review_rate_min"] <= rev <= _TARGETS["review_rate_max"])


def run(corpus: dict, decide: Decide, *, cwd: Optional[str] = None, corpus_sha256: str = "") -> dict:
    """Measure sc.LIVE, at pd.REVIEW_BELOW, once, on every pair in ``corpus`` -- no
    configuration search: the configuration is already fixed by H810's result."""
    pairs = corpus["pairs"]
    rev_before, dirty_before = sc.git_revision(), sc.git_dirty()
    raws = sc.raw_readings_for(sc.LIVE, pairs, decide, cwd=cwd)
    m = sc.metrics_for(pairs, raws, pd.REVIEW_BELOW)
    lo, hi = sc.wilson_interval(m["n_supports_correct"], m["n_confident_supports"])
    rev_after, dirty_after = sc.git_revision(), sc.git_dirty()
    settled = (rev_before, dirty_before) == (rev_after, dirty_after)
    models = sorted({r.model for r in raws.values() if r.model})
    return {
        "hypothesis_id": corpus.get("hypothesis_id", ""), "corpus": corpus.get("name", ""),
        "corpus_sha256": corpus_sha256, "config": f"live@{pd.REVIEW_BELOW:.2f}",
        "git_revision": rev_after if settled else "", "git_dirty": dirty_after if settled else None,
        "models": models, "metrics": {**m, "precision_wilson_95": [round(lo, 4), round(hi, 4)]},
        "targets": _TARGETS, "targets_met": _targets_met(lo, m),
    }


def render(report: dict) -> str:
    m = report["metrics"]
    lo, hi = m["precision_wilson_95"]
    lines = [
        f"support calibration growth: {report['hypothesis_id']} on {report['corpus']} "
        f"(sha256 {report['corpus_sha256'][:16] or 'unrecorded'}), config {report['config']}",
        f"  revision {report['git_revision'][:12] or 'unknown'}, models: {', '.join(report['models']) or 'none'}",
        f"  precision(supports): {m['precision_supports']} [{m['n_supports_correct']}/"
        f"{m['n_confident_supports']}], Wilson 95% [{lo}, {hi}] (bar: >= "
        f"{_TARGETS['precision_wilson_lower_min']})",
        f"  contradicts top-verdict: {m['contradicts_top_verdict']} (of "
        f"{m['n_contradicts_labelled']}, bar: >= {_TARGETS['contradicts_top_verdict_min']})",
        f"  miss rate: {m['miss_rate']} (of {m['n_harmful']} harmful-label pairs, bar: <= "
        f"{_TARGETS['miss_rate_max']})  review rate: {m['review_rate']} (bar: "
        f"{_TARGETS['review_rate_min']}-{_TARGETS['review_rate_max']})",
        f"  targets met: {report['targets_met']}",
    ]
    return "\n".join(lines)


def _fenced() -> bool:
    return os.environ.get(_FENCE[0], "") == _FENCE[1]


def main(argv: Optional[list] = None, decide: Optional[Decide] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--out", type=Path, help="write the full report as JSON")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if decide is None and _fenced():
        print(f"support calibration growth: {_FENCE[0]}={_FENCE[1]} is set, so no live decide "
              "call is made; unset it to run live", file=sys.stderr)
        return 2
    corpus, digest = read_corpus(args.corpus)
    report = run(corpus, decide or pd._default_decide, cwd=str(_ROOT), corpus_sha256=digest)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["targets_met"] else 1


if __name__ == "__main__":
    sys.exit(main())