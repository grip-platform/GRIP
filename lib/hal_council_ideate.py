"""Generic multi-LLM ideation/deliberation council via the HAL Gateway.

The reusable bridge that makes a dynamic workflow REAL multi-LLM instead of
Anthropic-only. The Claude Code ``Workflow`` tool's ``agent()`` spawns harness
sub-agents that are ALL Anthropic — so a "Multi-LLM council" built on agent()
is multi-PERSPECTIVE, single-PROVIDER. This runner dispatches one open-ended
question to N DISTINCT provider families over HAL (gemini / groq / deepseek /
xai / qwen / ...), the same way AGORA and the HAL Council already do for PR
review.

It REUSES the proven HAL HAPPI subprocess seam from ``hal_council_dispatch``
(DRY + syscall-doctrine: one HAL syscall shim, never a second hand-rolled
``hal --happi-api`` call), and swaps the PR-review prompt+parser for an
open-ended question -> N free-form answers (+ optional synthesis).

Goal #2205 (V>> 2026-06-22, /goal /auto /rsi): "why are we only using Anthropic
models?". A workflow that needs real multi-LLM shells out to ``bin/grip-council``
(or imports ``ideate`` here) instead of fanning out Claude agent()s.

Falsifiable real-multi-LLM check (``is_real_multi_llm``): the non-error answers
MUST span >=2 DISTINCT non-Anthropic provider families, else it is not
multi-LLM. A council whose only surviving voice is Anthropic fails this gate.

CLI:
    grip-council "<question>" [--context "..."] [--roster council|agora|open]
                 [--synthesize] [--timeout N] [--out PATH]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Reuse the proven HAL HAPPI seam — do NOT hand-roll a second `hal --happi-api`
# call (syscall-doctrine: one shim per boundary).
from hal_council_dispatch import (  # noqa: E402
    _AGORA_SPEAKER_ROSTER,
    _DEFAULT_TIMEOUT_S,
    _HAL_BIN_DEFAULT,
    _PROVIDER_ROSTER,
    _build_envelope,
    _parse_happi_events,
    _run_hal_subprocess,
    _subprocess_failure_reason,
)
from exogenous_council import EXOGENOUS_ROSTER  # noqa: E402

_HAPPI_RUNTIME_VERSION = "happi/1.3"

_IDEATION_SYSTEM = (
    "You are ONE independent voice on a multi-LLM design council. Give your "
    "best, concrete, decisive answer to the question. Prefer novel-but-grounded "
    "ideas over safe platitudes; name specific mechanisms, commands, ports, or "
    "trade-offs. Do not hedge or ask for clarification. <=450 words."
)

_SYNTH_SYSTEM = (
    "You are the integrator of a multi-LLM council. You are given the same "
    "question and several independent answers from DIFFERENT model families. "
    "Produce one unified synthesis: the strongest shared recommendation, the "
    "best unique idea from each voice worth keeping, and any genuine "
    "disagreement called out explicitly. Be concrete and decisive. <=700 words."
)


def _family(happi_provider: str) -> str:
    """Provider family from a HAPPI provider string.

    'gemini/gemini-2.5-flash' -> 'gemini'; 'claude-opus-4-7' -> 'anthropic'.
    """
    if "/" in happi_provider:
        return happi_provider.split("/", 1)[0]
    return "anthropic" if "claude" in happi_provider else happi_provider


# Named rosters. "open" excludes Anthropic entirely — use it when an Anthropic
# council is already running (it dodges the throttled lane) AND it is the
# cleanest proof of real multi-LLM (every voice is a distinct non-Anthropic
# family).
ROSTERS: dict[str, list] = {
    "council": _PROVIDER_ROSTER,
    "agora": _AGORA_SPEAKER_ROSTER,
    "open": [e for e in _PROVIDER_ROSTER if "claude" not in e[1]],
    "exogenous": EXOGENOUS_ROSTER,
}


@dataclass
class Voice:
    """One provider's answer (or its error)."""
    provider_id: str
    family: str
    description: str
    answer: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.answer.strip())


@dataclass
class CouncilOutcome:
    question: str
    voices: list[Voice] = field(default_factory=list)
    synthesis: Optional[str] = None
    synthesis_provider: Optional[str] = None

    @property
    def distinct_nonanthropic_families(self) -> set[str]:
        return {v.family for v in self.voices if v.ok and v.family != "anthropic"}

    @property
    def is_real_multi_llm(self) -> bool:
        """The falsifiable gate: >=2 distinct non-Anthropic families answered."""
        return len(self.distinct_nonanthropic_families) >= 2


def _ask_one(entry: tuple, question: str, context: str, hal_bin: str, timeout: int) -> Voice:
    """Dispatch the question to a single provider via the HAL HAPPI seam."""
    provider_id, happi_provider, auth, description = entry
    prompt = f"CONTEXT:\n{context}\n\nQUESTION:\n{question}" if context else question
    envelope = _build_envelope(
        provider_id, happi_provider, auth, prompt, system_override=_IDEATION_SYSTEM
    )
    proc = _run_hal_subprocess(json.dumps(envelope), hal_bin, timeout)
    if proc is None:
        return Voice(provider_id, _family(happi_provider), description,
                     error=_subprocess_failure_reason(hal_bin))
    parts, err = _parse_happi_events(proc.stdout)
    text = "".join(parts).strip()
    if text:
        return Voice(provider_id, _family(happi_provider), description, answer=text)
    return Voice(provider_id, _family(happi_provider), description,
                 error=err or _empty_reason(proc.stdout))


def _event_or_empty(line: str) -> dict:
    """One HAPPI event as a dict; a malformed or non-object line becomes {}."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return event if isinstance(event, dict) else {}


def _empty_reason(stdout: str) -> str:
    """Say why a voice came back with no answer text, as far as the stream shows.

    A bare "empty_response" hid the cause in three council sittings on 2026-10-11
    and 2026-10-12, while the same seat answered short and long test questions in
    full. The runtime's ``completed`` event carries ``stop_reason``, and a
    reasoning model can send ``thinking`` events with no answer at all, so the
    error names both when the stream has them.
    """
    events = [e for e in map(_event_or_empty, stdout.splitlines()) if e]
    stops = [str(e["stop_reason"]) for e in events
             if e.get("type") == "completed" and e.get("stop_reason")]
    thought = sum(len(e.get("text") or "") for e in events if e.get("type") == "thinking")
    details = [f"stop_reason={stops[-1]}"] if stops else []
    details += [f"thinking only, {thought} chars"] if thought else []
    return f"empty_response ({'; '.join(details)})" if details else "empty_response"


def ideate(
    question: str,
    context: str = "",
    roster: Optional[list] = None,
    hal_bin: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
    max_workers: int = 6,
) -> list[Voice]:
    """Dispatch ``question`` to every provider in ``roster`` IN PARALLEL.

    Returns one Voice per roster entry (errors captured, never raised), so a
    single dead provider never sinks the council.
    """
    roster = roster if roster is not None else _PROVIDER_ROSTER
    hal_bin = hal_bin or _HAL_BIN_DEFAULT
    voices: list[Voice] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_ask_one, e, question, context, hal_bin, timeout) for e in roster]
        for fut in concurrent.futures.as_completed(futures):
            voices.append(fut.result())
    return voices


def synthesize(
    question: str,
    voices: list[Voice],
    synth_entry: Optional[tuple] = None,
    hal_bin: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT_S,
) -> tuple[Optional[str], Optional[str]]:
    """Integrate the council's answers via one more HAL dispatch.

    Defaults to a Gemini synthesizer (a non-Anthropic family, exogenous to most
    speakers). Returns (synthesis_text, synthesizer_provider_id).
    """
    ok_voices = [v for v in voices if v.ok]
    if not ok_voices:
        return None, None
    if synth_entry is None:
        gemini = [e for e in _PROVIDER_ROSTER if _family(e[1]) == "gemini"]
        synth_entry = gemini[0] if gemini else ok_voices and _PROVIDER_ROSTER[0]
    provider_id, happi_provider, auth, _desc = synth_entry
    blocks = "\n\n".join(f"### Voice: {v.description} ({v.family})\n{v.answer}" for v in ok_voices)
    prompt = f"QUESTION:\n{question}\n\nINDEPENDENT ANSWERS:\n{blocks}"
    envelope = _build_envelope(provider_id, happi_provider, auth, prompt, system_override=_SYNTH_SYSTEM)
    proc = _run_hal_subprocess(json.dumps(envelope), hal_bin or _HAL_BIN_DEFAULT, timeout)
    if proc is None:
        return None, provider_id
    parts, _err = _parse_happi_events(proc.stdout)
    text = "".join(parts).strip()
    return (text or None), provider_id


def run_council(
    question: str,
    context: str = "",
    roster_name: str = "council",
    do_synthesize: bool = False,
    timeout: int = _DEFAULT_TIMEOUT_S,
) -> CouncilOutcome:
    """End-to-end: diverge (N providers) then optionally converge (synthesis)."""
    roster = ROSTERS.get(roster_name, _PROVIDER_ROSTER)
    voices = ideate(question, context=context, roster=roster, timeout=timeout)
    outcome = CouncilOutcome(question=question, voices=voices)
    if do_synthesize:
        outcome.synthesis, outcome.synthesis_provider = synthesize(
            question, voices, timeout=timeout
        )
    return outcome


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(obj) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _happi_idr_ref(envelope: dict, events: list[dict], model_versions: list[str]) -> dict:
    return {
        "sha256": _sha256({"envelope": envelope, "events": events}),
        "cid": None,
        "model_versions": model_versions,
        "block_anchor": None,
    }


def _context_ref(decision_body: dict, model_versions: list[str]) -> dict:
    return {
        "sha256": _sha256(decision_body),
        "predecessor_context": None,
        "snapshot_ref": None,
        "kind": "context-delta",
        "model_versions": model_versions,
    }


def _cite_verify_record(outcome: CouncilOutcome) -> dict:
    """HAPPI/1.3 cite.verify-ready provenance for the saved transcript.

    The council transcript is the source; citations are exact quote checks for
    each successful voice and synthesis. This does not prove the models are true;
    it proves the saved decision quotes are verbatim present in the transcript.
    """
    source_text = render(outcome)
    citations = _transcript_citations(outcome)
    records = [_located(citation, source_text) for citation in citations]
    return {
        "cmd": "cite.verify",
        "v": _HAPPI_RUNTIME_VERSION,
        "flags": {
            "sources": [{"id": "transcript", "text": source_text}],
            "citations": citations,
            "strict": True,
        },
        "usage": _cite_usage(source_text, records),
    }


def _transcript_citations(outcome: CouncilOutcome) -> list[dict]:
    """One exact-quote citation per answering voice, then one for the synthesis."""
    quotes = [(f"voice:{v.provider_id}", v.answer) for v in outcome.voices if v.ok]
    if outcome.synthesis:
        quotes.append(("synthesis", outcome.synthesis))
    return [{"id": cid, "source_id": "transcript", "quote": text[:240]} for cid, text in quotes]


def _located(citation: dict, source_text: str) -> dict:
    """The citation with its verbatim status and its offsets in the transcript."""
    quote = citation["quote"]
    start = source_text.find(quote)
    return {
        **citation,
        "status": "verified" if start >= 0 else "not_found",
        "start": start,
        "end": start + len(quote) if start >= 0 else -1,
    }


def _cite_usage(source_text: str, records: list[dict]) -> dict:
    """The cite.verify usage block: the transcript's hash, each record, the tally."""
    not_found = sum(1 for r in records if r["status"] == "not_found")
    verified = len(records) - not_found
    return {
        "sources": {
            "transcript": {
                "sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                "chars": len(source_text),
            }
        },
        "citations": records,
        "tally": {"verified": verified, "fuzzy": 0, "not_found": not_found},
        "grounding_rate": (verified / len(records)) if records else 1.0,
    }


def build_happi_audit_bundle(outcome: CouncilOutcome, *, roster_name: str) -> dict:
    model_versions = [
        f"{v.family}/{v.provider_id}" for v in outcome.voices if v.ok
    ]
    decision_body = {
        "question": outcome.question,
        "roster": roster_name,
        "is_real_multi_llm": outcome.is_real_multi_llm,
        "distinct_nonanthropic_families": sorted(outcome.distinct_nonanthropic_families),
        "voices": [
            {"provider_id": v.provider_id, "family": v.family, "ok": v.ok,
             "answer": v.answer, "error": v.error} for v in outcome.voices
        ],
        "synthesis": outcome.synthesis,
        "synthesis_provider": outcome.synthesis_provider,
    }
    envelope = {
        "v": _HAPPI_RUNTIME_VERSION,
        "id": "grip-council-audit",
        "cmd": "council.decide",
        "flags": {"roster": roster_name, "audit": True},
        "decision_body": decision_body,
    }
    events = [
        {"v": _HAPPI_RUNTIME_VERSION, "id": envelope["id"], "type": "started", "ts": 0},
        {"v": _HAPPI_RUNTIME_VERSION, "id": envelope["id"], "type": "completed", "ts": 1,
         "usage": {"voices": len(outcome.voices), "ok": sum(1 for v in outcome.voices if v.ok)}},
    ]
    idr_ref = _happi_idr_ref(envelope, events, model_versions)
    context_ref = _context_ref(decision_body, model_versions)
    cite = _cite_verify_record(outcome)
    return {
        "v": _HAPPI_RUNTIME_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "envelope": envelope,
        "events": events + [
            {"v": _HAPPI_RUNTIME_VERSION, "id": envelope["id"], "type": "idr", "ts": 2,
             "idr_ref": idr_ref},
            {"v": _HAPPI_RUNTIME_VERSION, "id": envelope["id"], "type": "context", "ts": 3,
             "context_ref": context_ref},
        ],
        "decision_body": decision_body,
        "idr_ref": idr_ref,
        "context_ref": context_ref,
        "cite_verify": cite,
    }


def render(outcome: CouncilOutcome) -> str:
    """Plain-text report — provider families are explicit (the multi-LLM proof)."""
    lines = [f"# Multi-LLM Council — {len(outcome.voices)} voices", f"Q: {outcome.question}", ""]
    fams = sorted(outcome.distinct_nonanthropic_families)
    gate = "REAL MULTI-LLM ✓" if outcome.is_real_multi_llm else "NOT multi-LLM ✗"
    lines.append(f"[{gate}] distinct non-Anthropic families: {', '.join(fams) or '(none)'}")
    lines.append("")
    for v in outcome.voices:
        head = f"## {v.description}  [{v.family}/{v.provider_id}]"
        lines.append(head)
        lines.append(v.answer if v.ok else f"(no answer — {v.error})")
        lines.append("")
    if outcome.synthesis:
        lines.append(f"## SYNTHESIS  [{_family_of_id(outcome.synthesis_provider)}]")
        lines.append(outcome.synthesis)
    return "\n".join(lines)


def _family_of_id(provider_id: Optional[str]) -> str:
    if not provider_id:
        return "?"
    for entry in _PROVIDER_ROSTER:
        if entry[0] == provider_id:
            return _family(entry[1])
    return provider_id


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="grip-council", description="Real multi-LLM council via HAL Gateway")
    parser.add_argument("question", nargs="?", help="the council question (or use --stdin)")
    parser.add_argument("--stdin", action="store_true", help="read the question from stdin")
    parser.add_argument("--context", default="", help="grounding context prepended to the question")
    parser.add_argument("--roster", default="council", choices=sorted(ROSTERS.keys()))
    parser.add_argument("--synthesize", action="store_true", help="add an integrator synthesis pass")
    parser.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT_S)
    parser.add_argument("--out", help="also write the report to this path")
    parser.add_argument("--happi-audit-out", help="write HAPPI/1.3 IDR/context/cite.verify audit JSON")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text report")
    args = parser.parse_args(argv)

    question = sys.stdin.read().strip() if args.stdin else (args.question or "")
    if not question:
        parser.error("a question is required (positional or --stdin)")

    outcome = run_council(
        question, context=args.context, roster_name=args.roster,
        do_synthesize=args.synthesize, timeout=args.timeout,
    )

    if args.json:
        payload = {
            "question": outcome.question,
            "is_real_multi_llm": outcome.is_real_multi_llm,
            "distinct_nonanthropic_families": sorted(outcome.distinct_nonanthropic_families),
            "voices": [
                {"provider_id": v.provider_id, "family": v.family, "ok": v.ok,
                 "answer": v.answer, "error": v.error} for v in outcome.voices
            ],
            "synthesis": outcome.synthesis,
            "synthesis_provider": outcome.synthesis_provider,
        }
        out = json.dumps(payload, indent=2)
    else:
        out = render(outcome)

    print(out)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    if args.happi_audit_out:
        audit = build_happi_audit_bundle(outcome, roster_name=args.roster)
        Path(args.happi_audit_out).write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return 0 if outcome.is_real_multi_llm else 3


if __name__ == "__main__":
    raise SystemExit(main())
