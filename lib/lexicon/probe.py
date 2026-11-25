"""The comprehension gate: does a kernel convey its rule as well as the original?

For one rule, the same fixed items are put to each model three times, each
under a different system prompt:

  original  the rule as written, read from git at the commit the items name
  kernel    the LEXICON followed by the kernel
  control   no rule at all

Scoring is deterministic, so no model grades another
(rules/exogenous-anchor-law.md - the verifier is never the author). An item is
either MULTIPLE CHOICE (one right letter) or SHORT ANSWER (the exact value,
command, name, number or yes/no the rule states, or UNKNOWN when the model does
not know). A short answer matches an accepted answer after surrounding quotes
and backticks, extra spaces and one final full stop, semicolon or comma are
ignored. Letter case is KEPT, because ``rg -I`` and ``rg -i`` are different
flags; only an accepted answer that is a plain lower-case word or phrase, such
as ``yes``, matches in any case.

Short answer exists because multiple choice proved unable to measure rule
knowledge on frontier models (2026-09-29, three item versions, two independent
authors): with no rule at all, models scored 78-100 on multiple choice, while
the same Claude Opus 5.5 seat, asked the same kind of fact with no options,
answered UNKNOWN. A capable model infers the right option from the options
themselves; it cannot infer a value it was never told.

On one model the kernel PASSES when it scores within ``margin`` points of the
original there, and the control scores at least ``control_gap`` points below
the original. If the control does about as well as the original, the items
never needed the rule and that model tells us nothing: the result is VACUOUS,
not a pass. The whole gate passes only when every model with a usable result
passes and at least five such models, from at least three vendors, took part.

For that check to work, the control must be ABLE to score: every condition
invites the model to use its own judgement where the instructions are silent,
and to answer UNKNOWN only when it does not know. A question a capable model
answers without the rule then shows up as a control score, and a kernel is
judged only on what it genuinely has to carry.

Items are PRE-REGISTERED. Each item file names the commit and path of the
text it was written from, plus its sha256, and the original condition is read
from exactly that blob. Items written after seeing a kernel would test the
kernel's author, not the kernel.

Every Claude seat runs isolated (tokencount.run_claude): no tools, no MCP
servers, no settings, hooks, skills or auto-memory, no inherited Claude or
Anthropic variables except where the login lives, and a private working
directory per call, so the rule under test can only reach the model through
the condition text. Checked 2026-09-29 with a file canary in the working
directory: under the earlier ``--allowedTools ""`` a seat read the file, and
concurrent seats shared one temporary directory holding every condition's
prompt, so Claude control scores measured before that date may be inflated.
Under the current flags the seat cannot read it. The control condition still
catches anything that leaks, as an inflated control score.

The ``--out`` file (:func:`to_json`) is one JSON object: ``rule``,
``source_commit``, ``source_sha256``, ``kernel_sha256`` (null when only the
items were validated), ``bank_sha256`` (the sha256 of the question bank file
the run used, so a re-render can refuse a different bank), ``items`` (a
count), ``runs`` (one per seat and
condition: seat, vendor, condition, correct, total, answers, error),
``verdicts`` (seat, vendor, status, detail), ``gate`` (passed, reason) and
``pooled`` (difference, low, high: the kernel minus the original in points,
pooled over every seat and question with a 95% bootstrap interval; margin:
the ``--margin`` its verdict used; null without a kernel run). The pooled
figure is reported beside the gate, not part of it: see
:func:`pooled_difference`.
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .tokencount import CountError, input_total, run_claude

try:
    from _compat import run_text
except ImportError:  # imported as lib.lexicon, with only the repository root on the path
    from lib._compat import run_text

LETTERS = ("A", "B", "C", "D")
PREAMBLE = "You are an AI agent. Follow the instructions below.\n\n"
NO_RULE = "(There are no further instructions.)"
UNKNOWN = "UNKNOWN"
DEFAULT_SEATS = ("deepseek", "xai", "groq", "gemini", "gemini-pro",
                 "claude:claude-haiku-4-5", "claude:claude-opus-5-5")
# The header must let a model answer from what it already knows. An earlier
# header said "the value the instructions give, or UNKNOWN if they do not say.
# Do not guess", and on 2026-09-29 all 630 control answers (3 rules x 7 seats x
# 30 items) were UNKNOWN: a compliant model with no rule can then never score,
# so the control could not catch a question that does not need the rule.
QUESTION_HEADER = (
    "Answer each question the way an agent following the instructions you were "
    "given would act. Where the instructions are silent, use your best judgement.\n"
    "- A question with lettered options: answer with one letter.\n"
    "- A question without options: answer with the exact value, command, name, "
    f"number or yes/no, or {UNKNOWN} if you do not know it.\n")


def _reply_instruction(items: tuple[Item, ...]) -> str:
    """How to reply, with an example keyed by the bank's own question ids.

    A fixed example ("q-01", "q-02") was copied by Gemini 3.1 Pro onto a bank
    whose ids start "b-" (2026-09-29): well-formed answers that matched no
    question, so every one of its runs on that bank was recorded as
    unparseable.
    """
    pairs = ", ".join(f'"{i.id}": "{value}"' for i, value in zip(items, ("B", "600")))
    return ("Reply with ONLY a JSON object mapping each question id to its answer, "
            f"for example {{{pairs}}}.\n\n")


class ProbeError(RuntimeError):
    """A seat could not produce an answer. Recorded, never scored as wrong."""


# ── items ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Item:
    """One question: multiple choice (options + answer) or short answer (accept)."""

    id: str
    clause: str
    question: str
    options: dict[str, str] = field(default_factory=dict)
    answer: str = ""
    safety: bool = False
    accept: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItemSet:
    rule: str
    source_commit: str
    source_path: str
    source_sha256: str
    items: tuple[Item, ...]
    bank_sha256: str = ""  # of the item file itself, so saved results name their bank


def _check_short(item: Item) -> None:
    if item.options or item.answer:
        raise ValueError(f"{item.id}: a short-answer item takes no options and no answer letter")
    if not all(isinstance(a, str) and normalise(a) for a in item.accept):
        raise ValueError(f"{item.id}: every accepted answer must be non-empty text")
    if any(normalise(a).lower() == UNKNOWN.lower() for a in item.accept):
        raise ValueError(f"{item.id}: {UNKNOWN} can never be an accepted answer")


def _check_item(item: Item) -> None:
    if item.accept:
        _check_short(item)
        return
    if sorted(item.options) != list(LETTERS):
        raise ValueError(f"{item.id}: options must be exactly {LETTERS}")
    if item.answer not in LETTERS:
        raise ValueError(f"{item.id}: answer {item.answer!r} is not one of {LETTERS}")


def _item(raw: dict) -> Item:
    return Item(**{**raw, "accept": tuple(raw.get("accept") or ())})


def load_items(path: Path) -> ItemSet:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    items = tuple(_item(raw) for raw in data["items"])
    for item in items:
        _check_item(item)
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: duplicate item ids")
    return ItemSet(rule=data["rule"], source_commit=data["source_commit"],
                   source_path=data["source_path"], source_sha256=data["source_sha256"],
                   items=items, bank_sha256=sha256_text(text))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def original_text(item_set: ItemSet, repo: Path) -> str:
    """The pre-registered source text, read from git and checked against its digest.

    Git prints the committed bytes, which are UTF-8. They are decoded as UTF-8
    whatever the locale says: read as cp1252, the Windows default, the text
    changes before its digest is taken and every bank is refused.
    """
    proc = run_text(["git", "-C", str(repo), "show",
                     f"{item_set.source_commit}:{item_set.source_path}"],
                    capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise ProbeError(f"cannot read {item_set.source_commit}:{item_set.source_path}: "
                         f"{proc.stderr.strip()[:200]}")
    if sha256_text(proc.stdout) != item_set.source_sha256:
        raise ProbeError(f"{item_set.source_path}@{item_set.source_commit} does not match "
                         "the digest the items were written against")
    return proc.stdout


def _render(item: Item) -> str:
    if item.accept:
        return f"{item.id}. {item.question}\n  (no options: give the exact answer, or {UNKNOWN})"
    opts = "\n".join(f"  {k}) {item.options[k]}" for k in LETTERS)
    return f"{item.id}. {item.question}\n{opts}"


def render_questions(items: tuple[Item, ...]) -> str:
    return QUESTION_HEADER + _reply_instruction(items) + "\n\n".join(_render(i) for i in items)


_WRAPPERS = "`'\" \t\n"


def normalise(answer: str) -> str:
    """Single spaces, no surrounding quotes or backticks, and no final . ; or ,

    Case is kept: ``rg -I`` drops filenames and ``rg -i`` ignores case, so a
    matcher that folded case would credit the wrong flag.
    """
    text = answer.strip(_WRAPPERS)
    if text[-1:] in (".", ";", ","):
        text = text[:-1].strip(_WRAPPERS)
    return " ".join(text.split())


def _matches(given: str, accepted: str) -> bool:
    """Exact after normalising; a plain lower-case word or phrase also ignores case."""
    g, a = normalise(given), normalise(accepted)
    plain_word = a.replace(" ", "").isalpha() and a == a.lower()
    return g == a or (plain_word and g.lower() == a)


def is_correct(item: Item, given: str | None) -> bool:
    """A letter for multiple choice; for short answer, one of the accepted answers."""
    if not given:
        return False
    if item.accept:
        return any(_matches(given, a) for a in item.accept)
    return given.strip().upper()[:1] == item.answer


_DECODER = json.JSONDecoder()


def _json_objects(text: str) -> list[dict]:
    """Every top-level JSON object in ``text``, in order.

    A real parser rather than a brace-matching pattern: a short answer is often
    a command, and a command such as ``"${arr[@]}"`` carries braces of its own.
    """
    found, start = [], text.find("{")
    while start != -1:
        try:
            obj, end = _DECODER.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        found.append(obj)
        start = text.find("{", end)
    return found


def parse_answers(text: str, ids: list[str]) -> dict[str, str]:
    """The answers a reply gives, as text, from its last JSON object naming a question."""
    wanted = set(ids)
    for obj in reversed(_json_objects(text)):
        answers = {k: str(v).strip() for k, v in obj.items() if k in wanted}
        if answers:
            return answers
    return {}


# ── seats ────────────────────────────────────────────────────────────────────

class Seat(Protocol):
    id: str
    vendor: str

    def answer(self, system: str, user: str) -> str: ...


def model_vendor(model: str, vendor_of) -> str:
    """The vendor of the MODEL behind a seat, not of the host serving it.

    The ``groq`` seat serves ``openai/gpt-oss-120b``: OpenAI's weights on
    Groq's hardware. Counting it as a separate vendor would inflate the gate's
    vendor-diversity figure, so an id the attribution table does not know
    falls back to its ``vendor/`` prefix.
    """
    vendor = vendor_of(model)
    return model.split("/", 1)[0] if vendor == "unknown" and "/" in model else vendor


@dataclass
class CouncilSeat:
    """A non-Anthropic model reached through lib/sovereign_council.provider_call."""

    name: str
    min_interval_s: float = 0.0
    timeout_s: float = 300.0
    # Reasoning models think before they answer, and think longest when the
    # rule is missing and they must work an answer out: DeepSeek v4-pro ran
    # past 8,000 tokens on no-rule controls and finished at 32,000 (stopping
    # on its own after 141 s). All five council providers accepted 32,000.
    max_tokens: int = 32000
    _last: float = field(default=0.0, repr=False)

    @property
    def id(self) -> str:
        return self.name

    @property
    def vendor(self) -> str:
        from lib.model_attribution import vendor_of  # lazy: import only when probing
        from lib.sovereign_council import REGISTRY
        return model_vendor(REGISTRY[self.name][1], vendor_of)

    def answer(self, system: str, user: str) -> str:
        from lib.sovereign_council import provider_call  # lazy: network stack
        wait = self.min_interval_s - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        # A reply cut off at the limit is an error, never a score: provider_call
        # falls back to the reasoning text when the content is empty, so a
        # truncated reply otherwise reads as "no JSON answer".
        res = provider_call(self.name, [{"role": "system", "content": system},
                                        {"role": "user", "content": user}],
                            time.time(), max_tokens=self.max_tokens, timeout=self.timeout_s)
        if not res.get("ok"):
            raise ProbeError(f"{self.name}: {res.get('text', '')[:200]}")
        if res.get("truncated"):
            raise ProbeError(f"{self.name}: reply cut off at the token limit "
                             f"(finish_reason={res.get('finish_reason')})")
        return res["text"]


@dataclass
class ClaudeSeat:
    """An Anthropic model through one isolated ``claude -p`` call.

    The isolation lives in tokencount.run_claude, shared with the token
    counter so the two cannot drift apart: no tools, no MCP servers, no
    settings or memory, no inherited Claude variables but the login's
    location, and a private working directory per call.
    """

    model: str
    vendor: str = "anthropic"
    timeout_s: int = 900

    @property
    def id(self) -> str:
        return f"claude:{self.model}"

    def answer(self, system: str, user: str) -> str:
        try:
            record = run_claude(self.model, user, system=system, timeout=self.timeout_s)
            input_total(record, self.model)  # raises unless this model served the call
        except CountError as exc:
            raise ProbeError(f"{self.id}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProbeError(f"{self.id}: no reply within {self.timeout_s}s") from exc
        return record.get("result") or ""


def seat(spec: str, registry: dict | None = None) -> Seat:
    """``claude:<model>`` for an Anthropic seat; any other name is a council seat.

    An unknown name is refused, never dropped: a probe that silently ran on
    fewer models would report a gate it did not measure
    (rules/declared-input-must-reach-the-actor.md). Groq's free tier caps a
    minute at 8,000 tokens, which one long rule plus its questions nearly
    fills, so its calls are spaced a minute apart.
    """
    if spec.startswith("claude:"):
        return ClaudeSeat(model=spec.split(":", 1)[1])
    if registry is None:
        from lib.sovereign_council import REGISTRY as registry  # lazy: network stack
    if spec not in registry:
        raise ValueError(f"unknown seat {spec!r}; council seats are {sorted(registry)}, "
                         "or use claude:<model>")
    return CouncilSeat(name=spec, min_interval_s=65.0 if spec == "groq" else 0.0)


# ── running and judging ──────────────────────────────────────────────────────

@dataclass
class Run:
    seat: str
    vendor: str
    condition: str
    correct: int
    total: int
    answers: dict[str, str]
    error: str | None = None

    @property
    def points(self) -> float:
        return 100.0 * self.correct / self.total if self.total else 0.0


def conditions(original: str, kernel: str | None, lexicon: str | None) -> dict[str, str]:
    """System prompts per condition; the kernel condition needs both texts.

    >>> sorted(conditions("RULE", "KERNEL", "LEXICON"))
    ['control', 'kernel', 'original']
    >>> conditions("RULE", None, None)["control"].endswith(NO_RULE)
    True
    """
    out = {"original": PREAMBLE + original, "control": PREAMBLE + NO_RULE}
    if kernel is not None:
        out["kernel"] = PREAMBLE + (lexicon or "") + "\n\n" + kernel
    return out


def run_seat(s: Seat, systems: dict[str, str], items: tuple[Item, ...]) -> list[Run]:
    user = render_questions(items)
    ids = [i.id for i in items]
    runs = []
    for condition, system in systems.items():
        try:
            reply = s.answer(system, user)
            answers = parse_answers(reply, ids)
            error = None if answers else f"no parseable JSON answer; the reply began {reply[:80]!r}"
        except (ProbeError, OSError, subprocess.SubprocessError, ValueError) as exc:
            answers, error = {}, str(exc)[:300]
        correct = sum(1 for i in items if is_correct(i, answers.get(i.id)))
        runs.append(Run(s.id, s.vendor, condition, correct, len(items), answers, error))
    return runs


@dataclass(frozen=True)
class Verdict:
    seat: str
    vendor: str
    status: str  # pass | fail | vacuous | error | untested
    detail: str


def judge_seat(runs: list[Run], margin: float, control_gap: float) -> Verdict:
    by = {r.condition: r for r in runs}
    head = runs[0]
    if any(r.error for r in runs):
        return Verdict(head.seat, head.vendor, "error",
                       "; ".join(f"{r.condition}: {r.error}" for r in runs if r.error))
    orig, ctrl = by["original"].points, by["control"].points
    if orig - ctrl < control_gap:
        return Verdict(head.seat, head.vendor, "vacuous",
                       f"control {ctrl:.0f} vs original {orig:.0f}: the items did not need the rule")
    if "kernel" not in by:
        return Verdict(head.seat, head.vendor, "untested",
                       f"original {orig:.0f}, control {ctrl:.0f}: items discriminate")
    kern = by["kernel"].points
    status = "pass" if kern >= orig - margin else "fail"
    return Verdict(head.seat, head.vendor, status,
                   f"kernel {kern:.0f} vs original {orig:.0f} (margin {margin:.0f}), control {ctrl:.0f}")


def gate(verdicts: list[Verdict], min_seats: int = 5, min_vendors: int = 3) -> tuple[bool, str]:
    """Every usable seat must pass, and enough seats and vendors must be usable."""
    usable = [v for v in verdicts if v.status in ("pass", "fail")]
    failed = [v.seat for v in usable if v.status == "fail"]
    vendors = {v.vendor for v in usable}
    if failed:
        return False, f"kernel below the original on: {', '.join(failed)}"
    if len(usable) < min_seats or len(vendors) < min_vendors:
        return False, (f"only {len(usable)} usable seat(s) from {len(vendors)} vendor(s); "
                       f"the gate needs {min_seats} from {min_vendors}")
    return True, f"{len(usable)} seats from {len(vendors)} vendors all within the margin"


def babel_card(rule: str, runs: list[Run], verdicts: list[Verdict], outcome: tuple[bool, str]) -> str:
    """The Babel card: one row per model, the three scores, and the verdict."""
    points = {(r.seat, r.condition): r.points for r in runs if not r.error}
    lines = [f"BABEL CARD — {rule}  ({runs[0].total if runs else 0} items; one item = "
             f"{100 / runs[0].total if runs and runs[0].total else 0:.1f} points)",
             f"  {'model':<30} {'original':>8} {'kernel':>7} {'control':>8}  verdict"]
    for v in verdicts:
        cells = [points.get((v.seat, c)) for c in ("original", "kernel", "control")]
        shown = [f"{c:>7.0f}" if c is not None else "      -" for c in cells]
        lines.append(f"  {v.seat:<30} {shown[0]:>8} {shown[1]:>7} {shown[2]:>8}  "
                     f"{v.status.upper()}: {v.detail}")
    if judged_a_kernel(runs):
        lines.append(f"  GATE: {'PASS' if outcome[0] else 'NOT PASSED'} — {outcome[1]}")
    else:
        lines.append("  GATE: not judged — a validation run has no kernel condition")
    return "\n".join(lines)


def judged_a_kernel(runs: list[Run]) -> bool:
    """True for a gate run. A validation run asks no kernel condition, so the
    kernel gate was never in play; the card and the exit code both ask this."""
    return any(r.condition == "kernel" for r in runs)


def _complete_seats(runs: list[Run]) -> dict[str, dict[str, Run]]:
    """Seats whose original, kernel and control runs all completed."""
    by_seat: dict[str, dict[str, Run]] = {}
    for run in runs:
        by_seat.setdefault(run.seat, {})[run.condition] = run
    return {seat: by for seat, by in by_seat.items()
            if all(c in by and not by[c].error for c in ("original", "kernel", "control"))}


def _question_line(item: Item, right: dict[str, set[str]], seats: dict[str, dict[str, Run]]) -> str:
    lines = [f"  {item.id}  original {len(right['original'])}/{len(seats)}  "
             f"kernel {len(right['kernel'])}/{len(seats)}  {item.clause}"]
    accepted = list(item.accept) or [item.answer]
    for seat in sorted(right["original"] - right["kernel"]):
        lines.append(f"      {seat}: the kernel run answered "
                     f"{seats[seat]['kernel'].answers.get(item.id)!r}; accepted {accepted[:3]}")
    return "\n".join(lines)


def question_view(items: tuple[Item, ...], runs: list[Run]) -> str:
    """Which questions the kernel lost against the original, and on which seats.

    A fact the kernel genuinely lost fails on most seats that answered it with
    the original; noise scatters across questions and seats. Only seats whose
    three runs all completed are counted, and questions the two conditions
    scored alike are left out, so the view lists exactly what differs.
    """
    seats = _complete_seats(runs)
    head = f"BY QUESTION — {len(items)} questions, {len(seats)} seat(s) with all three runs"
    if not seats:
        return head + "\n  no seat has a completed kernel run to compare"
    rows = [(item, {c: {s for s, by in seats.items() if is_correct(item, by[c].answers.get(item.id))}
                    for c in ("original", "kernel")}) for item in items]
    changed = sorted((row for row in rows if row[1]["original"] != row[1]["kernel"]),
                     key=lambda row: -len(row[1]["original"] - row[1]["kernel"]))
    if not changed:
        return head + "\n  the kernel and the original scored the same on every question"
    return "\n".join([head, *(_question_line(item, right, seats) for item, right in changed)])


POOL_MARGIN = 5.0  # points; the default --margin, shared with the per-seat gate
POOL_REPS = 4000


def _item_differences(item: Item, seats: dict[str, dict[str, Run]]) -> list[int]:
    """Kernel minus original on one question, one entry per seat (-1, 0 or +1)."""
    return [int(is_correct(item, by["kernel"].answers.get(item.id)))
            - int(is_correct(item, by["original"].answers.get(item.id))) for by in seats.values()]


def _draw(rng: random.Random, qids: list[str], sums: dict[str, int], counts: dict[str, int]) -> float:
    sample = rng.choices(qids, k=len(qids))
    return 100 * sum(map(sums.__getitem__, sample)) / sum(map(counts.__getitem__, sample))


def pooled_difference(items: tuple[Item, ...], runs: list[Run],
                      reps: int = POOL_REPS) -> tuple[float, float, float] | None:
    """Kernel minus original in points, pooled over every seat and question.

    Returns (difference, low, high), where low and high bound a 95% interval
    from a bootstrap that resamples QUESTIONS: seats answering the same question
    are correlated, so the question is the unit. Seeded, so the same runs always
    give the same numbers. None when no seat has all three runs.

    Measured on 2026-09-29 (verify-canonical, 47 questions, three runs): the
    per-seat gate rejected an identical text in 3 of 6 pairings of the original
    with its own reruns, while this interval accepted all three pairings
    (differences 0.0 to -1.4) and rejected the version 2 kernel in all three runs
    (about -36 to -41). The runs are
    eval/kern/gate/verify-canonical-not-claimed.v2.heldout{,.run2,.run3}.json on
    branch feat/kern-w1b-pilot (the KERN pilot, draft GRIP#6731), not on main.
    """
    seats = _complete_seats(runs)
    if not seats:
        return None
    diffs = {item.id: _item_differences(item, seats) for item in items}
    qids = sorted(diffs)
    sums = {q: sum(diffs[q]) for q in qids}
    counts = {q: len(diffs[q]) for q in qids}
    rng = random.Random(0)
    draws = sorted(_draw(rng, qids, sums, counts) for _ in range(reps))
    point = 100 * sum(sums.values()) / sum(counts.values())
    return point, draws[int(0.025 * reps)], draws[int(0.975 * reps)]


def pooled_line(pooled: tuple[float, float, float] | None, margin: float = POOL_MARGIN) -> str:
    """Non-inferior when the whole interval lies above -margin, the same
    --margin the per-seat gate was given."""
    if pooled is None:
        return "  POOLED: no seat has all three runs"
    diff, low, high = pooled
    verdict = "non-inferior" if low > -margin else "not shown non-inferior"
    return (f"  POOLED (every seat and question; reported beside the gate, not part of it): "
            f"kernel {diff:+.1f} points vs the original, 95% CI [{low:+.1f}, {high:+.1f}]: "
            f"{verdict} at a {margin:g}-point margin")


def to_json(item_set: ItemSet, runs: list[Run], verdicts: list[Verdict],
            outcome: tuple[bool, str], kernel_sha: str | None,
            pooled: tuple[float, float, float] | None = None, margin: float = POOL_MARGIN) -> dict:
    return {"rule": item_set.rule, "source_commit": item_set.source_commit,
            "source_sha256": item_set.source_sha256, "kernel_sha256": kernel_sha,
            "bank_sha256": item_set.bank_sha256 or None,
            "items": len(item_set.items), "runs": [asdict(r) for r in runs],
            "verdicts": [asdict(v) for v in verdicts],
            "gate": {"passed": outcome[0], "reason": outcome[1]},
            "pooled": {**dict(zip(("difference", "low", "high"), pooled)), "margin": margin}
            if pooled else None}
