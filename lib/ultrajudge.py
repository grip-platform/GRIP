#!/usr/bin/env python3
"""ultrajudge.py — Multi-model verdict council with role assignment.

Usage:
    python3 -m lib.ultrajudge "Should we adopt the syscall doctrine repo-wide?"
    python3 -m lib.ultrajudge --roles devil,pragmatist,futurist "<question>"
    python3 -m lib.ultrajudge --models opus,grok,sonnet,llama "<question>"
    python3 -m lib.ultrajudge --weights '{"devil": 2.0, "pragmatist": 1.5}' "<question>"

Dispatch is live HAL by default (``ULTRAJUDGE_USE_HAL=1``); set
``ULTRAJUDGE_USE_HAL=0`` for the labelled stub path (hermetic tests, explicit
offline runs). On the live path, a council whose every seat degraded to the
stub REFUSES to emit a deliverable rather than presenting canned verdicts as
a judgment (HALUnavailableError).

Writes the deliverable to $CLAUDE_DIR/state/ultrajudge/<slug>-<timestamp>.md
(-2, -3 ... when that name is taken) and prints the path.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional


def _claude_dir() -> Path:
    """$CLAUDE_DIR, else ~/.claude, through the shared shim. It was the folder
    this file sits in, whatever CLAUDE_DIR said, so a private run from a test
    install wrote its report into the live one (GRIP#9182)."""
    try:
        from lib.claude_dir import get_claude_dir
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from claude_dir import get_claude_dir
    return get_claude_dir()


CLAUDE_DIR = _claude_dir()
STATE_DIR = CLAUDE_DIR / "state" / "ultrajudge"
DEFAULT_ROLES = ["devil", "pragmatist", "futurist", "integrator", "historian"]
# Four seats, four DIFFERENT providers, each measured serving on 2026-09-20.
# The previous default was ["opus", "sonnet", "grok-3", "gpt-oss-120b"], and
# BOTH Anthropic seats returned nothing — so every default council ran two live
# seats and two canned stubs, while the stubs still voted. Anthropic is not
# reachable from this path at all: the gateway does not know `claude-*` ids and
# the ladder's anthropic rung is dead, so Opus and Fable are `claude -p` only.
# They remain requestable by name; they are no longer a default that silently
# fails.
DEFAULT_MODELS = ["astra", "grok", "gpt-oss-120b", "gemini"]
VERDICT_SCORE = {"proceed": 1.0, "rethink": 0.0, "abandon": -1.0}


class HALUnavailableError(RuntimeError):
    """Raised when the live path could not reach HAL for ANY council seat.

    V>>'s standing position (ULTRAPROMPT-hal-wiring-perimeter §1): "I'd
    rather it emit nothing than a fabricated PROCEED." A refusal beats a
    fully-canned deliverable wearing a council's clothes.
    """


_ROLE_DEFAULTS: dict[str, tuple[str, float, str]] = {
    "devil":      ("rethink", 0.71, "Strongest counterargument: unproven assumptions."),
    "pragmatist": ("proceed", 0.82, "Cost/benefit favours action; delay costs more."),
    "futurist":   ("proceed", 0.68, "12-month trajectory shows compounding returns."),
    "integrator": ("proceed", 0.80, "Synthesis: proceed with monitoring and rollback."),
    "historian":  ("proceed", 0.75, "Prior art confirms viability; two precedents."),
}

_ROLE_INSTRUCTIONS: dict[str, str] = {
    "devil": (
        "You are the DEVIL'S ADVOCATE: construct the strongest case AGAINST, "
        "attack unproven assumptions, and name the failure mode most likely "
        "to be dismissed."
    ),
    "pragmatist": (
        "You are the PRAGMATIST: weigh concrete cost/benefit, delivery risk, "
        "and what shipping this actually takes versus what delaying costs."
    ),
    "futurist": (
        "You are the FUTURIST: project the 12-month trajectory — compounding "
        "effects, lock-in, and what this decision forecloses or enables."
    ),
    "integrator": (
        "You are the INTEGRATOR: synthesise the strongest overall "
        "recommendation, naming the monitoring and rollback conditions under "
        "which it holds."
    ),
    "historian": (
        "You are the HISTORIAN: check whether this has been tried before, "
        "name the closest precedents, and say what happened to them."
    ),
}

_ULTRAJUDGE_PROMPT = """You are one voice on a multi-model verdict council. Your role: {role}.

{role_instruction}

QUESTION UNDER JUDGMENT:
{question}

Return ONLY a JSON object (no prose before or after) matching exactly:
{{
  "verdict": "<proceed|rethink|abandon>",
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<2-4 sentences of role-specific reasoning about THIS question>",
  "key_evidence": ["<specific evidence item grounded in the question>"]
}}

Rules: verdict must be exactly one of proceed, rethink, abandon. Reason about
the actual question above — a generic answer that could apply to any question
is a failure."""

try:  # the three-tier import fallback this module already uses elsewhere
    from lib.council_seat import canonical_label, json_object, model_address
except ImportError:  # pragma: no cover - script-mode
    try:
        from council_seat import canonical_label, json_object, model_address
    except ImportError:
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parent))
        from council_seat import canonical_label, json_object, model_address

# Backward-compat alias: tests/test_ultrajudge.py imports this name directly
# (`from lib.ultrajudge import _model_address`). lib/council_seat.py is the
# shared home now (GRIP#9187 -- lib/ultraread.py needed the identical logic,
# which it was NOT getting: its own dispatch called adapter.generate(), a
# method with no model parameter at all).
_model_address = model_address


try:  # the three-tier import fallback this module already uses elsewhere
    from lib.seat_answer_guard import classify as classify_seat, explain as seat_explain
except ImportError:  # pragma: no cover - script-mode
    try:
        from seat_answer_guard import classify as classify_seat, explain as seat_explain
    except ImportError:
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parent))
        from seat_answer_guard import classify as classify_seat, explain as seat_explain


def _dispatch_stub(model: str, role: str, question: str) -> dict:
    """Labelled stub dispatcher — canned per-role response.

    Used for hermetic tests (``ULTRAJUDGE_USE_HAL=0``) and as the per-pair
    fail-open fallback when a live call degrades. EVERY string field
    self-identifies as stub output so a canned verdict can never again be
    mistaken for a live one. The ``question`` is deliberately unread here:
    a stub performs no live analysis, and it says so.
    """
    verdict, confidence, reasoning = _ROLE_DEFAULTS.get(
        role, ("rethink", 0.50, "Unknown role — stub default.")
    )
    return {
        "model": f"{model}-stub",
        "requested_model": model,
        "role": role,
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": f"[stub] {reasoning} Stub: no live analysis performed.",
        "key_evidence": [f"[{model}-stub/{role}] Stub evidence item."],
        "source": "stub",
    }


def _parse_verdict_json(text: str) -> dict:
    """Parse and validate a model's verdict JSON. Raises ValueError on any
    contract violation so the caller can fall back to the labelled stub."""
    parsed = json_object(text)
    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in VERDICT_SCORE:
        raise ValueError(f"invalid verdict {verdict!r}")
    confidence = float(parsed["confidence"])
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence out of range: {confidence}")
    reasoning = str(parsed.get("reasoning", "")).strip()
    if not reasoning:
        raise ValueError("empty reasoning")
    evidence = parsed.get("key_evidence") or []
    if not isinstance(evidence, list):
        evidence = [evidence]
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": reasoning,
        "key_evidence": [str(e) for e in evidence],
    }


def _build_prompt(role: str, question: str) -> str:
    """Assemble the role-steered council prompt WITH the question in it."""
    instruction = _ROLE_INSTRUCTIONS.get(
        role,
        f"You are the {role.upper()} expert lens: judge from that role's "
        "distinct vantage point.",
    )
    return _ULTRAJUDGE_PROMPT.format(
        role=role, role_instruction=instruction, question=question,
    )


def _live_verdict(result, parsed: dict, model: str, role: str) -> dict:
    """Assemble a live verdict dict, naming the model HAL ACTUALLY served."""
    served = result.model or f"{model} (route unreported)"
    provenance = result.provenance() if hasattr(result, "provenance") else served
    return {
        "model": served,
        "requested_model": model,
        "role": role,
        "verdict": parsed["verdict"],
        "confidence": parsed["confidence"],
        "reasoning": parsed["reasoning"],
        "key_evidence": parsed["key_evidence"],
        "source": "hal",
        "provenance": provenance,
    }


def _resolve_hal_adapter_factory() -> Optional[Callable]:
    """Import HALLLMAdapter via the three-tier fallback this module needs
    elsewhere too (relative -> lib.-absolute -> bare): running this file as
    a script leaves no parent package, so the relative import alone would
    turn every seat into a stub with HAL blamed for an import error it never
    made (measured 2026-09-20). Returns None when none of the three resolve.
    """
    try:
        from .hal_llm_adapter import HALLLMAdapter  # type: ignore
    except ImportError:
        try:
            from lib.hal_llm_adapter import HALLLMAdapter  # type: ignore
        except ImportError:
            try:
                from hal_llm_adapter import HALLLMAdapter  # type: ignore
            except ImportError:
                return None
    return lambda: HALLLMAdapter().connect()


def _unavailable(role: str, model: str, reasoning: str) -> dict:
    """The 'seat gave no answer to judge' shape — distinct from a stub (a
    stub is canned text; this seat was never served, or the harness talked
    to its operator). Neither votes (see compute_weighted_verdict)."""
    return {"role": role, "model": model, "verdict": "unavailable",
            "confidence": 0.0, "source": "unavailable", "reasoning": reasoning}


def _run_seat(adapter, model: str, role: str, question: str) -> dict:
    """One live seat call. Exceptions propagate to _dispatch_real's except,
    which stubs the seat; PIN first (no_failover=True) so a rung HAL cannot
    serve comes back EMPTY, never silently answered by another model."""
    result = adapter.infer(
        _build_prompt(role, question),
        model=model_address(model), audit=False, no_failover=True,
    )
    served = getattr(result, "model", None)
    if served is None and not (result.text or "").strip():
        return _unavailable(role, model, f"seat not served: HAL could not reach {model}")
    if result.must_refuse():
        return _dispatch_stub(model, role, question)

    # The harness can talk to its operator instead of the model ("Autocompact
    # is thrashing...") with no error on any channel. min_chars=1: a SHORT
    # malformed reply is still the model answering (the parser below stubs it).
    seat_state = classify_seat(result.text or "", min_chars=1)
    if not seat_state.counts_as_a_vote:
        return _unavailable(role, model, seat_explain(seat_state, model))

    parsed = _parse_verdict_json(result.text or "")
    return _live_verdict(result, parsed, model, role)


def _subscription_seat_runner():
    """(runner, seats): lib/sovereign_council's _subprocess_call and the GPT
    seats it serves on the ChatGPT subscription, imported the same three-tier
    way as HAL's adapter; (None, set()) when the module cannot be imported."""
    try:
        from . import sovereign_council as council  # type: ignore
    except ImportError:
        try:
            from lib import sovereign_council as council  # type: ignore
        except ImportError:
            try:
                import sovereign_council as council  # type: ignore
            except ImportError:
                return None, set()
    seats = {name for name, spec in council.SUBPROCESS_SPEAKERS.items()
             if spec.get("family") == "openai"}
    return council._subprocess_call, seats


def _run_subscription_seat(runner: Callable, model: str, role: str, question: str) -> dict:
    """One GPT seat on the ChatGPT subscription, through the council's Codex
    speaker (GRIP#9245). HAL refuses OpenAI's per-token route
    (PAID_OPENAI_DISABLED), and GPT never runs on credits
    (rules/gpt-on-the-subscription-never-credits.md). As with a HAL seat, a
    seat that gives no answer is unavailable, never a stub or a vote."""
    reply = runner(model, [{"role": "user", "content": _build_prompt(role, question)}], time.time())
    text = str(reply.get("text") or "")
    if not reply.get("ok"):
        return _unavailable(role, model, f"seat not served on the subscription: {text[:200]}")
    seat_state = classify_seat(text, min_chars=1)
    if not seat_state.counts_as_a_vote:
        return _unavailable(role, model, seat_explain(seat_state, model))
    served = reply.get("model") or model
    verdict = _live_verdict(SimpleNamespace(text=text, model=served),
                            _parse_verdict_json(text), model, role)
    verdict.update(source="subscription",
                   provenance=f"{served} on the ChatGPT subscription (Codex)")
    return verdict


def _dispatch_real(model: str, role: str, question: str,
                   adapter_factory: Optional[Callable] = None) -> dict:
    """Live HAL dispatch via lib/hal_llm_adapter.HALLLMAdapter — one real
    provider call per (role, model) pair, with the QUESTION IN THE PROMPT.
    The result names the model HAL ACTUALLY served, never merely the one
    requested (provenance honesty, ULTRAPROMPT-hal-wiring-perimeter §1
    requirement 4).

    Any failure falls back to the LABELLED stub (fail-open); run() refuses
    outright when every seat degraded (§1 requirement 5).

    adapter_factory: dependency-injection seam for tests. Default = real adapter.
    """
    if adapter_factory is None:
        # GPT seats run on the ChatGPT subscription, never through HAL's
        # per-token route (GRIP#9245). A test that injects an adapter keeps
        # every seat on it, so no test reaches the real Codex CLI.
        runner, seats = _subscription_seat_runner()
        if runner is not None and model in seats:
            try:
                return _run_subscription_seat(runner, model, role, question)
            except Exception as exc:  # a dead seat, never a crashed council
                return _unavailable(role, model, f"seat not served on the subscription: {exc}")
        adapter_factory = _resolve_hal_adapter_factory()
        if adapter_factory is None:
            return _dispatch_stub(model, role, question)
    try:
        return _run_seat(adapter_factory(), model, role, question)
    except Exception:  # fail-open per pair — run() refuses if ALL seats degrade
        return _dispatch_stub(model, role, question)


def _select_dispatch() -> Callable[[str, str, str], dict]:
    """Choose dispatch path at call time so the env toggle takes effect in tests."""
    if os.environ.get("ULTRAJUDGE_USE_HAL", "1") == "1":
        return _dispatch_real
    return _dispatch_stub


def _make_slug(question: str) -> str:
    """Convert question text to filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", question)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:60] or "question"


def _write_new_report(stem: str, md: str) -> Path:
    """Write ``md`` to the first free name of <stem>.md, <stem>-2.md, ... in
    STATE_DIR. Each name is created exclusively, so two runs of one question in
    the same second never share a file; the stem is timed to the second, and
    the second run used to replace the first (GRIP#9182). A write that raises
    removes the file it created, so it leaves no half-written report looking
    like a whole one, and the write's own error is the one raised. A process
    killed mid-write can still leave one; only a temporary file and a rename
    would close that."""
    for n in range(1, 1000):
        path = STATE_DIR / (f"{stem}.md" if n == 1 else f"{stem}-{n}.md")
        try:
            fh = path.open("x", encoding="utf-8")
        except FileExistsError:
            continue
        try:
            with fh:
                fh.write(md)
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass  # keep the write's own error: it says what went wrong
            raise
        return path
    raise FileExistsError(f"999 reports named {stem} already exist in {STATE_DIR}")


def compute_weighted_verdict(
    verdicts: list[dict],
    weights: Optional[dict[str, float]] = None,
) -> dict:
    """Compute weighted verdict from per-role results.

    Returns dict with label, weighted_confidence, raw_score.
    Deterministic: same inputs always produce same output.
    """
    if not verdicts:
        return {"label": "no-verdict", "weighted_confidence": 0.0, "raw_score": 0.0}

    # ONLY LIVE SEATS VOTE. A stub is canned text chosen by a constant table; an
    # unavailable seat was never served at all. Counting either lets a fixed
    # string outvote a model that actually reasoned — observed 2026-09-20, where
    # the sole live seat returned `rethink` at 0.90 with a substantive objection
    # and two stubs carried the headline to "PROCEED 81%". The deliverable
    # already warned that stubs were present; the verdict line did not, so a
    # reader skimming the top saw a recommendation built from filler.
    #
    # With no live seat the answer is "no-verdict" — no quorum is not a verdict
    # (rules/exogenous-anchor-law.md), and a number computed from canned rows is
    # worse than an honest absence because it looks like evidence.
    live = [v for v in verdicts if v.get("source") not in ("stub", "unavailable")]
    excluded = len(verdicts) - len(live)
    if not live:
        return {"label": "no-verdict", "weighted_confidence": 0.0,
                "raw_score": 0.0, "live_seats": 0, "excluded_seats": excluded}
    verdicts = live

    weights = weights or {}
    total_weight = 0.0
    weighted_sum = 0.0
    for v in verdicts:
        w = weights.get(v.get("role", ""), 1.0)
        score = VERDICT_SCORE.get(v.get("verdict", "rethink"), 0.0)
        conf = float(v.get("confidence", 0.5))
        weighted_sum += w * conf * score
        total_weight += w * conf
    raw = weighted_sum / total_weight if total_weight > 0 else 0.0
    label = "proceed" if raw > 0.1 else ("abandon" if raw < -0.1 else "rethink")
    return {"label": label, "weighted_confidence": (raw + 1.0) / 2.0, "raw_score": raw,
            "live_seats": len(verdicts), "excluded_seats": excluded}


def _one_line(text) -> str:
    """Model text folded onto one line. A seat's reasoning goes into the
    Markdown report, and a line of its own reading "## Recommendation" would
    otherwise become a heading the report never wrote (review of GRIP#9741)."""
    return " ".join(str(text or "").split())


def _render_table(verdicts: list[dict]) -> list[str]:
    """Build the Markdown verdict table. The Model column carries what was
    ACTUALLY served (or an explicit ``-stub`` label) — never a bare requested
    name for a model that was not called."""
    rows = [
        "| Role | Model | Verdict | Confidence | Key reasoning |",
        "|---|---|---|---|---|",
    ]
    for v in verdicts:
        # A bare "|" in model text would end the cell and shift every column after it.
        short = _one_line(v.get("reasoning", ""))[:80].replace("|", "\\|")
        rows.append(
            f"| {v.get('role','')} | {v.get('model','')} "
            f"| {v.get('verdict','')} | {v.get('confidence', 0):.2f} | {short} |"
        )
    return rows


def _render_stub_banner(verdicts: list[dict]) -> list[str]:
    """A banner — not a footnote — whenever any verdict is stub-sourced
    (§1 requirement 4). Returns [] when every seat was a live call."""
    stub_rows = [v for v in verdicts if v.get("source") == "stub"]
    if not stub_rows:
        return []
    return [
        (
            f"> **STUB VERDICTS PRESENT** — {len(stub_rows)} of {len(verdicts)} "
            "verdicts below came from the canned stub dispatcher; NO live model "
            "call was made for those rows (labelled `-stub` in the Model "
            "column). Do not treat this deliverable as a live council verdict."
        ),
        "",
    ]


def _render_unserved_banner(verdicts: list[dict]) -> list[str]:
    """Name each seat that was never served; its vote is not counted. When the
    devil's seat is one of them, nothing argued against the leading answer, so
    the confidence was never tested against dissent (GRIP#9245: a run with an
    unserved devil printed PROCEED at 100%). Returns [] when every seat served."""
    unserved = [v for v in verdicts if v.get("source") == "unavailable"]
    if not unserved:
        return []
    names = ", ".join(f"{v.get('role')} ({v.get('model')})" for v in unserved)
    lines = [f"> **{len(unserved)} of {len(verdicts)} seats unserved**: {names}. "
             "Their votes are not in the weighted verdict."]
    if any(v.get("role") == "devil" for v in unserved):
        lines += ["", "> **No case against was heard**: the devil's-advocate seat went "
                      "unserved, so the confidence above was never tested against dissent."]
    return lines + [""]


def _render_dissents(verdicts: list[dict], majority: str) -> list[str]:
    """Build ## Dissents section. Returns [] when all verdicts match majority.
    An unserved seat is no dissent: it made no case (the banner names it)."""
    dissenters = [
        v for v in verdicts
        if v.get("verdict") != majority and v.get("role") != "integrator"
        and v.get("source") != "unavailable"
    ]
    if not dissenters:
        return []
    lines: list[str] = ["## Dissents (strongest case against)", ""]
    for v in dissenters:
        lines += [f"> **{v.get('role')} ({v.get('model')})**: {_one_line(v.get('reasoning', ''))}", ""]
    return lines


def _render_seat_reasoning(verdicts: list[dict]) -> list[str]:
    """Every served seat's full reasoning, not only the dissents'. The table
    cuts reasoning at 80 characters, so a devil that agreed with the majority
    left no case on the page (GRIP#9245). An unserved seat has none to show.
    Each reasoning is folded onto one line, so model text cannot open a heading
    of its own (a "## Recommendation" line inside it would otherwise become one)."""
    served = [v for v in verdicts if v.get("source") != "unavailable"]
    if not served:
        return []
    lines: list[str] = ["## Each seat's reasoning", ""]
    for v in served:
        lines += [f"**{v.get('role', '')} ({v.get('model', '')})**: {_one_line(v.get('reasoning', ''))}", ""]
    return lines


def synthesise(question: str, verdicts: list[dict], weights: Optional[dict] = None) -> str:
    """Build the Markdown deliverable from per-role verdict results."""
    wv = compute_weighted_verdict(verdicts, weights)
    conf_pct = int(wv["weighted_confidence"] * 100)
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    integrator = next((v for v in verdicts if v.get("role") == "integrator"), None)
    recommendation = (
        integrator.get("reasoning", "") if integrator
        else f"Weighted verdict: **{wv['label']}** at {conf_pct}% confidence."
    )
    sections = (
        [f"# UltraJudge: {question}", "", f"**Generated**: {ts}", "",
         f"## Weighted verdict: {wv['label'].upper()} ({conf_pct}% weighted confidence)", ""]
        + _render_stub_banner(verdicts)
        + _render_unserved_banner(verdicts)
        + _render_table(verdicts) + [""]
        + _render_dissents(verdicts, wv["label"])
        + _render_seat_reasoning(verdicts)
        + ["## Recommendation", "", recommendation, ""]
    )
    return "\n".join(sections)


def _dispatch_council(question: str, pairs: list[tuple[str, str]],
                      adapter_factory: Optional[Callable]) -> list[dict]:
    """Dispatch every (role, model) seat; refuse if the live path yielded
    nothing but stubs (HAL wholly unreachable — §1 requirement 5)."""
    dispatch = _select_dispatch()
    if dispatch is not _dispatch_real:
        return [dispatch(model, role, question) for role, model in pairs]
    verdicts = [
        _dispatch_real(model, role, question, adapter_factory=adapter_factory)
        for role, model in pairs
    ]
    if verdicts and all(v.get("source") == "stub" for v in verdicts):
        raise HALUnavailableError(
            "UltraJudge: HAL wholly unreachable — every council seat "
            "degraded to the stub. Refusing to emit a canned verdict. "
            "(Set ULTRAJUDGE_USE_HAL=0 for an explicit, labelled stub run.)"
        )
    return verdicts


def run(
    question: str,
    roles: Optional[list[str]] = None,
    models: Optional[list[str]] = None,
    weights: Optional[dict] = None,
    adapter_factory: Optional[Callable] = None,
) -> Path:
    """Orchestrate: assign roles, dispatch, synthesise, write deliverable.

    Live HAL dispatch by default (``ULTRAJUDGE_USE_HAL`` unset or ``1``);
    ``0`` selects the labelled stub. On the live path, when EVERY seat
    degraded to the stub this raises :class:`HALUnavailableError` and writes
    nothing — a canned deliverable is worse than no deliverable.

    adapter_factory threads through to :func:`_dispatch_real` (test seam).
    """
    roles = roles or DEFAULT_ROLES
    models = [canonical_label(m) for m in (models or DEFAULT_MODELS)]
    pairs = [(roles[i % len(roles)], models[i % len(models)]) for i in range(len(roles))]
    verdicts = _dispatch_council(question, pairs, adapter_factory)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    md = synthesise(question, verdicts, weights)
    slug = _make_slug(question)
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return _write_new_report(f"{slug}-{ts}", md)


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-model verdict council")
    parser.add_argument("question", help="Question or proposal to judge")
    parser.add_argument("--roles", help="Comma-separated role list")
    parser.add_argument("--models", help="Comma-separated model list")
    parser.add_argument("--weights", help='JSON weight map e.g. \'{"devil": 2.0}\'')
    args = parser.parse_args()

    roles = [r.strip() for r in args.roles.split(",")] if args.roles else None
    models = [m.strip() for m in args.models.split(",")] if args.models else None
    weights = json.loads(args.weights) if args.weights else None
    try:
        out = run(args.question, roles, models, weights)
    except HALUnavailableError as exc:
        print(f"[ultrajudge] REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(f"UltraJudge deliverable: {out}")


if __name__ == "__main__":
    # The astra seat reaches lib/sovereign_council, which can write signed
    # records, so a system interpreter re-executes into one that can dual-sign
    # first (rules/never-system-python-for-grip.md), in the same form
    # lib/cc_feature_scanner.py uses.
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(Path(__file__))
    main()
