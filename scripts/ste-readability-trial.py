"""H778: is 80% ASD-STE100 easier to read than GRIP's current voice, with no loss of facts?

The trial reads a file of pairs. Each pair is one paragraph from GRIP's public
docs and the same paragraph rewritten in Simplified Technical English. For each
pair, each judge makes five calls:

1. it writes three factual questions, with answers, from the ORIGINAL alone;
2. it sees both versions in a random order and says which is easier to read;
3. it answers the three questions from the STE version alone;
4. it answers them from the original alone;
5. it marks all six answers against the expected answers.

The judges are never Anthropic models, because the rewrites are written by one
(rules/exogenous-anchor-law.md: the judge is never the author's family). The
two seats are DeepSeek v4 Pro, called straight at the provider with an answer
budget, and GPT-6 Astra on the Codex subscription.

THE VERDICT is computed, not written. H778 predicts that each judge prefers the
STE version in at least 14 of 20 pairs and answers at least as accurately from
it. It is FALSIFIED when a judge prefers it in fewer than 11, or answers less
accurately from it. A judge that did not finish every pair gives no verdict:
the result is then INCONCLUSIVE, because a pair that was not judged is not a pass.
A run over fewer than the 20 pairs is a smoke run, and it is INCONCLUSIVE too.

The record holds numbers, the judges' short sentences and the public paragraphs.
It holds nothing from any session. Its sha256 is over the canonical JSON of
everything else in it, so a reader can recompute it.

ONE SEAT AT A TIME. A seat can be out of allowance when the other is ready. So
each judge may run alone into its own record, and --combine joins the records.
Each record names the sha256 of the pairs it judged and the seed. Records join
only when both match and no judge is in two of them, so a judge runs once and
its run cannot be swapped for a better one.

usage: grip-py scripts/ste-readability-trial.py --pairs FILE --out FILE
           [--judges deepseek,astra] [--seed N] [--limit N] [--workers N]
       grip-py scripts/ste-readability-trial.py --combine FILE FILE ... --out FILE
       grip-py scripts/ste-readability-trial.py --resume FILE --pairs FILE --out FILE

--resume judges again only the pairs that have an error in a record (a judge's
usage window can fill in the middle of a run). Each pair that was judged stays
exactly as it is, and each pair that is judged again keeps its seeded order.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: H778 is a prediction about 20 pairs. A run over another number is a smoke run.
PAIRS_REQUIRED, PREFER_MIN, FALSIFY_BELOW, QUESTIONS, ATTEMPTS = 20, 14, 11, 3, 3
#: DeepSeek reasons before it answers, and the reasoning uses the same budget.
#: A call that is cut off is tried again with the next budget.
DEEPSEEK_BUDGETS = (16000, 48000, 96000)
ASTRA_MODEL, ASTRA_EFFORT, ASTRA_TIMEOUT_S = "gpt-6-astra", "medium", 600
LIMITS = ("This trial measures two things: which version a model judge calls easier to read, and "
          "whether the judge answers factual questions as accurately from each version. It is not "
          "a measure of one expert reader's time and error rate on real work. The plan measures "
          "that separately (H781).")

ASK_QUESTIONS = """Read the passage. Write exactly 3 short factual questions that a reader can answer from this passage alone, each with its correct answer. Ask about specific facts: a number, a name, a condition, a cause.
Reply with JSON only, in this shape: [{"q": "...", "a": "..."}, {"q": "...", "a": "..."}, {"q": "...", "a": "..."}]

PASSAGE:
"""
ASK_PREFERENCE = """Two versions of the same passage follow. Which version is easier to read for an expert software engineer? Judge only how easy it is to read and to act on.
Reply with JSON only, in this shape: {"choice": "A" or "B", "why": "one sentence"}

VERSION A:
%s

VERSION B:
%s
"""
ASK_ANSWERS = """Answer each question from the passage alone. If the passage does not say, answer "not stated".
Reply with JSON only: a list of exactly 3 short answers, in the order of the questions.

PASSAGE:
%s

QUESTIONS:
%s
"""
ASK_MARKS = """For each question below, an expected answer and two given answers follow. Say for each given answer whether it has the same meaning as the expected answer.
Reply with JSON only: a list of exactly 3 objects, in the order of the questions, in this shape: {"x": true or false, "y": true or false}

%s
"""


class JudgeError(RuntimeError):
    """A judge gave no usable answer."""


def deepseek_judge(prompt: str) -> tuple[str, str]:
    """DeepSeek v4 Pro at the provider. The HAL route has a fixed 16,384-token
    budget and reports an empty answer as success, so it is not used here."""
    from lib import sovereign_council as sc

    for budget in DEEPSEEK_BUDGETS:
        result = sc.provider_call("deepseek", [{"role": "user", "content": prompt}], time.time(),
                                  max_tokens=budget, timeout=900)
        if result.get("ok") and not result.get("truncated"):
            return str(result.get("text") or ""), str(result.get("model") or "deepseek")
        if result.get("ok") is not True:
            raise JudgeError(str(result.get("text"))[:200])
    raise JudgeError(f"cut off at every budget up to {DEEPSEEK_BUDGETS[-1]}")


def astra_judge(prompt: str) -> tuple[str, str]:
    """GPT-6 Astra on the Codex subscription, read-only, outside every repository,
    with a small environment so the judge sees nothing of this session's.

    rules/gpt-on-the-subscription-never-credits.md: the call forces the ChatGPT
    login and blanks both API key variables, so a full subscription window makes
    Codex refuse. Without that it would go on to workspace credits.
    """
    env = {key: os.environ[key] for key in ("HOME", "PATH", "LANG", "CODEX_HOME") if key in os.environ}
    env |= {"OPENAI_API_KEY": "", "CODEX_API_KEY": ""}
    handle, out_path = tempfile.mkstemp(prefix="ste-trial-", suffix=".txt")
    os.close(handle)
    argv = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
            "--cd", tempfile.gettempdir(), "--color", "never", "-c", "model_provider=openai",
            "-c", "forced_login_method=chatgpt",
            "-c", f"model_reasoning_effort={ASTRA_EFFORT}", "-m", ASTRA_MODEL, "-o", out_path]
    try:
        done = subprocess.run(argv, input=prompt, text=True, encoding="utf-8", errors="replace",
                              capture_output=True, env=env, timeout=ASTRA_TIMEOUT_S)
        answer = Path(out_path).read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"{type(exc).__name__}: {exc}"[:200]) from exc
    finally:
        Path(out_path).unlink(missing_ok=True)
    if done.returncode != 0 or not answer:
        raise JudgeError(f"codex exit {done.returncode}: {(done.stderr or '').strip()[-160:]}")
    return answer, f"{ASTRA_MODEL} (codex, effort {ASTRA_EFFORT})"


JUDGES = {"deepseek": deepseek_judge, "astra": astra_judge}


def parsed(text: str):
    """The JSON value in a judge's reply. A judge may wrap it in a code fence or
    put a sentence before it, so the first bracket to the last one is read."""
    start = min((at for at in (text.find("["), text.find("{")) if at >= 0), default=-1)
    end = max(text.rfind("]"), text.rfind("}"))
    if start < 0 or end <= start:
        raise JudgeError("no JSON in the reply")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise JudgeError(f"the reply is not JSON: {exc}"[:120]) from exc


def asked(ask, prompt: str, shape) -> tuple[object, str]:
    """One question to a judge, tried again when the reply has the wrong shape.
    Returns the checked value and the model that served it."""
    problem = "no attempt made"
    for _ in range(ATTEMPTS):
        try:
            text, model = ask(prompt)
            return shape(parsed(text)), model
        except JudgeError as exc:
            problem = str(exc)
    raise JudgeError(problem)


def three_questions(value) -> list[dict]:
    good = isinstance(value, list) and len(value) == QUESTIONS and all(
        isinstance(item, dict) and str(item.get("q", "")).strip() and str(item.get("a", "")).strip()
        for item in value)
    if not good:
        raise JudgeError("the questions do not have the asked shape")
    return [{"q": str(item["q"]).strip(), "a": str(item["a"]).strip()} for item in value]


def a_choice(value) -> dict:
    choice = str(value.get("choice", "")).strip().upper()[:1] if isinstance(value, dict) else ""
    if choice not in ("A", "B"):
        raise JudgeError("the choice is not A or B")
    return {"choice": choice, "why": str(value.get("why", "")).strip()[:300]}


def three_answers(value) -> list[str]:
    if not (isinstance(value, list) and len(value) == QUESTIONS):
        raise JudgeError("the answers do not have the asked shape")
    return [str(item).strip()[:300] for item in value]


def three_marks(value) -> list[dict]:
    good = isinstance(value, list) and len(value) == QUESTIONS and all(
        isinstance(item, dict) and isinstance(item.get("x"), bool) and isinstance(item.get("y"), bool)
        for item in value)
    if not good:
        raise JudgeError("the marks do not have the asked shape")
    return value


def _numbered(lines: list[str]) -> str:
    return "\n".join(f"{number}. {line}" for number, line in enumerate(lines, 1))


def _marking_sheet(questions: list[dict], x_answers: list[str], y_answers: list[str]) -> str:
    return "\n\n".join(
        f"QUESTION {number}: {item['q']}\nEXPECTED: {item['a']}\nGIVEN X: {x}\nGIVEN Y: {y}"
        for number, (item, x, y) in enumerate(zip(questions, x_answers, y_answers), 1))


def judged(ask, pair: dict, ste_first: bool) -> dict:
    """One judge's five calls on one pair. ``ste_first`` is the random order: it
    puts the STE version first for the preference and for the marking sheet."""
    questions, model = asked(ask, ASK_QUESTIONS + pair["original"], three_questions)
    first, second = (pair["rewrite"], pair["original"]) if ste_first else (pair["original"], pair["rewrite"])
    choice, _ = asked(ask, ASK_PREFERENCE % (first, second), a_choice)
    listed = _numbered([item["q"] for item in questions])
    from_ste, _ = asked(ask, ASK_ANSWERS % (pair["rewrite"], listed), three_answers)
    from_original, _ = asked(ask, ASK_ANSWERS % (pair["original"], listed), three_answers)
    x_answers, y_answers = (from_ste, from_original) if ste_first else (from_original, from_ste)
    marks, _ = asked(ask, ASK_MARKS % _marking_sheet(questions, x_answers, y_answers), three_marks)
    ste_key, original_key = ("x", "y") if ste_first else ("y", "x")
    marked = {"ste": [mark[ste_key] for mark in marks], "original": [mark[original_key] for mark in marks]}
    return {"id": pair["id"], "model": model, "ste_first": ste_first,
            "preferred": "ste" if (choice["choice"] == "A") == ste_first else "original",
            "why": choice["why"], "questions": questions,
            "answers": {"ste": from_ste, "original": from_original},
            "marks": marked, "correct": {side: sum(given) for side, given in marked.items()}}


def _one(ask, pair: dict, ste_first: bool) -> dict:
    try:
        return judged(ask, pair, ste_first)
    except JudgeError as exc:
        return {"id": pair["id"], "error": str(exc)[:200]}


def orders(pairs: list[dict], judge: str, seed: int) -> list[bool]:
    """The random order of each pair for one judge, fixed by the seed."""
    rng = random.Random(f"{seed}:{judge}")
    return [rng.random() < 0.5 for _ in pairs]


def totals(rows: list[dict], pairs: int) -> dict:
    """The counts of one judge. ``complete`` is false when any pair has no result."""
    done = [row for row in rows if "error" not in row]
    return {"pairs": pairs, "pairs_done": len(done), "complete": len(done) == pairs,
            "preferred_ste": sum(row["preferred"] == "ste" for row in done),
            "correct_ste": sum(row["correct"]["ste"] for row in done),
            "correct_original": sum(row["correct"]["original"] for row in done),
            "questions": QUESTIONS * len(done)}


def verdict(by_judge: dict[str, dict]) -> str:
    """H778's own prediction, applied to the counts. See the module docstring."""
    whole = all(total["complete"] and total["pairs"] == PAIRS_REQUIRED for total in by_judge.values())
    if not by_judge or not whole:
        return "INCONCLUSIVE"
    judges = list(by_judge.values())
    if any(total["preferred_ste"] < FALSIFY_BELOW or total["correct_ste"] < total["correct_original"]
           for total in judges):
        return "FALSIFIED"
    if all(total["preferred_ste"] >= PREFER_MIN for total in judges):
        return "CONFIRMED"
    return "INCONCLUSIVE"


def sealed(record: dict) -> dict:
    """``record`` with the sha256 of its canonical JSON added."""
    body = {key: value for key, value in record.items() if key != "sha256"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {**body, "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def _judge_rows(ask, pairs: list[dict], order: list[bool], workers: int) -> list[dict]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda item: _one(ask, *item), zip(pairs, order)))


def pairs_digest(pairs: list[dict]) -> str:
    """The sha256 of what the judges are shown: each pair's id and its two texts."""
    shown = [[pair["id"], pair["original"], pair["rewrite"]] for pair in pairs]
    canonical = json.dumps(shown, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_of(results: dict, seed: int, pairs: int, digest: str) -> dict:
    """The sealed record for these results: the counts and the verdict are computed here."""
    by_judge = {name: totals(rows, pairs) for name, rows in results.items()}
    return sealed({"hypothesis": "H778", "seed": seed, "pairs": pairs, "pairs_sha256": digest,
                   "prediction": {"prefer_ste_at_least": PREFER_MIN, "falsified_below": FALSIFY_BELOW,
                                  "accuracy": "from the STE version, at least as many correct answers"},
                   "totals": by_judge, "verdict": verdict(by_judge), "limits": LIMITS,
                   "results": results})


def run(pairs: list[dict], judges: dict, seed: int, workers: int = 4) -> dict:
    """The whole trial: every judge on every pair, then the counts and the verdict."""
    results = {name: _judge_rows(ask, pairs, orders(pairs, name, seed), workers)
               for name, ask in judges.items()}
    return record_of(results, seed, len(pairs), pairs_digest(pairs))


def combined(records: list[dict]) -> dict:
    """One record from several, each made by other judges on the same pairs and seed."""
    first = records[0]
    same = all((record["seed"], record["pairs"], record["pairs_sha256"]) ==
               (first["seed"], first["pairs"], first["pairs_sha256"]) for record in records)
    untouched = all(sealed(record) == record for record in records)
    names = [name for record in records for name in record["results"]]
    if not (same and untouched) or len(names) != len(set(names)):
        raise ValueError("the records do not join: they need the same pairs and seed, an unbroken "
                         "sha256 each, and no judge in two of them")
    results = {name: rows for record in records for name, rows in record["results"].items()}
    return record_of(results, first["seed"], first["pairs"], first["pairs_sha256"])


def _again(ask, pairs: list[dict], rows: list[dict], order: list[bool], workers: int) -> list[dict]:
    """``rows`` with each error row replaced by a new judgement of the same pair in the same order."""
    failed = [index for index, row in enumerate(rows) if "error" in row]
    fresh = _judge_rows(ask, [pairs[index] for index in failed], [order[index] for index in failed], workers)
    return [fresh[failed.index(index)] if index in failed else row for index, row in enumerate(rows)]


def resumed(record: dict, pairs: list[dict], judges: dict, workers: int = 4) -> dict:
    """The record with each failed pair judged again. A judged pair is never judged twice."""
    digest = pairs_digest(pairs)
    if sealed(record) != record or (record["pairs_sha256"], record["pairs"]) != (digest, len(pairs)):
        raise ValueError("the record cannot be resumed: it needs an unbroken sha256 and these same pairs")
    results = {name: _again(judges[name], pairs, rows, orders(pairs, name, record["seed"]), workers)
               if name in judges else rows for name, rows in record["results"].items()}
    return record_of(results, record["seed"], len(pairs), digest)


def table(record: dict) -> str:
    """The counts of each judge, one row each, then the verdict."""
    rows = [f"{name:<10} prefers STE in {total['preferred_ste']:>2} of {total['pairs_done']:>2} pairs; "
            f"correct answers: STE {total['correct_ste']} / original {total['correct_original']} "
            f"of {total['questions']}" + ("" if total["complete"] else "  (NOT COMPLETE)")
            for name, total in record["totals"].items()]
    return "\n".join([*rows, f"{record['hypothesis']} verdict: {record['verdict']}", f"sha256: {record['sha256']}"])


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pairs", type=Path, help="the pairs file to judge")
    parser.add_argument("--combine", type=Path, nargs="+", metavar="FILE",
                        help="join records that other judges made on the same pairs and seed")
    parser.add_argument("--resume", type=Path, metavar="FILE",
                        help="judge again only the pairs that have an error in this record (needs --pairs)")
    parser.add_argument("--out", type=Path, required=True, help="where to write the sealed record")
    parser.add_argument("--judges", default="deepseek,astra", help="comma-separated: " + ", ".join(JUDGES))
    parser.add_argument("--seed", type=int, default=778, help="fixes the A/B order of every pair")
    parser.add_argument("--limit", type=int, default=0, help="judge only the first N pairs (a smoke run)")
    parser.add_argument("--workers", type=int, default=4, help="pairs judged at the same time, per judge")
    return parser.parse_args(argv)


def _judged_record(args: argparse.Namespace) -> dict | None:
    """The record of a new run, or None after a message when a judge name does not exist."""
    names = [name.strip() for name in args.judges.split(",") if name.strip()]
    unknown = [name for name in names if name not in JUDGES]
    if unknown or not names or args.pairs is None:
        print(f"ste-readability-trial: no such judge: {', '.join(unknown) or '(none named)'}"
              if unknown or not names else "ste-readability-trial: give --pairs or --combine", file=sys.stderr)
        return None
    pairs = json.loads(args.pairs.read_text(encoding="utf-8"))["pairs"]
    pairs = pairs[:args.limit] if args.limit else pairs
    return run(pairs, {name: JUDGES[name] for name in names}, args.seed, args.workers)


def _combined_record(paths: list[Path]) -> dict | None:
    """The joined record, or None after a message when the records do not join."""
    try:
        return combined([json.loads(path.read_text(encoding="utf-8")) for path in paths])
    except (OSError, ValueError, KeyError) as exc:
        print(f"ste-readability-trial: {exc}", file=sys.stderr)
        return None


def _resumed_record(args: argparse.Namespace) -> dict | None:
    """The resumed record, or None after a message when the record cannot be resumed."""
    names = [name.strip() for name in args.judges.split(",") if name.strip() in JUDGES]
    try:
        record = json.loads(args.resume.read_text(encoding="utf-8"))
        pairs = json.loads(args.pairs.read_text(encoding="utf-8"))["pairs"]
        return resumed(record, pairs, {name: JUDGES[name] for name in names}, args.workers)
    except (OSError, ValueError, KeyError, AttributeError) as exc:
        print(f"ste-readability-trial: {exc}", file=sys.stderr)
        return None


def _record(args: argparse.Namespace) -> dict | None:
    if args.combine:
        return _combined_record(args.combine)
    return _resumed_record(args) if args.resume else _judged_record(args)


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when the record is written, 2 when it cannot be made."""
    args = _parse(argv)
    record = _record(args)
    if record is None:
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(table(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())
