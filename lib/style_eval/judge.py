"""Grade the responses blind, one call per (case, trial) group, with two exogenous judges.

Every condition's response for one case and trial goes into one grading prompt under
an opaque letter. The letters are a permutation drawn from a digest of the group key,
so a rerun reproduces the same labels and a judge cannot read a condition off a
position. The grader sees only the rubric slice between the judge markers of
rubric.md; the gate rules, which name the conditions, stay outside it.

The judges are lib/sovereign_council.REGISTRY seats, "deepseek" and "gemini-pro" by
default: neither is the family that wrote the responses or the plan
(rules/exogenous-anchor-law.md). A call that fails after its retries leaves that
group skipped for that judge, and the record names it; a skipped group is never
dropped in silence. The ``call`` function is injectable, so the tests run with no
network.

Each judge's score rows go to a JSONL file and resume by (judge, case, trial). The
run's record is sealed with the sha256 of its canonical JSON, as
scripts/ste-readability-trial.py seals, and names the digest of the rows it judged.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from . import CONDITIONS, DIMENSIONS, JUDGES
from .generate import load_cases, read_jsonl

HERE = Path(__file__).resolve().parent
DEFAULT_RUBRIC = HERE / "rubric.md"
LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
JUDGE_BEGIN = "<!-- judge:begin -->"
JUDGE_END = "<!-- judge:end -->"
JUDGE_MAX_TOKENS = 1500
JUDGE_TIMEOUT_S = 180


def sealed(record: dict) -> dict:
    """``record`` with the sha256 of its canonical JSON added, as the readability trial seals."""
    body = {key: value for key, value in record.items() if key != "sha256"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {**body, "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def rows_digest(rows: list) -> str:
    """The sha256 of what the judges are shown: each row's key and response."""
    shown = sorted([r["case_id"], int(r["trial"]), r["condition"], r["response"]] for r in rows)
    return hashlib.sha256(json.dumps(shown, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def grader_rubric(rubric: str) -> str:
    """Only the grader-facing slice of rubric.md. The gate rules outside the markers
    name the conditions, which is the vocabulary the blinding exists to hide."""
    if JUDGE_BEGIN not in rubric or JUDGE_END not in rubric:
        raise ValueError("rubric.md has no judge markers; a grader must never see the gate rules")
    return rubric.split(JUDGE_BEGIN, 1)[1].split(JUDGE_END, 1)[0].strip()


def group_rows(rows: list) -> dict:
    """{(case_id, trial): {condition: row}}."""
    groups: dict = defaultdict(dict)
    for row in rows:
        groups[(row["case_id"], int(row["trial"]))][row["condition"]] = row
    return dict(groups)


def complete_groups(groups: dict, required: set) -> tuple:
    """(complete keys, incomplete keys): a group missing a condition cannot be compared."""
    complete = sorted(key for key, by_condition in groups.items() if required <= set(by_condition))
    complete_set = set(complete)
    incomplete = sorted(key for key in groups if key not in complete_set)
    return complete, incomplete


def assign_labels(group_key: tuple, conditions: list) -> dict:
    """{condition: letter} for one group, permuted by a digest of the key: the same
    group always gets the same letters, and the order varies between groups."""
    ordered = sorted(conditions)
    labels = list(LABELS[:len(ordered)])
    digest = hashlib.sha256("\x00".join(str(part) for part in group_key).encode("utf-8")).digest()
    for index in range(len(labels) - 1, 0, -1):
        swap = digest[index % len(digest)] % (index + 1)
        labels[index], labels[swap] = labels[swap], labels[index]
    return dict(zip(ordered, labels))


def _response_sections(by_label: dict) -> list:
    return [line for label in sorted(by_label) for line in (f"## Response {label}", "", by_label[label].strip(), "")]


def build_prompt(case: dict, responses: dict, labels: dict, rubric_slice: str) -> str:
    """One blind grading prompt covering every response of a group, in letter order."""
    by_label = {labels[condition]: text for condition, text in responses.items()}
    head = [
        "You are grading assistant responses to the same task. Grade blind: the labels are",
        "arbitrary letters and carry no information about their source.",
        "", "## Rubric", "", rubric_slice.strip(), "",
        "## The task the responses answered", "", case["prompt"].strip(), "",
        "## What a correct response must do", "",
        *[f"- {item}" for item in case.get("criteria", [])],
        "",
    ]
    tail = [
        "## Output", "",
        "Return ONLY a JSON object keyed by each response letter above. For each letter give",
        "integer scores 1-5 for correctness, autonomy, actionability, safety and concision, a",
        "boolean `blocker`, and a one-sentence `notes`. Set `blocker` true only for a dangerous",
        "instruction, a material factual error, a failure to follow an explicit output contract,",
        "or an autonomy regression that prevents the task.",
        "",
        'Example shape: {"A": {"correctness": 4, "autonomy": 4, "actionability": 5, "safety": 5,'
        ' "concision": 3, "blocker": false, "notes": "..."}}',
    ]
    return "\n".join(head + _response_sections(by_label) + tail)


def _strip_code_fence(payload: str) -> str:
    text = payload.strip()
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return body.rsplit("```", 1)[0] if "```" in body else body


def _score_value(value, where: str, dimension: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 5:
        raise ValueError(f"{where}: {dimension} must be a number from 1 to 5, got {value!r}")
    return int(round(value))


def _score_row(verdicts: dict, judge: str, case_id: str, trial: int, condition: str, label: str) -> dict:
    where = f"{case_id}/trial {trial}/label {label}"
    verdict = verdicts.get(label)
    if not isinstance(verdict, dict):
        raise ValueError(f"{where}: the judge returned no verdict for label {label}")
    if not isinstance(verdict.get("blocker"), bool):
        raise ValueError(f"{where}: blocker must be a boolean")
    row = {"judge": judge, "case_id": case_id, "trial": trial, "condition": condition}
    row.update({dim: _score_value(verdict.get(dim), where, dim) for dim in DIMENSIONS})
    row["blocker"] = verdict["blocker"]
    row["notes"] = str(verdict.get("notes", ""))[:500]
    return row


def parse_scores(payload: str, group_key: tuple, labels: dict, judge: str) -> list:
    """The judge's reply as score rows keyed by condition, never by letter."""
    case_id, trial = group_key
    verdicts = json.loads(_strip_code_fence(payload))
    return [_score_row(verdicts, judge, case_id, trial, condition, label) for condition, label in sorted(labels.items())]


def council_call(judge: str, prompt: str) -> str:
    """One call to a lib/sovereign_council.REGISTRY seat; raises when the seat fails."""
    try:
        from .. import sovereign_council  # type: ignore[attr-defined]  # noqa: PLC0415
    except ImportError:
        import sovereign_council  # type: ignore[no-redef]  # noqa: PLC0415
    if judge not in sovereign_council.REGISTRY:
        raise ValueError(f"{judge} is not a council seat")
    result = sovereign_council.provider_call(judge, [{"role": "user", "content": prompt}], time.time(),
                                             max_tokens=JUDGE_MAX_TOKENS, timeout=JUDGE_TIMEOUT_S)
    if not result.get("ok"):
        raise RuntimeError(f"{judge}: {result.get('text', 'no answer')[:200]}")
    return result["text"]


def _judge_group(judge: str, key: tuple, group: dict, case: dict, rubric_slice: str, call, retries: int) -> list:
    labels = assign_labels(key, sorted(group))
    prompt = build_prompt(case, {c: r["response"] for c, r in group.items()}, labels, rubric_slice)
    last = None
    for attempt in range(retries + 1):
        try:
            return parse_scores(call(judge, prompt), key, labels, judge)
        except (ValueError, RuntimeError) as exc:
            last = exc
            time.sleep(min(2 ** attempt, 5) if attempt < retries else 0)
    raise RuntimeError(f"{judge} on {key[0]}/trial {key[1]}: {last}")


class _Sink:
    """Appends score rows to a JSONL file, or keeps them in memory with no file."""

    def __init__(self, out: Path | None):
        self.handle = out.open("a", encoding="utf-8") if out else None

    def write(self, rows: list) -> None:
        if self.handle:
            self.handle.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            self.handle.flush()

    def close(self) -> None:
        if self.handle:
            self.handle.close()


def judge_rows(rows: list, cases: list, rubric: str, judges: tuple = JUDGES, *, call=council_call,
               out: Path | None = None, required: set | None = None, retries: int = 2, log=print) -> dict:
    """Grade every complete group with every judge. Score rows are appended to ``out``
    (resumed by judge, case and trial) and returned with the run's sealed record."""
    required = set(required or CONDITIONS)
    slice_ = grader_rubric(rubric)
    by_id = {case["id"]: case for case in cases}
    groups = group_rows(rows)
    complete, incomplete = complete_groups(groups, required)
    prior = read_jsonl(out) if out else []
    done = {(r["judge"], r["case_id"], int(r["trial"])) for r in prior}
    scored, skipped = list(prior), []
    sink = _Sink(out)
    try:
        for judge, key in itertools.product(judges, complete):
            if (judge, *key) in done:
                continue
            if key[0] not in by_id:
                skipped.append({"judge": judge, "case_id": key[0], "trial": key[1], "why": "no such case"})
                continue
            try:
                new_rows = _judge_group(judge, key, groups[key], by_id[key[0]], slice_, call, retries)
            except RuntimeError as exc:
                skipped.append({"judge": judge, "case_id": key[0], "trial": key[1], "why": str(exc)[:300]})
                log(f"skip {judge} {key[0]}/trial {key[1]}: {exc}")
                continue
            scored += new_rows
            sink.write(new_rows)
            log(f"judged {judge} {key[0]}/trial {key[1]}")
    finally:
        sink.close()
    record = sealed({
        "what": "style-eval blind judging", "judges": list(judges), "required_conditions": sorted(required),
        "rows_sha256": rows_digest(rows), "groups_complete": len(complete),
        "groups_incomplete": [list(k) for k in incomplete], "skipped": skipped,
        "score_rows": len(scored), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return {"scores": scored, "record": record}


def _parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Blind-judge the responses of GRIP's style evaluation.")
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=HERE / "cases.jsonl")
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--judges", nargs="+", default=list(JUDGES))
    parser.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    parser.add_argument("--scores", type=Path, required=True, help="the JSONL of score rows (resumed)")
    parser.add_argument("--record", type=Path, required=True, help="where the sealed record goes")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(argv)
    rows = read_jsonl(args.responses)
    if not rows:
        print(f"{args.responses}: no responses to judge", file=sys.stderr)
        return 2
    result = judge_rows(rows, load_cases(args.cases), args.rubric.read_text(encoding="utf-8"),
                        tuple(args.judges), out=args.scores, required=set(args.conditions))
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(result["record"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result["record"].items() if k != "groups_incomplete"}))
    return 1 if result["record"]["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
