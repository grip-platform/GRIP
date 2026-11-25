#!/usr/bin/env python3
"""scripts/support_calibration.py -- the Phase 3.4 /dfi-fib calibration loop for the Jev
support reading (lib/prove_it_decide.py), on the harder corpus
docs/measurements/2026-10-11-support-calibration-corpus.json (H810).

lib/prove_it_decide.py asks one fixed question, with one fixed set of options and one
fixed review line (REVIEW_BELOW = 0.80). Whether that exact configuration is the right
one is a measurement, not an assumption -- and this script never changes it: QUESTIONS,
OPTIONS and REVIEW_BELOW in lib/prove_it_decide.py are read-only inputs here.

The loop steps through a FIXED, ordered list of twelve configurations -- the live
question at review thresholds 0.80/0.85/0.90/0.95, then an alternative wording with a
stricter meaning of "supports" at the same four thresholds, then a four-option wording
(an added "partial" option, mapped to unsupported) at the same four thresholds -- via
lib.improve_kernel.InnerspeakerGo (direct-first bias, Fibonacci depth cap 13, fixed-point
exit). Each wording needs only ONE live HAL-decide call per development pair (both option
orders, in one request): the four thresholds it is swept at are read off the SAME cached
probabilities, since a review line is a cutoff on a probability this script already has.

Selection happens on the DEVELOPMENT half only, against four targets (precision of a
confident "supports", the contradicts-labelled top-verdict share, and H809's miss-rate and
review-rate bars). The chosen configuration is then read ONCE on the HELD-OUT half; that
run is what gets reported and what settles H810 (via hypothesis_engine.verify, since the
metric has no built-in collector).

Data limit and privacy floor: every claim/quote pair goes through
lib.decision_privacy.may_send before any call, exactly as lib.prove_it_decide does.

Usage:
    bin/grip-py scripts/support_calibration.py
    bin/grip-py scripts/support_calibration.py --corpus <file> --out <report.json> --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import decision_privacy  # noqa: E402  -- the same privacy floor prove_it_decide uses
import prove_it_decide as pd  # noqa: E402  -- read-only: QUESTIONS/OPTIONS/REVIEW_BELOW
from improve_kernel import ImproveResult, InnerspeakerGo, fingerprint  # noqa: E402

DEFAULT_CORPUS = _ROOT / "docs" / "measurements" / "2026-10-11-support-calibration-corpus.json"
DEFAULT_FIXED_POINTS = _ROOT / "data" / "support-calibration-fixed-points.jsonl"
DEFAULT_TELEMETRY = _ROOT / "logs" / "support-calibration-kernel.jsonl"
#: The live review line, read from lib.prove_it_decide at import time, first -- then the
#: higher lines from the plan. A `sorted(set(...))` so a future change to the live line
#: can never produce a duplicate or an out-of-order sweep (AGORA, deepseek-v4-flash,
#: PR #8025): a hardcoded 0.80 here would silently stop meaning "the live line" the day
#: REVIEW_BELOW changes, and this script is meant to be re-run for later calibrations.
THRESHOLDS = tuple(sorted({pd.REVIEW_BELOW, 0.85, 0.90, 0.95}))
TARGETS = {"precision_supports_min": 0.95, "contradicts_top_verdict_min": 0.85,
           "miss_rate_max": 0.10, "review_rate_min": 0.10, "review_rate_max": 0.90}
_FENCE = ("GRIP_CLASSIFY_NO_DECIDE", "1")
_Z95 = 1.959963984540054  # Phi^-1(0.975): the Wilson 95% interval's z

Decide = Callable[[str, dict], Optional[dict]]


# ---------------------------------------------------------------------------
# Configurations: question wording x review threshold
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Wording:
    name: str
    question: str
    options: Dict[str, str]


LIVE = Wording("live", pd._QUESTION, dict(pd.OPTIONS))
STRICT_SUPPORTS = Wording(
    "strict_supports", pd._QUESTION,
    {"supports": "the quote, by itself, fully and precisely establishes what the claim "
                 "asserts, with no missing exception, qualifier or scope limit",
     "contradicts": pd.OPTIONS["contradicts"], "says_nothing": pd.OPTIONS["says_nothing"]})
FOUR_OPTION = Wording(
    "four_option", pd._QUESTION,
    {"supports": pd.OPTIONS["supports"], "contradicts": pd.OPTIONS["contradicts"],
     "says_nothing": pd.OPTIONS["says_nothing"],
     "partial": "the quote backs only part of what the claim asserts, or a narrower or "
                "weaker version of it than the claim actually states"})
#: Fixed order, written down before any live call (direct-first: the live wording first,
#: swept through every threshold, before either alternative is tried at all).
WORDINGS = (LIVE, STRICT_SUPPORTS, FOUR_OPTION)
CONFIGS = tuple(f"{w.name}@{t:.2f}" for w in WORDINGS for t in THRESHOLDS)
_WORDING_BY_NAME = {w.name: w for w in WORDINGS}


def wording_of(config_name: str) -> Wording:
    return _WORDING_BY_NAME[config_name.rsplit("@", 1)[0]]


def threshold_of(config_name: str) -> float:
    return float(config_name.rsplit("@", 1)[1])


# ---------------------------------------------------------------------------
# One wording's order-balanced reading (generalised lib.prove_it_decide.support_reading)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RawReading:
    """A wording's probabilities for one pair, before any threshold is applied -- the
    threshold sweep below reuses this across all four review lines for free."""
    probabilities: Dict[str, float] = field(default_factory=dict)
    model: str = ""
    skipped: str = ""

    @property
    def verdict(self) -> str:
        return max(self.probabilities, key=self.probabilities.get) if self.probabilities else ""


def _choice(wording: Wording, order) -> dict:
    return {"type": "choice", "instructions": wording.question,
            "criteria": {v: wording.options[v] for v in order}}


def _questions(wording: Wording) -> dict:
    names = list(wording.options)
    return {"forward": _choice(wording, names), "reversed": _choice(wording, names[::-1])}


def _complete(options, answer) -> Dict[str, float]:
    """lib.prove_it_decide._complete, generalised over ``options`` instead of the live
    module's fixed OPTIONS, so a four-option wording validates the same way."""
    probs = answer.get("probabilities") if isinstance(answer, dict) else None
    probs = probs if isinstance(probs, dict) else {}
    valid = {v: float(probs[v]) for v in options
             if isinstance(probs.get(v), (int, float)) and not isinstance(probs.get(v), bool)
             and 0 <= probs[v] <= 1}
    if len(valid) != len(options) or abs(sum(valid.values()) - 1.0) > pd.DISTRIBUTION_TOLERANCE:
        return {}
    return valid


def _averaged(options, answers: dict) -> tuple:
    readings = [_complete(options, answers.get(q)) for q in ("forward", "reversed")]
    if all(readings):
        return {v: sum(r[v] for r in readings) / len(readings) for v in options}, ""
    return {}, "incomplete_answer" if any(readings) else "no_answer"


def raw_reading(wording: Wording, claim: str, quote: str, *, cwd: Optional[str],
                decide: Decide) -> RawReading:
    """One wording's reading of (claim, quote) -- the same wire call and privacy floor as
    lib.prove_it_decide.support_reading, with the question/options parameterised."""
    state = f"CLAIM: {claim}\nQUOTE: {quote}"
    allowed, why = decision_privacy.may_send(state, cwd=cwd)
    if not allowed:
        return RawReading(skipped=f"privacy_floor:{why}")
    try:
        result = decide(state, _questions(wording))
    except Exception:  # noqa: BLE001 -- a raising decide leaves this pair with no reading
        result = None
    answered = isinstance(result, dict) and result.get("ok") and isinstance(result.get("answers"), dict)
    probs, why = _averaged(wording.options, result["answers"]) if answered else ({}, "no_answer")
    model = str(result.get("model") or "") if isinstance(result, dict) and result.get("ok") else ""
    return RawReading(probs, model, "" if probs else why)


def raw_readings_for(wording: Wording, pairs: list, decide: Decide, *,
                     cwd: Optional[str] = None) -> Dict[str, RawReading]:
    """One raw reading per pair, keyed by pair id -- computed once per wording and reused
    across every threshold in THRESHOLDS."""
    return {p["id"]: raw_reading(wording, p["claim"], p["quote"], cwd=cwd, decide=decide)
            for p in pairs}


# ---------------------------------------------------------------------------
# Metrics at a given threshold, from already-fetched raw readings
# ---------------------------------------------------------------------------

def _confident_supports(raw: RawReading, threshold: float) -> bool:
    return raw.verdict == "supports" and raw.probabilities.get("supports", 0.0) >= threshold


def _row(pair: dict, raw: RawReading, threshold: float) -> dict:
    confident_supports = _confident_supports(raw, threshold)
    return {"id": pair["id"], "label": pair["label"], "verdict": raw.verdict,
            "confident_supports": confident_supports,
            "correct_supports": confident_supports and pair["label"] == "supports",
            "miss": pair["label"] != "supports" and confident_supports,
            "contradicts_hit": pair["label"] == "contradicts" and raw.verdict == "contradicts",
            "review": not confident_supports, "no_reading": not raw.probabilities}


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def metrics_for(pairs: list, raws: Dict[str, RawReading], threshold: float) -> dict:
    """precision of a confident "supports", the contradicts top-verdict share, H809's
    miss rate and review rate, and the raw counts the Wilson interval needs -- all from
    ``raws``, already fetched, at the given ``threshold``."""
    rows = [_row(p, raws[p["id"]], threshold) for p in pairs]
    predicted_supports = [r for r in rows if r["confident_supports"]]
    contradicts_labelled = [r for r in rows if r["label"] == "contradicts"]
    harmful = [r for r in rows if r["label"] != "supports"]
    n_correct = sum(1 for r in predicted_supports if r["correct_supports"])
    return {
        "n": len(rows), "n_confident_supports": len(predicted_supports), "n_supports_correct": n_correct,
        "precision_supports": _rate(n_correct, len(predicted_supports)),
        "n_contradicts_labelled": len(contradicts_labelled),
        "contradicts_top_verdict": _rate(sum(r["contradicts_hit"] for r in contradicts_labelled),
                                         len(contradicts_labelled)),
        "n_harmful": len(harmful), "miss_rate": _rate(sum(r["miss"] for r in harmful), len(harmful)),
        "review_rate": _rate(sum(r["review"] for r in rows), len(rows)),
        "no_reading": sum(r["no_reading"] for r in rows),
    }


def targets_met(m: dict) -> bool:
    p, c = m["precision_supports"], m["contradicts_top_verdict"]
    miss, rev = m["miss_rate"], m["review_rate"]
    return (p is not None and p >= TARGETS["precision_supports_min"]
            and c is not None and c >= TARGETS["contradicts_top_verdict_min"]
            and miss is not None and miss <= TARGETS["miss_rate_max"]
            and rev is not None and TARGETS["review_rate_min"] <= rev <= TARGETS["review_rate_max"])


def wilson_interval(successes: int, n: int, z: float = _Z95) -> tuple:
    """Wilson 95% interval for a binomial proportion (successes out of n). Pure maths, no
    dependency -- (0.0, 1.0) when n is 0, since no data bounds nothing."""
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    adjust = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - adjust) / denom), min(1.0, (centre + adjust) / denom))


# ---------------------------------------------------------------------------
# The DFI-FIB loop: InnerspeakerGo over the fixed CONFIGS list
# ---------------------------------------------------------------------------

def _dev_step(dev_pairs: list, decide: Decide, cwd: Optional[str], trace: list,
             raw_cache: Dict[str, Dict[str, RawReading]]):
    """One InnerspeakerGo converge step: evaluate the config named by ``state`` on the
    development half, recording every step in ``trace``, and advance to the next
    configuration (or stay put at the last one, so the kernel detects a fixed point)."""
    def converge(state: str, depth: int) -> ImproveResult[str]:
        wording, threshold = wording_of(state), threshold_of(state)
        if wording.name not in raw_cache:
            raw_cache[wording.name] = raw_readings_for(wording, dev_pairs, decide, cwd=cwd)
        m = metrics_for(dev_pairs, raw_cache[wording.name], threshold)
        passed = targets_met(m)
        trace.append({"config": state, "depth": depth, "passed": passed, **{
            k: m[k] for k in ("precision_supports", "contradicts_top_verdict", "miss_rate", "review_rate")}})
        idx = CONFIGS.index(state)
        next_state = CONFIGS[idx + 1] if idx + 1 < len(CONFIGS) else state
        metrics = {"precision_supports": m["precision_supports"] or 0.0,
                   "contradicts_top_verdict": m["contradicts_top_verdict"] or 0.0,
                   "miss_rate": 1.0 if m["miss_rate"] is None else m["miss_rate"],
                   "review_rate": m["review_rate"] or 0.0, "targets_met": 1.0 if passed else 0.0}
        return ImproveResult(state=next_state, metrics=metrics, depth=depth,
                             fingerprint=fingerprint(next_state))
    return converge


def run_dfi_fib(dev_pairs: list, decide: Decide, *, cwd: Optional[str] = None,
               fixed_points_path: Path = DEFAULT_FIXED_POINTS,
               telemetry_path: Path = DEFAULT_TELEMETRY) -> tuple:
    """Steps through CONFIGS via InnerspeakerGo. Returns (winning_config_name, dev_trace)."""
    trace: list = []
    raw_cache: Dict[str, Dict[str, RawReading]] = {}
    kernel = InnerspeakerGo(converge=_dev_step(dev_pairs, decide, cwd, trace, raw_cache),
                            threshold={"targets_met": ("ge", 1.0)},
                            fixed_points_path=fixed_points_path, telemetry_path=telemetry_path)
    kernel(CONFIGS[0])
    return trace[-1]["config"], trace


# ---------------------------------------------------------------------------
# Provenance (git state), mirroring scripts/support_probe.py
# ---------------------------------------------------------------------------

def git_revision(cwd: Path = _ROOT) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True,
                             text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    rev = out.stdout.strip()
    return rev if out.returncode == 0 and len(rev) in (40, 64) else ""


def git_dirty(cwd: Path = _ROOT) -> Optional[bool]:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=str(cwd), capture_output=True,
                             text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


# ---------------------------------------------------------------------------
# Corpus loading and the full report
# ---------------------------------------------------------------------------

def read_corpus(path: Path) -> tuple:
    raw = path.read_bytes()
    corpus = json.loads(raw.decode("utf-8"))
    pairs = corpus.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError(f"{path}: no pairs")
    return corpus, hashlib.sha256(raw).hexdigest()


def run(corpus: dict, decide: Decide, *, cwd: Optional[str] = None, corpus_sha256: str = "",
       fixed_points_path: Path = DEFAULT_FIXED_POINTS, telemetry_path: Path = DEFAULT_TELEMETRY) -> dict:
    dev = [p for p in corpus["pairs"] if p["split"] == "dev"]
    held_out = [p for p in corpus["pairs"] if p["split"] == "held_out"]
    rev_before, dirty_before = git_revision(), git_dirty()
    winner, dev_trace = run_dfi_fib(dev, decide, cwd=cwd, fixed_points_path=fixed_points_path,
                                    telemetry_path=telemetry_path)
    wording, threshold = wording_of(winner), threshold_of(winner)
    held_raws = raw_readings_for(wording, held_out, decide, cwd=cwd)
    held = metrics_for(held_out, held_raws, threshold)
    lo, hi = wilson_interval(held["n_supports_correct"], held["n_confident_supports"])
    rev_after, dirty_after = git_revision(), git_dirty()
    settled = (rev_before, dirty_before) == (rev_after, dirty_after)
    models = sorted({r.model for r in held_raws.values() if r.model})
    return {
        "corpus": corpus.get("name", ""), "corpus_sha256": corpus_sha256, "hypothesis_id": "H810",
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_revision": rev_after if settled else "", "git_dirty": dirty_after if settled else None,
        "models": models,
        "winner": winner, "winner_question": wording.question, "winner_options": dict(wording.options),
        "winner_review_below": threshold,
        "dev_trace": dev_trace, "dev_depth_reached": dev_trace[-1]["depth"],
        "dev_targets_met": dev_trace[-1]["passed"],
        "held_out": {**held, "precision_wilson_95": [round(lo, 4), round(hi, 4)]},
        "held_out_targets_met": targets_met(held),
        "targets": TARGETS,
    }


def render(report: dict) -> str:
    h, dt = report["held_out"], report["dev_trace"]
    lo, hi = h["precision_wilson_95"]
    lines = [
        f"support calibration: winner {report['winner']} (dev depth {report['dev_depth_reached']}, "
        f"dev targets met: {report['dev_targets_met']})",
        f"  corpus {report['corpus']} (sha256 {report['corpus_sha256'][:16] or 'unrecorded'}), "
        f"revision {report['git_revision'][:12] or 'unknown'}, models: {', '.join(report['models']) or 'none'}",
        "  dev path: " + " -> ".join(f"{s['config']}{'*' if s['passed'] else ''}" for s in dt),
        f"  held-out precision(supports): {h['precision_supports']} "
        f"[{h['n_supports_correct']}/{h['n_confident_supports']}], Wilson 95% [{lo}, {hi}]",
        f"  held-out contradicts top-verdict: {h['contradicts_top_verdict']} "
        f"(of {h['n_contradicts_labelled']})",
        f"  held-out miss rate: {h['miss_rate']} (of {h['n_harmful']} harmful-label pairs)  "
        f"review rate: {h['review_rate']}",
        f"  held-out targets met: {report['held_out_targets_met']}",
    ]
    return "\n".join(lines)


def _fenced() -> bool:
    return os.environ.get(_FENCE[0], "") == _FENCE[1]


def main(argv: Optional[list] = None, decide: Optional[Decide] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--out", type=Path, help="write the full report as JSON")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fixed-points", type=Path, default=DEFAULT_FIXED_POINTS)
    ap.add_argument("--telemetry", type=Path, default=DEFAULT_TELEMETRY)
    args = ap.parse_args(argv)
    if decide is None and _fenced():
        print(f"support calibration: {_FENCE[0]}={_FENCE[1]} is set, so no live decide call "
              "is made; unset it to run live", file=sys.stderr)
        return 2
    corpus, digest = read_corpus(args.corpus)
    report = run(corpus, decide or pd._default_decide, cwd=str(_ROOT), corpus_sha256=digest,
                fixed_points_path=args.fixed_points, telemetry_path=args.telemetry)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["held_out_targets_met"] else 1


if __name__ == "__main__":
    sys.exit(main())