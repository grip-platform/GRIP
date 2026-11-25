#!/usr/bin/env python3
"""lib/decide_quick.py -- ask Jev one or more typed questions about a piece of text.

The backend for the ``/decide`` skill (``skills/decide/SKILL.md``): a small,
scriptable CLI over HAL's ``decide`` capability
(``lib.hal_llm_adapter._query_grip_decide`` -- no new network send point).
Reads ``state`` from ``--state``, ``--state-file`` or stdin, asks one or more
typed questions in ONE Jev request, and prints each answer with its
probabilities, the model id that answered, and the fixed disclaimer
"machine reading, precision unmeasured". An answer in TypeSafe's own
0.30-0.70 uncertain band is reported as "uncertain", never as a verdict
(docs/decide.md in HAL; the Jev integration plan, ``~/.grip/plans/
snug-questing-quill.md``).

Every send goes through the decision-model privacy floor first
(``lib.decision_privacy.may_send``): internal and public text only, per the
operator's limit (``rules/personal-matters-stay-private.md``). When the
floor refuses, nothing is sent and the CLI exits non-zero with the reason on
one line.

A Choice question is asked in BOTH option orders and the per-option
probabilities averaged, because Jev favours the first-listed option
(TypeSafe's own documented weakness). See ``_average_choice``.

``--seal`` records the question text, a SHA-256 digest of ``state`` (never
the state itself), the answers and the model id as a signed decision record,
reusing the SAME signer every other GRIP tool uses
(``lib.precog.idr.build_idr`` / ``append_idr`` -- see ``lib.sovereign_council``
for the reference call site). This module writes no new signer. When only a
demo (HMAC, forgeable-by-design) seal is possible under the running
interpreter, it says so plainly instead of printing success
(``rules/never-system-python-for-grip.md``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_LIB = Path(__file__).resolve().parent
# Both of lib/ (bare-module imports -- none here, but siblings follow this
# same preamble) AND its parent (the package-qualified `from lib.X import Y`
# imports this module's deferred imports use, e.g. lib.decision_privacy,
# lib.hal_llm_adapter, lib.precog.idr) go on sys.path. The latter resolves
# ONLY when the repo root is on sys.path; without it, run as a script from
# any cwd, it fails with `No module named 'lib'` (lib/grip_save.py carries
# the same fix, with the fuller incident write-up: AGORA #5070).
for _p in (str(_LIB), str(_LIB.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: A manually-invoked CLI, not a hook on a fixed budget -- generous headroom
#: for a batch of several typed questions in one call (vendor: latency stays
#: flat to about 64 questions; SDK default overall budget is 30s).
_TIMEOUT_S = 15.0
#: TypeSafe's own uncertain band: an answer here is reported as "uncertain",
#: never as the model's verdict (docs/decide.md).
_UNCERTAIN_LOW, _UNCERTAIN_HIGH = 0.30, 0.70
#: Printed beside every answered question -- never omitted, never softened.
MACHINE_READING = "machine reading, precision unmeasured"

Decide = Callable[[str, dict], Optional[dict]]


class UsageError(ValueError):
    """A bad CLI invocation. Printed on one line, exit 2 -- never a Jev failure."""


@dataclass(frozen=True)
class Answer:
    """One typed question's reading.

    ``judge`` is the single scalar the uncertain-band check applies to: the
    probability for a Noul, the top averaged probability for a Choice, the
    confidence for a Score. ``detail`` carries the kind-specific numbers
    (probabilities, the chosen option, the nearest level) for rendering.
    """
    id: str
    kind: str  # "noul" | "choice" | "score"
    ok: bool = False
    judge: Optional[float] = None
    detail: Dict[str, object] = field(default_factory=dict)

    @property
    def uncertain(self) -> bool:
        return self.ok and self.judge is not None and _UNCERTAIN_LOW <= self.judge <= _UNCERTAIN_HIGH


# --- CLI surface -------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decide_quick.py",
        description="Ask Jev (TypeSafe's decision model, via HAL decide) one or "
                    "more typed questions about a piece of internal text.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--state", help="the text to ask about, given inline")
    source.add_argument("--state-file", help="read the text to ask about from this file")
    parser.add_argument("--noul", nargs=2, action="append", default=[],
                        metavar=("ID", "QUESTION"),
                        help="a yes/no question; may be given more than once")
    parser.add_argument("--choice", nargs=2, action="append", default=[],
                        metavar=("ID", "QUESTION"),
                        help="pick-one question; pair with --options")
    parser.add_argument("--options", action="append", default=[],
                        metavar="a,b,c",
                        help="comma-separated options for a --choice; the Kth "
                             "--options pairs with the Kth --choice")
    parser.add_argument("--score", nargs=2, action="append", default=[],
                        metavar=("ID", "QUESTION"),
                        help="how-much question; pair with --levels")
    parser.add_argument("--levels", action="append", default=[],
                        metavar="low|mid|high",
                        help="pipe-separated ordered levels for a --score; the "
                             "Kth --levels pairs with the Kth --score")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--seal", action="store_true",
                        help="seal this call as a signed decision record")
    return parser


def _split(raw: str, sep: str) -> List[str]:
    return [part.strip() for part in raw.split(sep) if part.strip()]


def _build_plan(args: argparse.Namespace) -> Tuple[dict, List[Tuple[str, str, object]], Dict[str, str]]:
    """``(wire_questions, plan, question_text)``.

    ``wire_questions`` is what is actually sent to Jev -- a Choice expands
    into two questions, ``{id}__fwd`` and ``{id}__rev`` (the same options,
    forward then reversed), so the order bias cancels on average. ``plan``
    says how to read the result back per the CALLER's own id; ``question_text``
    is each id's instructions, kept for rendering and for ``--seal``.
    """
    wire: dict = {}
    plan: List[Tuple[str, str, object]] = []
    qtext: Dict[str, str] = {}
    seen: set = set()

    def claim(id_: str) -> None:
        if id_ in seen:
            raise UsageError(f"duplicate question id {id_!r}")
        seen.add(id_)

    for id_, question in args.noul:
        claim(id_)
        wire[id_] = {"type": "noul", "instructions": question}
        plan.append((id_, "noul", None))
        qtext[id_] = question

    if len(args.choice) != len(args.options):
        raise UsageError(
            f"--choice given {len(args.choice)} time(s) but --options given "
            f"{len(args.options)} time(s) -- give one --options per --choice, "
            "in the same order")
    for (id_, question), raw_options in zip(args.choice, args.options):
        claim(id_)
        opts = _split(raw_options, ",")
        if len(opts) < 2:
            raise UsageError(f"--choice {id_!r} needs at least 2 options, got {raw_options!r}")
        wire[f"{id_}__fwd"] = {"type": "choice", "instructions": question,
                               "criteria": {o: None for o in opts}}
        wire[f"{id_}__rev"] = {"type": "choice", "instructions": question,
                               "criteria": {o: None for o in reversed(opts)}}
        plan.append((id_, "choice", opts))
        qtext[id_] = question

    if len(args.score) != len(args.levels):
        raise UsageError(
            f"--score given {len(args.score)} time(s) but --levels given "
            f"{len(args.levels)} time(s) -- give one --levels per --score, "
            "in the same order")
    for (id_, question), raw_levels in zip(args.score, args.levels):
        claim(id_)
        levels = _split(raw_levels, "|")
        if len(levels) < 2:
            raise UsageError(f"--score {id_!r} needs at least 2 levels, got {raw_levels!r}")
        wire[id_] = {"type": "score", "instructions": question, "criteria": levels}
        plan.append((id_, "score", levels))
        qtext[id_] = question

    if not plan:
        raise UsageError("give at least one question: --noul, --choice or --score")
    return wire, plan, qtext


def _read_state(args: argparse.Namespace) -> str:
    if args.state is not None:
        return args.state
    if args.state_file is not None:
        return Path(args.state_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def _state_cwd_hint(args: argparse.Namespace) -> str:
    """Where the privacy floor's guardian-repo check should look.

    A ``--state-file`` inside the guardian repository must refuse even when
    the CLI itself was launched from elsewhere; inline text and stdin have no
    such anchor, so the process cwd is the honest fallback.
    """
    if args.state_file is not None:
        return os.path.dirname(os.path.abspath(args.state_file))
    return os.getcwd()


# --- reading answers back -----------------------------------------------------
def _num(value) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok else None


def _choice_probs(answer) -> Optional[Dict[str, float]]:
    probs = answer.get("probabilities") if isinstance(answer, dict) else None
    return probs if isinstance(probs, dict) else None


def _average_choice(opts: Sequence[str], fwd, rev) -> Optional[Dict[str, float]]:
    """Each option's probability averaged across both option orders.

    None when either call gave no usable Choice answer -- a half-averaged
    reading would silently pretend the de-biasing happened.
    """
    pf, pr = _choice_probs(fwd), _choice_probs(rev)
    if pf is None or pr is None:
        return None
    out: Dict[str, float] = {}
    for opt in opts:
        a, b = _num(pf.get(opt)), _num(pr.get(opt))
        if a is None or b is None:
            return None
        out[opt] = (a + b) / 2
    return out


def _read_noul(id_: str, answers: dict) -> Answer:
    answer = answers.get(id_)
    p = _num(answer.get("noul")) if isinstance(answer, dict) else None
    if p is None or not 0 <= p <= 1:
        return Answer(id_, "noul")
    return Answer(id_, "noul", True, p, {"p": p})


def _read_choice(id_: str, opts: Sequence[str], answers: dict) -> Answer:
    probs = _average_choice(opts, answers.get(f"{id_}__fwd"), answers.get(f"{id_}__rev"))
    if probs is None:
        return Answer(id_, "choice")
    chosen = max(probs, key=probs.get)
    n = len(opts)
    # TypeSafe's own confidence formula (the Jev integration plan, "What Jev
    # is"): (p_max - 1/n) / (1 - 1/n). n >= 2 is enforced in _build_plan.
    confidence = (probs[chosen] - 1 / n) / (1 - 1 / n)
    return Answer(id_, "choice", True, probs[chosen],
                  {"choice": chosen, "probabilities": probs, "confidence": confidence})


def _read_score(id_: str, levels: Sequence[str], answers: dict) -> Answer:
    answer = answers.get(id_)
    if not isinstance(answer, dict):
        return Answer(id_, "score")
    score, confidence = _num(answer.get("score")), _num(answer.get("confidence"))
    probs_raw = answer.get("probabilities")
    if score is None or confidence is None or not isinstance(probs_raw, dict):
        return Answer(id_, "score")
    probs: Dict[str, float] = {}
    for i, level in enumerate(levels):
        p = _num(probs_raw.get(str(i)))
        if p is None:
            return Answer(id_, "score")
        probs[level] = p
    nearest = levels[max(0, min(len(levels) - 1, round(score)))]
    return Answer(id_, "score", True, confidence,
                  {"score": score, "nearest_level": nearest, "probabilities": probs})


def read_answers(plan: Sequence[Tuple[str, str, object]], result: Optional[dict]) -> List[Answer]:
    """One :class:`Answer` per planned question, in order. Never raises."""
    ok = isinstance(result, dict) and bool(result.get("ok"))
    answers = (result.get("answers") or {}) if ok else {}
    out: List[Answer] = []
    for id_, kind, meta in plan:
        if kind == "noul":
            out.append(_read_noul(id_, answers))
        elif kind == "choice":
            out.append(_read_choice(id_, meta, answers))
        else:
            out.append(_read_score(id_, meta, answers))
    return out


# --- sending -------------------------------------------------------------------
def _floor(text: str, cwd: Optional[str]) -> Tuple[bool, str]:
    try:
        from lib.decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from lib.hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=_TIMEOUT_S)


def ask(state: str, wire_questions: dict, *, cwd: Optional[str] = None,
        decide: Optional[Decide] = None) -> Tuple[Optional[dict], str]:
    """``(result, "")`` on a send, or ``(None, why)`` when the floor refused it.

    A transport failure (hal-server down, no route, a timeout) is NOT the
    floor refusing -- it is a normal, reportable "no answer", same as any
    other caller of ``_query_grip_decide``.

    ``decide`` defaults to ``None`` rather than binding ``_default_decide``
    as a default VALUE -- a Python default is captured once, at `def` time,
    so a caller (``main()``) that never passes ``decide`` explicitly would
    keep calling the ORIGINAL function object even after a test monkeypatches
    the module attribute of the same name. Resolving it here, each call,
    reads whatever ``_default_decide`` currently names.
    """
    if decide is None:
        decide = _default_decide
    allowed, why = _floor(state, cwd)
    if not allowed:
        return None, f"privacy_floor:{why}"
    try:
        result = decide(state, wire_questions)
    except Exception:  # noqa: BLE001 -- advisory; never raises into the caller
        result = None
    return result, ""


# --- rendering -----------------------------------------------------------------
def _summary(answer: Answer) -> str:
    d = answer.detail
    if answer.kind == "noul":
        return f"p={d['p']:.2f}"
    if answer.kind == "choice":
        per_option = " ".join(f"{k}={v:.2f}" for k, v in d["probabilities"].items())
        return (f"{d['choice']} (p={d['probabilities'][d['choice']]:.2f}, "
                f"confidence={d['confidence']:.2f}) [{per_option}]")
    per_level = " ".join(f"{k}={v:.2f}" for k, v in d["probabilities"].items())
    return f"{d['nearest_level']} (score={d['score']:.2f}, confidence={answer.judge:.2f}) [{per_level}]"


def render_text(answer: Answer, question_text: str, model: str) -> str:
    lines = [f"[{answer.id}] type={answer.kind}", f"  question: {question_text}"]
    if not answer.ok:
        lines.append("  answer: no answer (Jev unreachable, or gave a malformed reply)")
        return "\n".join(lines)
    label = "uncertain" if answer.uncertain else "answer"
    lines.append(f"  {label}: {_summary(answer)}")
    tail = f" (model {model})" if model else ""
    lines.append(f"  {MACHINE_READING}{tail}")
    return "\n".join(lines)


def render_json(answer: Answer, question_text: str, model: str) -> dict:
    payload = {
        "id": answer.id, "type": answer.kind, "question": question_text,
        "ok": answer.ok, "uncertain": answer.uncertain if answer.ok else None,
        "model": model, "machine_reading": True, "calibrated": False, "note": MACHINE_READING,
    }
    payload.update(answer.detail)
    return payload


# --- sealing ---------------------------------------------------------------
def seal(question_text: Dict[str, str], state: str, answers: Sequence[Answer], model: str,
        *, idr_path: Optional[Path] = None) -> dict:
    """Seal this call as a signed decision record. Never raises.

    Reuses the canonical signer (``lib.precog.idr``) every other GRIP tool
    seals through -- see ``lib.sovereign_council._seal_idr`` for the
    reference call site. ``state`` is hashed, never stored verbatim.
    ``idr_path`` overrides the chain file (tests only -- the CLI never
    passes it, so a real ``/decide --seal`` always writes GRIP's one shared
    chain).
    """
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    try:
        from lib.precog.idr import CUSTODY_DEMO, append_idr, audit_custody_tier, build_idr
    except Exception as exc:  # noqa: BLE001 -- sealing is optional provenance, never fatal
        return {"sealed": False, "reason": f"idr_unavailable: {type(exc).__name__}: {exc}"}
    decision = {
        "questions": question_text,
        "state_sha256": digest,
        "answers": {a.id: {"ok": a.ok, "judge": a.judge, **a.detail} for a in answers},
        "model": model,
    }
    try:
        idr = build_idr(prompt="grip-decide", fingerprint="decide-quick",
                        decision=decision, predecessor_idr=None, depth=0, kind="grip-decide")
        append_idr(idr, path=idr_path)
    except Exception as exc:  # noqa: BLE001 -- never crash the CLI on a seal failure
        return {"sealed": False, "reason": f"seal_failed: {type(exc).__name__}: {exc}"}
    tier = audit_custody_tier(idr.audit)
    return {"sealed": True, "idr_id": idr.id, "custody": tier, "demo": tier == CUSTODY_DEMO}


def render_seal(report: dict) -> str:
    if not report.get("sealed"):
        return f"seal: not sealed -- {report.get('reason', 'unknown')}"
    if report.get("demo"):
        return (f"seal: {report['idr_id']} -- DEMO KEY ONLY (custody={report['custody']}); "
                "NOT a verifiable signature on this interpreter "
                "(rules/never-system-python-for-grip.md: run under bin/grip-py)")
    return f"seal: {report['idr_id']} (custody={report['custody']})"


# --- entry point -------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        wire, plan, qtext = _build_plan(args)
    except UsageError as exc:
        print(f"decide: {exc}", file=sys.stderr)
        return 2
    try:
        state = _read_state(args)
    except OSError as exc:
        print(f"decide: cannot read state: {exc}", file=sys.stderr)
        return 2

    result, skipped = ask(state, wire, cwd=_state_cwd_hint(args))
    if skipped:
        print(f"decide: not sent -- {skipped}", file=sys.stderr)
        return 3

    answers = read_answers(plan, result)
    model = str((result or {}).get("model") or "")

    if args.json:
        print(json.dumps([render_json(a, qtext[a.id], model) for a in answers], indent=2))
    else:
        for a in answers:
            print(render_text(a, qtext[a.id], model))

    if args.seal:
        print(render_seal(seal(qtext, state, answers, model)))

    return 0 if any(a.ok for a in answers) else 1


if __name__ == "__main__":
    # This shebang is `/usr/bin/env python3`, which need not be an interpreter
    # that can dual-sign. Without this `--seal` would write a forgeable demo
    # seal when a real one was one interpreter away (lib/pq_reexec.py).
    #
    # Imported as top-level `pq_reexec` off THIS checkout, never as
    # `lib.pq_reexec`: by now `lib` may already be bound to another tree's
    # package, and the import would miss the file or read another branch's copy.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))
    sys.exit(main())
