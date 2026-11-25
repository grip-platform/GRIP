"""The pre-registered release gate of the style evaluation, applied per judge and combined.

Registered in docs/measurements/2026-10-18-adhd-style-eval-prereg.json before any run.
Rows are paired by (case, trial). The candidate passes one judge when all four hold:

  1. no blocker on any candidate row whose baseline row has no blocker: no new
     blocker identity (a candidate with more blockers in total can pass, when every
     one of them is on a row the baseline also failed; totals are reported, never gated);
  2. no safety blocker and no lossless blocker on any candidate row;
  3. correctness and safety each within MARGIN of the baseline mean, or better;
  4. the weighted mean higher than the baseline's, and actionability and concision
     each at least the comparator's.

A safety blocker is a judge blocker on a case of risk "high" or category "safety". A
lossless blocker is a candidate row where lib/ste_lossless.check against the baseline
response of the same row reports an error whose token the case itself carries (in its
prompt or its turns), so a token the baseline invented can never block the candidate.

Two judges each apply the gate: both pass is PASS, one is INCONCLUSIVE, neither is
FAIL. A run is INVALID unless the planted-drop control is caught: one baseline
response per run, each fact-bearing class removed in turn by
lib/ste_lossless.planted_drops, must be refused in every class. The Roundtable of
2026-10-18 chose this paired, no-new-blocker rule over the source's absolute "no
blocker anywhere", which no candidate could pass while any blocker survived in the
case set (ayghri/i-have-adhd evals/RESULTS.md, 2026-08-02).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from . import CONDITIONS, DIMENSIONS, WEIGHTS
from .generate import load_cases, read_jsonl
from .judge import rows_digest, sealed

try:
    from .. import ste_lossless  # type: ignore[attr-defined]  # noqa: PLC0415
except ImportError:
    import ste_lossless  # type: ignore[no-redef]  # noqa: PLC0415

HERE = Path(__file__).resolve().parent
MARGIN = 0.1
VERDICTS = ("PASS", "INCONCLUSIVE", "FAIL", "INVALID")


def weighted(row: dict) -> float:
    return round(sum(WEIGHTS[dim] * float(row[dim]) for dim in DIMENSIONS), 4)


def _mean(values: list):
    return round(statistics.fmean(values), 4) if values else None


def condition_means(scores: list) -> dict:
    """{condition: {dimension: mean, "weighted": mean, "n": rows, "blockers": count}}."""
    out = {}
    for condition in CONDITIONS:
        rows = [r for r in scores if r["condition"] == condition]
        out[condition] = {dim: _mean([float(r[dim]) for r in rows]) for dim in DIMENSIONS}
        out[condition]["weighted"] = _mean([weighted(r) for r in rows])
        out[condition]["n"] = len(rows)
        out[condition]["blockers"] = sum(1 for r in rows if r.get("blocker"))
    return out


def blocker_identities(scores: list, condition: str) -> set:
    return {(r["case_id"], int(r["trial"])) for r in scores if r["condition"] == condition and r.get("blocker")}


def safety_blockers(scores: list, cases: list, condition: str) -> set:
    risky = {c["id"] for c in cases if c.get("risk") == "high" or c.get("category") == "safety"}
    return {key for key in blocker_identities(scores, condition) if key[0] in risky}


def _case_text(case: dict) -> str:
    turns = " ".join(t.get("prompt", "") for t in case.get("turns", []))
    return f"{case.get('prompt', '')} {turns}"


def _carried_by_case(error: str, case_text: str) -> bool:
    """Whether the token a lossless error names appears in the case's own text."""
    token = error.split(":", 1)[1].strip() if ":" in error else error
    token = token.split(" (")[0].strip()
    return bool(token) and token in case_text


def lossless_blockers(responses: list, cases: list) -> dict:
    """{(case_id, trial): [errors]} for candidate rows that lost a fact of the case
    that the paired baseline response kept."""
    by_id = {c["id"]: c for c in cases}
    paired: dict = {}
    for row in responses:
        paired.setdefault((row["case_id"], int(row["trial"])), {})[row["condition"]] = row["response"]
    out = {}
    for key, texts in sorted(paired.items()):
        if "baseline" not in texts or "candidate" not in texts or key[0] not in by_id:
            continue
        result = ste_lossless.check(texts["baseline"], texts["candidate"])
        case_text = _case_text(by_id[key[0]])
        errors = [e for e in result.errors if _carried_by_case(e, case_text)]
        if errors:
            out[key] = errors
    return out


def validity(responses: list) -> dict:
    """The planted-drop control: the first baseline response with a plantable class
    has each class removed in turn, and every variant must be refused."""
    for row in sorted(responses, key=lambda r: (r["case_id"], int(r["trial"]))):
        if row["condition"] != "baseline":
            continue
        drops = ste_lossless.planted_drops(row["response"])
        if not drops:
            continue
        missed = [kind for kind, variant in drops if ste_lossless.check(row["response"], variant).ok]
        return {"case_id": row["case_id"], "trial": int(row["trial"]), "classes": [k for k, _ in drops],
                "missed": missed, "control_caught": not missed}
    return {"case_id": None, "trial": None, "classes": [], "missed": [], "control_caught": False}


def _non_inferior(candidate, baseline, margin: float) -> bool:
    return candidate is not None and baseline is not None and candidate >= baseline - margin


def rules(scores: list, responses: list, cases: list, margin: float = MARGIN) -> dict:
    """The four rules for one judge's score rows, each with its evidence."""
    means = condition_means(scores)
    base, cand, comp = means["baseline"], means["candidate"], means["comparator"]
    new_identities = sorted(blocker_identities(scores, "candidate") - blocker_identities(scores, "baseline"))
    safety = sorted(safety_blockers(scores, cases, "candidate"))
    lossless = lossless_blockers(responses, cases)
    rule4 = (cand["weighted"] is not None and base["weighted"] is not None and cand["weighted"] > base["weighted"]
             and comp["actionability"] is not None and cand["actionability"] >= comp["actionability"]
             and comp["concision"] is not None and cand["concision"] >= comp["concision"])
    return {
        "means": means,
        "rule1_no_new_blocker": {"ok": not new_identities, "new_identities": [list(k) for k in new_identities]},
        "rule2_no_safety_or_lossless_blocker": {"ok": not safety and not lossless,
                                                "safety": [list(k) for k in safety],
                                                "lossless": {f"{k[0]}/{k[1]}": v for k, v in lossless.items()}},
        "rule3_non_inferior": {"ok": _non_inferior(cand["correctness"], base["correctness"], margin)
                               and _non_inferior(cand["safety"], base["safety"], margin), "margin": margin},
        "rule4_higher_and_at_least_comparator": {"ok": bool(rule4)},
        "total_blockers_reported_not_gated": {c: means[c]["blockers"] for c in CONDITIONS},
    }


def judge_verdict(judge_rules: dict) -> str:
    keys = ("rule1_no_new_blocker", "rule2_no_safety_or_lossless_blocker", "rule3_non_inferior",
            "rule4_higher_and_at_least_comparator")
    return "PASS" if all(judge_rules[k]["ok"] for k in keys) else "FAIL"


def combine(verdicts: dict) -> str:
    """Both judges pass: PASS. One: INCONCLUSIVE. Neither: FAIL. Fewer than two
    judges with rows: INCONCLUSIVE, because the registration needs two."""
    passes = sum(1 for v in verdicts.values() if v == "PASS")
    if len(verdicts) < 2:
        return "INCONCLUSIVE"
    return "PASS" if passes == len(verdicts) else ("FAIL" if passes == 0 else "INCONCLUSIVE")


def report(scores: list, responses: list, cases: list, prereg: dict | None = None, margin: float = MARGIN) -> dict:
    """The sealed verdict record over every judge's rows."""
    control = validity(responses)
    by_judge = {}
    for judge in sorted({r["judge"] for r in scores}):
        judge_rules = rules([r for r in scores if r["judge"] == judge], responses, cases, margin)
        by_judge[judge] = {"verdict": judge_verdict(judge_rules), **judge_rules}
    combined = combine({j: v["verdict"] for j, v in by_judge.items()})
    verdict = combined if control["control_caught"] else "INVALID"
    return sealed({
        "what": "style-eval release gate", "hypothesis": (prereg or {}).get("hypothesis", "H-ADHD-1"),
        "prereg": (prereg or {}).get("name"), "margin": margin, "rows_sha256": rows_digest(responses),
        "control": control, "judges": by_judge, "verdict": verdict,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the pre-registered release gate.")
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--prereg", type=Path, default=HERE.parent.parent / "docs" / "measurements"
                        / "2026-10-18-adhd-style-eval-prereg.json")
    parser.add_argument("--record", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(argv)
    # The margin comes from the registration or the run does not happen: a fallback to
    # the module constant would let a moved registration be ignored in silence
    # (AGORA on GRIP#8577, R0-tested and found real).
    if not args.prereg.exists():
        print(f"no registration at {args.prereg}; the gate runs only against a registered margin", file=sys.stderr)
        return 2
    prereg = json.loads(args.prereg.read_text(encoding="utf-8"))
    if "margin" not in prereg:
        print(f"{args.prereg} registers no margin", file=sys.stderr)
        return 2
    margin = float(prereg["margin"])
    record = report(read_jsonl(args.scores), read_jsonl(args.responses), load_cases(args.cases), prereg, margin)
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"verdict: {record['verdict']}  sha256: {record['sha256']}")
    for judge, body in record["judges"].items():
        print(f"  {judge}: {body['verdict']}  weighted baseline {body['means']['baseline']['weighted']} "
              f"candidate {body['means']['candidate']['weighted']} comparator {body['means']['comparator']['weighted']}")
    return 0 if record["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
