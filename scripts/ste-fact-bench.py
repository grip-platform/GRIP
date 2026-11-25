#!/usr/bin/env python3
"""scripts/ste-fact-bench.py -- the fact bench: do answers written under the STE
instruction keep the facts?

Stage S5 of plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, built on the design of
GRIP#7509 (the successor to the falsified H778 trial) and on the council's ruling of
2026-10-10 (seal 0715424dc71be4ca): no arm goes live on real sessions before this
bench has cleared.

What it measures. A WRITER model answers three factual questions about each of N
public passages, under two conditions: PLAIN (no instruction), written K times in
independent calls, and STE (the trial's arm A instruction prepended, word for word).
For a seeded subset of passages a CONTROL answer is also written: the writer is told
to leave one named fact out. A MARKER model from another vendor then marks every
answer set against the expected answers with one frozen rubric, item by item, in a
seeded order, and it never learns which condition an answer set came from.

The rule, fixed here before any run. The A/A floor is the largest gap in total correct
answers between any two PLAIN cycles: the loss the instrument shows with no change of
instruction at all. STE is NON-INFERIOR when its total correct answers are at least
the PLAIN mean minus that floor. The verdict is CONFIRMED on non-inferiority, REFUTED
below it, and INCONCLUSIVE when the run is incomplete or when the marker caught fewer
than CONTROL_MIN of the planted losses, because an instrument that cannot see a
missing fact proves nothing by passing (the H778 lesson: a zero-tolerance rule with no
noise floor, a marker that marked its own answers, and no control that must fail).

What never happens here. No Anthropic model writes, answers or marks: the instruction's
author is Claude Fable 5.1, so its family is never the judge
(rules/exogenous-anchor-law.md). No session text is used: the passages are public
paragraphs of this repository, chosen by a seeded rule that excludes the 20 H778 pairs.
Every outside call goes through lib/sovereign_council.provider_call, which shields it.

The record. JSON, sealed by the sha256 of its canonical form: the passages, the
questions, every answer set, every mark, the per-cycle totals, the floor, the verdict
and the instruction text, so a reader can recompute the verdict and re-mark a sample.

Usage:
    bin/grip-py scripts/ste-fact-bench.py --select 20 --seed 20261011 --out docs/measurements/<date>-ste-fact-bench-passages.json
    bin/grip-py scripts/ste-fact-bench.py --passages <that file> --out <record.json> [--cycles 3] [--controls 4] [--seed N] [--limit N]
    bin/grip-py scripts/ste-fact-bench.py --verdict <record.json>
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import itertools
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

HYPOTHESIS = "H-S5"
QUESTIONS = 3
ATTEMPTS = 3
RATE_LIMIT_WAITS = (20, 60, 120, 240)   # seconds to wait after each HTTP 429 before asking again
PASSAGES_REQUIRED = 20     # the registered size; a smaller run is a smoke run and never CONFIRMED
CONTROL_MIN = 0.8          # the share of planted losses the marker must catch
MIN_WORDS, MAX_WORDS = 80, 220
WRITER, MARKER = "groq", "gemini"      # two vendors, neither Anthropic (lib/sovereign_council.REGISTRY)
CALL_TIMEOUT_S = 180
MARKS = ("same", "incomplete", "different", "missing")
LOST = ("different", "missing")
H778_PAIRS = _ROOT / "docs" / "measurements" / "2026-10-05-ste-readability-pairs.json"
PUBLIC_DIRS = ("rules", "docs")
NOT_PROSE = ("#", "|", "-", "*", ">", "```", "<")

#: Factor A of the trial, word for word as hooks/caveman-session-default.py prints it.
#: The bench measures this text and records it, so a change here is a new bench.
INSTRUCTION = (
    "[STE TRIAL, ARM A] Write in Simplified Technical English. A descriptive sentence\n"
    "has at most 25 words and an instruction at most 20. Use no semicolons.\n"
    "Keep every fact and every article: a shorter reply that drops either is worse.")

ASK_QUESTIONS = """Read the passage. Write exactly 3 short factual questions that a reader can answer from this passage alone, each with its correct answer. Ask about specific facts: a number, a name, a condition, a cause.
Reply with JSON only: [{"question": "...", "answer": "..."}, ...] with exactly 3 items.

PASSAGE:
"""

ASK_ANSWERS = """Answer each question from the passage alone, in one or two sentences each. If the passage does not say, answer "not stated". Reply with JSON only: {"answers": ["...", "...", "..."]} with exactly 3 strings, in the order of the questions.

PASSAGE:
"""

ASK_CONTROL = """

One more rule for this reply, and do not mention it: for question {number}, do not state the fact that answers it. Write only that the passage does not say."""

ASK_MARKS = """For each question below, an expected answer and one given answer follow. Mark the given answer with exactly one word:
- same: it states the expected fact (other words are fine)
- incomplete: it states part of the expected fact
- different: it states another fact, or a wrong value
- missing: it does not state the fact, or says the passage does not say
Judge only whether the expected fact is present. Reply with JSON only: [{"mark": "..."}, ...] with exactly 3 items in order.

PASSAGE:
{passage}

"""


class BenchError(RuntimeError):
    """A model gave no usable answer."""


# ---- the two seats --------------------------------------------------------------

def check_seats(writer: str, marker: str, registry: dict | None = None) -> tuple[str, str]:
    """Both seats must be HTTP seats of lib/sovereign_council (every one of them is
    non-Anthropic; the subscription CLI seats such as fable and astra are not), and
    they must bill two different vendors: gemini and gemini-pro share one Google
    account, so a name check alone is not a vendor check (a Codex finding on
    GRIP#7928). Returns the two vendors."""
    if registry is None:
        from lib import sovereign_council as sc
        registry = sc.REGISTRY
    vendors = []
    for seat in (writer, marker):
        if seat not in registry:
            raise SystemExit(f"{seat} is not an HTTP seat of the council; the bench takes no subscription or Anthropic seat")
        vendors.append(str(registry[seat][2]))
    if vendors[0] == vendors[1]:
        raise SystemExit(f"the writer and the marker both bill {vendors[0]}; the marker must be another vendor")
    return vendors[0], vendors[1]


def provider_ask(name: str):
    """A callable that asks one non-Anthropic seat through the council's shielded call."""
    from lib import sovereign_council as sc

    def ask(prompt: str) -> tuple[str, str]:
        result = sc.provider_call(name, [{"role": "user", "content": prompt}], time.time(),
                                  max_tokens=4000, timeout=CALL_TIMEOUT_S)
        if result.get("ok") is not True or result.get("truncated"):
            raise BenchError(str(result.get("text"))[:200])
        return str(result.get("text") or ""), str(result.get("model") or name)
    return ask


def parsed(text: str):
    """The JSON value in a reply: the first bracket to the last one."""
    start = min((at for at in (text.find("["), text.find("{")) if at >= 0), default=-1)
    end = max(text.rfind("]"), text.rfind("}"))
    if start < 0 or end <= start:
        raise BenchError("no JSON in the reply")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise BenchError(f"the reply is not JSON: {exc}"[:120]) from exc


def asked(ask, prompt: str, shape, sleep=time.sleep) -> tuple[object, str]:
    """One prompt to a seat, tried again when the reply has the wrong shape, and
    waited for when the seat is rate-limited: a 429 is a pause, not an answer. The
    first full run lost 10 of 20 passages to groq's per-minute limit (2026-10-10)."""
    problem = "no attempt made"
    waits = iter(RATE_LIMIT_WAITS)
    attempts = 0
    while attempts < ATTEMPTS:
        try:
            text, model = ask(prompt)
            return shape(parsed(text)), model
        except BenchError as exc:
            problem = str(exc)
            if "429" not in problem:
                attempts += 1
                continue
            wait = next(waits, None)
            if wait is None:        # the schedule is spent: stop, never hammer the seat (Codex, GRIP#7938)
                raise BenchError(f"rate limit held past {sum(RATE_LIMIT_WAITS)} s: {problem}") from exc
            sleep(wait)
    raise BenchError(problem)


def three_questions(value) -> list[dict]:
    items = value if isinstance(value, list) else (value or {}).get("questions")
    if not isinstance(items, list) or len(items) != QUESTIONS:
        raise BenchError("not three questions")
    out = [{"question": str(i.get("question") or "").strip(), "answer": str(i.get("answer") or "").strip()}
           for i in items if isinstance(i, dict)]
    if len(out) != QUESTIONS or not all(q["question"] and q["answer"] for q in out):
        raise BenchError("a question or an answer is empty")
    return out


def three_answers(value) -> list[str]:
    items = value.get("answers") if isinstance(value, dict) else value
    if not isinstance(items, list) or len(items) != QUESTIONS:
        raise BenchError("not three answers")
    return [str(a).strip() for a in items]


def three_marks(value) -> list[str]:
    items = value if isinstance(value, list) else (value or {}).get("marks")
    if not isinstance(items, list) or len(items) != QUESTIONS:
        raise BenchError("not three marks")
    marks = [str((i.get("mark") if isinstance(i, dict) else i) or "").strip().lower() for i in items]
    if any(mark not in MARKS for mark in marks):
        raise BenchError("a mark is not one of " + ", ".join(MARKS))
    return marks


# ---- the passages -------------------------------------------------------------------

def _is_passage(block: str) -> bool:
    return (MIN_WORDS <= len(block.split()) <= MAX_WORDS and not block.startswith(NOT_PROSE)
            and "```" not in block and "|" not in block)


def _paragraphs(text: str) -> list[str]:
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", text)]
    return [b for b in blocks if _is_passage(b)]


def _h778_texts() -> set[str]:
    try:
        pairs = json.loads(H778_PAIRS.read_text(encoding="utf-8")).get("pairs") or []
    except (OSError, ValueError):
        return set()
    return {" ".join(str(p.get("original") or "").split()) for p in pairs}


def encrypted_patterns(root: Path) -> list[str]:
    """The path patterns .gitattributes encrypts with git-crypt. A decrypting checkout
    shows those files as plaintext, and a passage taken from one would be committed in
    the clear inside the passages file, so they are never candidates."""
    try:
        lines = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.split()[0] for line in lines if "filter=git-crypt" in line and not line.startswith("#")]


def _encrypted(relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern.replace("**", "*"))
               for pattern in patterns)


def _passages_of(path: Path, root: Path, excluded: set[str], patterns: list[str]) -> list[dict]:
    relative = str(path.relative_to(root))
    if _encrypted(relative, patterns):
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [{"id": hashlib.sha256(p.encode("utf-8")).hexdigest()[:8], "source": relative, "text": p}
            for p in _paragraphs(text) if p not in excluded]


def candidates(root: Path, dirs=PUBLIC_DIRS) -> list[dict]:
    """Every public paragraph of the given directories, with its source, in path order.
    Encrypted files and the H778 pairs are left out."""
    excluded, patterns = _h778_texts(), encrypted_patterns(root)
    paths = itertools.chain.from_iterable(sorted((root / folder).glob("*.md")) for folder in dirs)
    return list(itertools.chain.from_iterable(_passages_of(path, root, excluded, patterns) for path in paths))


def select(root: Path, count: int, seed: int) -> dict:
    pool = candidates(root)
    rng = random.Random(seed)
    chosen = rng.sample(pool, min(count, len(pool)))
    return {"seed": seed, "pool": len(pool), "passages": sorted(chosen, key=lambda p: p["id"])}


def passages_digest(passages: list[dict]) -> str:
    canonical = json.dumps([[p["id"], p["text"]] for p in passages], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---- one passage --------------------------------------------------------------------

def _answer_sets(write, passage: str, questions: list[dict], cycles: int, control_item: int | None) -> dict:
    """Every answer set of one passage: PLAIN cycles, STE, and the CONTROL when planted."""
    numbered = "\n".join(f"{n}. {q['question']}" for n, q in enumerate(questions, 1))
    plain = ASK_ANSWERS + passage + "\n\nQUESTIONS:\n" + numbered
    sets = {f"plain{c}": asked(write, plain, three_answers)[0] for c in range(1, cycles + 1)}
    sets["ste"] = asked(write, INSTRUCTION + "\n\n" + plain, three_answers)[0]
    if control_item is not None:
        sets["control"] = asked(write, plain + ASK_CONTROL.format(number=control_item + 1), three_answers)[0]
    return sets


def _mark_prompt(passage: str, questions: list[dict], answers: list[str]) -> str:
    rows = "\n".join(f"{n}. Q: {q['question']}\n   expected: {q['answer']}\n   given: {a}"
                     for n, (q, a) in enumerate(zip(questions, answers), 1))
    return ASK_MARKS.replace("{passage}", passage) + rows     # not .format: the rubric holds JSON braces


def _marked(mark, passage: str, questions: list[dict], sets: dict, rng: random.Random) -> dict:
    """Every answer set marked, in a seeded order the marker cannot read a condition from."""
    names = sorted(sets)
    rng.shuffle(names)
    return {name: asked(mark, _mark_prompt(passage, questions, sets[name]), three_marks)[0] for name in names}


def one(write, mark, passage: dict, cycles: int, control_item: int | None, rng: random.Random) -> dict:
    try:
        questions, marker_model = asked(mark, ASK_QUESTIONS + passage["text"], three_questions)
        sets = _answer_sets(write, passage["text"], questions, cycles, control_item)
        marks = _marked(mark, passage["text"], questions, sets, rng)
    except BenchError as exc:
        return {"id": passage["id"], "error": str(exc), "control_item": control_item}
    return {"id": passage["id"], "questions": questions, "answers": sets, "marks": marks,
            "control_item": control_item, "marker_model": marker_model}


# ---- the numbers and the verdict ------------------------------------------------------

def _correct(rows: list[dict], name: str) -> int:
    return sum(row["marks"][name].count("same") for row in rows if name in row["marks"])


def _caught(row: dict) -> bool:
    return row["marks"]["control"][row["control_item"]] in LOST


def totals(rows: list[dict], cycles: int, passages: int) -> dict:
    done = [row for row in rows if "error" not in row]
    plain = [_correct(done, f"plain{c}") for c in range(1, cycles + 1)]
    controls = [row for row in done if row.get("control_item") is not None]
    caught = sum(_caught(row) for row in controls)
    return {"passages": passages, "passages_done": len(done), "complete": len(done) == passages,
            "items": QUESTIONS * len(done), "plain_correct": plain,
            "plain_mean": statistics.fmean(plain) if plain else 0.0,
            "floor": (max(plain) - min(plain)) if plain else 0,
            "ste_correct": _correct(done, "ste"),
            "controls": len(controls), "controls_caught": caught,
            "control_share": caught / len(controls) if controls else None}


def verdict(t: dict, required: int = PASSAGES_REQUIRED) -> tuple[str, str]:
    """The registered rule, applied to the totals. See the module docstring."""
    if t["passages"] < required:
        return "INCONCLUSIVE", f"a smoke run of {t['passages']} passages; the registration needs {required}"
    if not t["complete"] or not t["passages"]:
        return "INCONCLUSIVE", f"{t['passages_done']} of {t['passages']} passages done"
    if t["control_share"] is None or t["control_share"] < CONTROL_MIN:
        return "INCONCLUSIVE", (f"the marker caught {t['controls_caught']} of {t['controls']} planted losses; "
                                f"the floor is {CONTROL_MIN:.0%}")
    margin = t["plain_mean"] - t["floor"]
    if t["ste_correct"] >= margin:
        return "CONFIRMED", (f"STE kept {t['ste_correct']} of {t['items']} facts against a plain mean of "
                             f"{t['plain_mean']:.1f} with an A/A floor of {t['floor']}: non-inferior")
    return "REFUTED", (f"STE kept {t['ste_correct']} of {t['items']} facts, under the plain mean "
                       f"{t['plain_mean']:.1f} minus the A/A floor {t['floor']}")


def sealed(record: dict) -> dict:
    body = {key: value for key, value in record.items() if key != "sha256"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {**body, "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def control_items(passages: list[dict], controls: int, seed: int) -> dict[str, int]:
    """Which passages carry a planted loss, and which of their three facts is left out."""
    rng = random.Random(seed * 7919 + 1)
    chosen = rng.sample(passages, min(controls, len(passages)))
    return {p["id"]: rng.randrange(QUESTIONS) for p in chosen}


def passage_rng(seed: int, passage_id: str) -> random.Random:
    """The marking order of one passage depends on the seed and the passage alone, so
    a passage judged again by --resume gets the order the first run would have given it."""
    return random.Random(f"{seed}:{passage_id}")


def run(passages: list[dict], write, mark, seed: int, cycles: int = 3, controls: int = 4,
        seats: tuple[str, str] = (WRITER, MARKER), required: int = PASSAGES_REQUIRED) -> dict:
    planted = control_items(passages, controls, seed)
    rows = [one(write, mark, p, cycles, planted.get(p["id"]), passage_rng(seed, p["id"])) for p in passages]
    t = totals(rows, cycles, len(passages))
    word, because = verdict(t, required)
    return sealed({"hypothesis": HYPOTHESIS, "seed": seed, "cycles": cycles, "writer": seats[0], "marker": seats[1],
                   "instruction": INSTRUCTION, "rule": verdict.__doc__, "control_min": CONTROL_MIN,
                   "passages_required": required, "smoke": len(passages) < required,
                   "passages_sha256": passages_digest(passages), "passages": passages, "rows": rows,
                   "totals": t, "verdict": word, "because": because})


PROTOCOL = ("instruction", "control_min", "rule")


def _same_protocol(record: dict) -> list[str]:
    """The protocol fields of ``record`` that differ from this code's. A resumed row is
    written by this code's prompts and judged by this code's rule, so the record it
    joins must have used the same ones (Codex, GRIP#7938)."""
    current = {"instruction": INSTRUCTION, "control_min": CONTROL_MIN, "rule": verdict.__doc__}
    return sorted(name for name in PROTOCOL if record.get(name) != current[name])


def resume(record: dict, write, mark) -> dict:
    """Judge again only the passages whose row holds an error (a seat's window filled,
    a rate limit held): every judged row stays exactly as it is, the planted control
    items stay where they were, and the new record names the seal it grew from. The
    source is checked here, on every entry path: a tampered record, or one made under
    another protocol, grows nothing."""
    word, because = recomputed(record)
    if word == "TAMPERED":
        raise BenchError(f"the record to resume is TAMPERED: {because}")
    drift = _same_protocol(record)
    if drift:
        raise BenchError("the record was made under another protocol (" + ", ".join(drift) + "); run a new bench")
    by_id = {p["id"]: p for p in record["passages"]}
    cycles, seed = int(record["cycles"]), int(record["seed"])
    again = [row["id"] for row in record["rows"] if "error" in row]
    rows = [row if "error" not in row
            else one(write, mark, by_id[row["id"]], cycles, row.get("control_item"), passage_rng(seed, row["id"]))
            for row in record["rows"]]
    t = totals(rows, cycles, len(record["passages"]))
    word, because = verdict(t, int(record.get("passages_required", PASSAGES_REQUIRED)))
    body = {k: v for k, v in record.items() if k != "sha256"}
    return sealed({**body, "rows": rows, "totals": t, "verdict": word, "because": because,
                   "resumed_from": record.get("sha256"), "resumed_passages": again})


def recomputed(record: dict) -> tuple[str, str]:
    """A record's verdict from its raw rows, after its seal is checked. A record whose
    seal or whose stored totals disagree with its rows is TAMPERED, whatever its
    stored verdict says (a Codex finding on GRIP#7928)."""
    if sealed({k: v for k, v in record.items() if k != "sha256"})["sha256"] != record.get("sha256"):
        return "TAMPERED", "the record's seal does not match its content"
    fresh = totals(record["rows"], int(record["cycles"]), len(record["passages"]))
    if fresh != record.get("totals"):
        return "TAMPERED", "the stored totals do not match the rows"
    return verdict(fresh, int(record.get("passages_required", PASSAGES_REQUIRED)))


# ---- the command line ------------------------------------------------------------------

def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--select", type=int, help="choose this many public passages and write them to --out")
    ap.add_argument("--passages", help="the passages file to bench")
    ap.add_argument("--verdict", help="recompute the verdict of a record")
    ap.add_argument("--resume", help="judge again only the errored passages of this record (seats and seed from it)")
    ap.add_argument("--out", help="where to write the passages or the record")
    ap.add_argument("--seed", type=int, default=20261011)
    ap.add_argument("--cycles", type=int, default=3, help="independent PLAIN cycles (the A/A floor)")
    ap.add_argument("--controls", type=int, default=4, help="passages that carry a planted loss")
    ap.add_argument("--limit", type=int, help="bench only the first N passages (a smoke run)")
    ap.add_argument("--writer", default=WRITER)
    ap.add_argument("--marker", default=MARKER)
    return ap.parse_args(argv)


def _bench(args: argparse.Namespace) -> dict:
    if not args.out:
        raise SystemExit("a bench run needs --out: every question, answer, mark and the seal must be kept")
    check_seats(args.writer, args.marker)
    passages = json.loads(Path(args.passages).read_text(encoding="utf-8"))["passages"]
    if args.limit:
        passages = passages[:args.limit]
    return run(passages, provider_ask(args.writer), provider_ask(args.marker), args.seed, args.cycles,
               args.controls, (args.writer, args.marker))


def _resumed(args: argparse.Namespace) -> dict:
    if not args.out:
        raise SystemExit("a resume needs --out: the record that grows from the old one must be kept")
    record = json.loads(Path(args.resume).read_text(encoding="utf-8"))
    check_seats(record["writer"], record["marker"])
    try:
        return resume(record, provider_ask(record["writer"]), provider_ask(record["marker"]))
    except BenchError as exc:
        raise SystemExit(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.verdict:
        word, because = recomputed(json.loads(Path(args.verdict).read_text(encoding="utf-8")))
        print(f"{word}: {because}")
        return 1 if word == "TAMPERED" else 0
    if args.select:
        result = select(_ROOT, args.select, args.seed)
    elif args.resume:
        result = _resumed(args)
        print(f"{result['verdict']}: {result['because']}")
    elif args.passages:
        result = _bench(args)
        print(f"{result['verdict']}: {result['because']}")
    else:
        print("give --select N, --passages FILE or --verdict FILE", file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
