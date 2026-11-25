#!/usr/bin/env python3
"""lib/ultrathink_trio.py -- /ultrathink's three-seat deliberation.

V>> 2026-10-03: "Amend /ultrathink to always use Fable 5.1 and GPT-6 Astra and
DeepSeek v4 Pro in parallel and reasoning together, and debating and
falsifying ... Remember RAILLM ... In this case the same applied. Going from
SOTA frontier models -> replacing one, or go one level lower -> etc."

WHAT "ALWAYS" MEANS HERE (an interpretation, stated so V>> can overrule it):
whenever /ultrathink DELIBERATES -- an explicit ``/ultrathink <question>``, or a
deliberate library call from another skill. It does not mean a three-model
council on every turn: the always-active half of /ultrathink, the six HALT
guards, is unchanged. A council on every prompt would cost more attention,
latency and quota than it returns.

THE SHAPE
  Round 1   the seats answer the question in parallel.
  Round 2   each seat receives the other positions and, for each, names the
            claim, the evidence offered, the observation that would refute it,
            and a verdict: REFUTED, WEAKENED or STANDS.
  Synthesis is NOT a fourth model call. The result carries ``synthesis_prompt``
            for the MAIN THREAD. When the session runs on Fable the Fable
            seat IS the main thread, and a second ``claude -p`` call would
            spend the weekly limit twice, so that seat makes no call at all.

THE LADDER (RAILLM per seat, ``~/.hal/docs/RAILLM.md``). Each seat climbs its
own ordered chain: a failed rung is recorded and the next one tried, a rung
whose precondition fails is recorded as skipped, and an exhausted seat is
recorded as degraded. Fallbacks run in synchronous waves and are assigned in
seat order, so which rung a seat ends on never depends on thread timing. A rung
that bills the OpenAI API key runs only on ``GRIP_CODEX_ALLOW_PAID=1``, the same
opt-in as bin/grip-codex-ladder's rung 2; without it the rung is skipped before
it can hold a vendor (rules/gpt-on-the-subscription-never-credits.md).

THREE GUARDS, composed from lib/roundtable.py rather than rewritten:
  F-1  the seats that answer must be distinct vendors. A fallback that would
       seat a second model from one vendor is refused and the ladder moves on;
       ``assert_disjoint`` re-checks every answering set. Vendors are read off
       the MODEL's maker with the provider prefix stripped: Groq's gpt-oss-120b
       is OpenAI-trained, so it clashes with an answering Astra seat.
  F-2  the record is sealed by a sha256 over canonical JSON (``plan_sha256``),
       so the same inputs re-seal to the same digest and an edit is visible.
  F-3  fewer than two seats answering is a refusal (:class:`UltrathinkUnavailable`),
       never a fabricated council. One seat is returned only on request, and
       labelled "single seat, not a council".

The record (prose included) goes to ``$CLAUDE_DIR/state/ultrathink/<sha>.json``;
the shared IDR chain receives ONLY the sha256 and seat/rung metadata.

Transports are composed, never new: the Fable and Opus rungs and the Codex
Astra rung ride lib/sovereign_council.py's proven subprocess seats; the HAL
rungs ride lib/hal_llm_adapter._query_grip_infer (a pin implies no silent
failover); grok-4.6 rides lib/roundtable._chat_call; the registry fallbacks
ride lib/sovereign_council.provider_call.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.roundtable import (  # noqa: E402
    VENDOR,
    RoundtableError,
    RoundtableUnavailableError,
    assert_disjoint,
    plan_sha256 as canonical_sha256,
    vendor_of,
)

logger = logging.getLogger(__name__)

RECORD_FORMAT = "ultrathink-trio/1"
SINGLE_SEAT_LABEL = "single seat, not a council"
COUNCIL_LABEL = "council"


# --- the ladder ---------------------------------------------------------------


def _bare(model: str) -> str:
    """A model id without its provider prefix (``openai/gpt-6-astra`` -> ``gpt-6-astra``)."""
    return model.rsplit("/", 1)[-1]


def model_vendor(model: str) -> str:
    """The maker of a model, provider prefix stripped first.

    The strip matters: ``vendor_of("openai/gpt-6-astra")`` resolves the whole
    string to itself, which would pass as disjoint from every other seat.
    """
    return vendor_of(_bare(model))


#: The opt-in a paid rung needs: the variable bin/grip-codex-ladder already reads
#: for its OpenAI-API rung, so one switch governs both
#: (rules/gpt-on-the-subscription-never-credits.md).
PAID_OPT_IN = "GRIP_CODEX_ALLOW_PAID"


@dataclass(frozen=True)
class Rung:
    """One step of a seat's fallback ladder."""

    label: str              # stable id, shown in the footer and the sealed record
    model: str              # the model id the transport is asked for
    transport: str          # a key of LIVE_TRANSPORTS
    target: str = ""        # endpoint / registry name, for the chat transports
    requires_key: str = ""  # provider whose API key must resolve before a call
    opt_in: str = ""        # variable that must be "1" before this rung may run

    @property
    def vendor(self) -> str:
        return model_vendor(self.model)


DEFAULT_SEATS = ("fable", "astra", "deepseek")

#: The per-seat RAILLM ladders. The Fable seat stops at Opus 5.5: there is no
#: third Anthropic rung, and an Anthropic seat never falls to another vendor
#: silently -- it is recorded as degraded instead.
LADDER: dict[str, tuple[Rung, ...]] = {
    # Fable 5.2 first (2026-11-11): the vendor's current Fable, cheaper than 5.1 on
    # every column ($8 / $40 against $10 / $50), served on this account through
    # Claude Code 2.1.319. Fable 5.1 stays as the second rung.
    "fable": (
        Rung("fable-5.2", "claude-fable-5-2", "claude-p"),
        Rung("fable-5.1", "claude-fable-5-1", "claude-p"),
        Rung("opus-5.5", "claude-opus-5-5", "claude-p"),
    ),
    "astra": (
        Rung("astra-codex", "gpt-6-astra", "codex"),
        Rung("astra-hal", "openai/gpt-6-astra", "hal", requires_key="openai", opt_in=PAID_OPT_IN),
        Rung("grok-4.6", "grok-4.6", "chat", target="xai"),
        Rung("gemini-pro", "gemini-3.1-pro-preview", "council", target="gemini-pro"),
    ),
    "deepseek": (
        Rung("deepseek-v4-pro", "deepseek/deepseek-v4-pro", "hal"),
        Rung("deepseek-flash", "deepseek/deepseek-flash", "hal"),
        Rung("groq-gpt-oss-120b", "openai/gpt-oss-120b", "council", target="groq"),
        Rung("gemini-flash", "gemini-3.5-flash-lite", "council", target="gemini"),
    ),
}

def session_rung() -> Rung:
    """The fable seat when the main thread holds it, named for the session's model.

    ``$GRIP_SESSION_MODEL`` says which Fable the session runs, so a Fable 5.2
    session is never recorded as Fable 5.1. The ``[1m]`` window suffix is Claude
    Code's spelling, not the model's, and is dropped. When no Fable is named
    there (an explicit ``session_fable=True``), the fable ladder's first rung
    stands in.
    """
    named = _bare(os.environ.get("GRIP_SESSION_MODEL", "")).split("[", 1)[0].strip()
    model = named if "fable" in named.lower() else LADDER["fable"][0].model
    return Rung("session", model, "session")


# --- errors --------------------------------------------------------------------


class SeatCallError(RuntimeError):
    """A rung did not answer. Raised by a transport, recorded by the ladder."""


class RungSkipped(SeatCallError):
    """A rung was not tried because its precondition did not hold (no API key)."""


class UltrathinkUnavailable(RoundtableUnavailableError):
    """Fewer than two seats answered: refuse rather than fabricate (roundtable F-3).

    Every attempt is carried in ``attempts`` (``advisors`` is the parent's name
    for the same list) and named in the message, so the cause survives.
    """

    def __init__(self, message: str, attempts: list[dict]) -> None:
        super().__init__(message, advisors=attempts)
        self.attempts = list(attempts)


# --- live transports (composed; tests inject their own) ------------------------

#: Read bound per transport, seconds. Astra at max effort took 3m10s for one
#: answer on 2026-10-03, and a debate prompt is longer, so Codex gets headroom.
#: The Claude seat gets more: on 2026-11-11 a text-only Fable 5.2 call on a
#: /connect design question took 663 seconds at the effort settings.json sets
#: (CLAUDE_CODE_EFFORT_LEVEL=max), and at 300 seconds all three Anthropic rungs
#: (Fable 5.2, Fable 5.1, Opus 5.5) timed out on the same question twice.
_TIMEOUT_S = {"claude-p": 900, "codex": 600, "hal": 240, "council": 180}

#: Room to reason. A cap, not a spend: sovereign_council's judge uses the same.
_COUNCIL_MAX_TOKENS = 8192

#: sovereign_council.provider_call reports a 200 with no content as ok=True
#: carrying this placeholder text. It is not an answer.
_EMPTY_200 = "(200 but empty"


def _spawn(spec: dict, prompt: str, timeout: int) -> tuple[str, str]:
    """One CLI seat through sovereign_council's subprocess transport (prompt on stdin)."""
    from lib import sovereign_council as sc

    argv, model = sc._spawn_argv_and_model(spec)
    rc, answer, err = sc._spawn_answer(spec, argv, prompt, timeout)
    if rc != 0:
        # claude -p prints its own failure (a usage cap, an API error) on stdout,
        # so stderr alone recorded six Fable seat failures of 2026-11-24 as
        # "(no stderr)" (GRIP#9272). When stderr is empty, the end of stdout
        # carries the cause.
        detail = (err or answer[-300:])[:300]
        raise SeatCallError(f"exit {rc}: {detail or '(no output)'}")
    return answer, model


def _claude_p(rung: Rung, prompt: str) -> tuple[str, str]:
    """An Anthropic seat via ``claude -p``: the council's fable seat, model swapped.

    Keeps that spec's empty tool allowlist and its stripped API-key variables,
    so the call bills the subscription login, never a dead API key.
    """
    from lib import sovereign_council as sc

    base = sc.SUBPROCESS_SPEAKERS["fable"]
    argv = list(base["argv"])
    argv[argv.index("--model") + 1] = rung.model
    return _spawn({**base, "argv": argv, "model": rung.model}, prompt, _TIMEOUT_S["claude-p"])


def _codex(rung: Rung, prompt: str) -> tuple[str, str]:
    """Astra on the Codex subscription: the council's proven codex seat."""
    from lib import sovereign_council as sc

    spec = {**sc.SUBPROCESS_SPEAKERS["codex"], "model": rung.model}
    return _spawn(spec, prompt, _TIMEOUT_S["codex"])


def _key_resolves(provider: str) -> bool:
    """Whether the provider's API key resolves (env -> Keychain -> /proc)."""
    from lib.sovereign_council import provider_key

    try:
        provider_key(provider)
    except Exception:  # noqa: BLE001 -- any failure to resolve means "no key"
        return False
    return True


def _hal(rung: Rung, prompt: str) -> tuple[str, str]:
    """A pinned model through HAL. The pin implies no failover, so HAL cannot
    answer with another model under this rung's name."""
    if rung.requires_key and not _key_resolves(rung.requires_key):
        raise RungSkipped(f"no {rung.requires_key} API key resolves")
    from lib.hal_llm_adapter import _query_grip_infer

    result = _query_grip_infer(prompt, model=rung.model, timeout_s=_TIMEOUT_S["hal"])
    if not result:
        raise SeatCallError(
            f"HAL gave no answer for {rung.model} (pin refused, provider error, or HAL unreachable)")
    return result["text"], str(result.get("model") or rung.model)


def _chat(rung: Rung, prompt: str) -> tuple[str, str]:
    """grok-4.6 through the roundtable's chat transport."""
    from lib import roundtable

    res = roundtable._chat_call(rung.target, [{"role": "user", "content": prompt}])
    if not res.get("ok"):
        raise SeatCallError(str(res.get("text") or "no output")[:300])
    return res["text"], str(res.get("model") or rung.model)


def _council(rung: Rung, prompt: str) -> tuple[str, str]:
    """A registry seat through sovereign_council.provider_call.

    The read timeout is the trio's own 180 s floor or the seat's per-seat
    allowance (GRIP_COUNCIL_READ_TIMEOUT_S_<SEAT>, GRIP#8521), whichever is
    longer. Until 2026-10-18 the floor went down as an explicit value, and
    provider_call keeps an explicit value over the override (``timeout or
    read_timeout_for(name)``), so the allowance that lets xai finish a
    107K-character prompt could never reach a council rung on this path. Found
    by the falsify pass over the product fleet's hardening lane.
    """
    from lib import sovereign_council as sc

    timeout = max(_TIMEOUT_S["council"], sc.read_timeout_for(rung.target))
    res = sc.provider_call(rung.target, [{"role": "user", "content": prompt}], time.time(),
                           max_tokens=_COUNCIL_MAX_TOKENS, timeout=timeout)
    text = str(res.get("text") or "")
    if not res.get("ok") or text.startswith(_EMPTY_200):
        raise SeatCallError(text[:300] or "no output")
    if res.get("truncated"):
        raise SeatCallError(
            f"answer cut off at its token budget (finish_reason={res.get('finish_reason')})")
    return text, str(res.get("model") or rung.model)


LIVE_TRANSPORTS: dict[str, Callable] = {
    "claude-p": _claude_p,
    "codex": _codex,
    "hal": _hal,
    "chat": _chat,
    "council": _council,
}


def _resolve_transports(transports) -> dict:
    """The live map, or the caller's. An injected map REPLACES the live one, so a
    kind it leaves out has no transport (its rungs fail) rather than reaching
    the network. A single callable serves every kind."""
    if transports is None:
        return dict(LIVE_TRANSPORTS)
    if callable(transports):
        return {kind: transports for kind in LIVE_TRANSPORTS}
    return dict(transports)


# --- one rung, one call ----------------------------------------------------------


@dataclass
class _Outcome:
    kind: str           # answered | failed | skipped
    text: str = ""
    served: str = ""
    reason: str = ""
    seconds: float = 0.0


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _known_vendor(model: str) -> Optional[str]:
    """The vendor of a served model id, or None when the classifier does not know it."""
    bare = _bare(model)
    vendor = vendor_of(bare)
    return None if vendor == bare and bare not in VENDOR else vendor


def _judge_answer(rung: Rung, out, seconds: float) -> _Outcome:
    """Accept an answer only if it has text and a KNOWN different vendor did not serve it."""
    text, served = (out, rung.model) if isinstance(out, str) else (out[0], out[1] or rung.model)
    text = str(text or "").strip()
    if not text:
        return _Outcome("failed", reason="empty answer", seconds=seconds)
    served_vendor = _known_vendor(str(served))
    if served_vendor and served_vendor != rung.vendor:
        return _Outcome("failed", seconds=seconds,
                        reason=f"served by {served} ({served_vendor}), not {rung.vendor}")
    return _Outcome("answered", text=text, served=str(served), seconds=seconds)


def _call_rung(transports: Mapping, rung: Rung, prompt: str) -> _Outcome:
    """Call one rung. Never raises: a dead rung is a recorded failure."""
    fn = transports.get(rung.transport)
    if fn is None:
        return _Outcome("failed", reason=f"no transport for {rung.transport!r}")
    t0 = time.monotonic()
    try:
        out = fn(rung, prompt)
    except RungSkipped as exc:
        return _Outcome("skipped", reason=str(exc), seconds=time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001 -- a council survives a dead rung
        return _Outcome("failed", reason=_reason(exc), seconds=time.monotonic() - t0)
    return _judge_answer(rung, out, time.monotonic() - t0)


# --- the ladder engine ----------------------------------------------------------

_QUOTA_RE = re.compile(r"\b429\b|rate.?limit|usage limit|out of credits|insufficient_quota", re.I)
_QUOTA_NOTE = "quota or window full: recorded and fallen back, never topped up"


class _Log:
    """The run's attempts (sealed) and seconds per seat (wall clock, not sealed)."""

    def __init__(self) -> None:
        self.attempts: list[dict] = []
        self.seconds: dict[str, float] = {}

    def add(self, seat: str, rnd: int, rung: Rung, outcome: str,
            reason: str = "", seconds: float = 0.0) -> None:
        if outcome == "failed" and _QUOTA_RE.search(reason):
            reason = f"{reason} [{_QUOTA_NOTE}]"
        self.attempts.append({"seat": seat, "round": rnd, "rung": rung.label,
                              "model": rung.model, "vendor": rung.vendor,
                              "outcome": outcome, "reason": reason})
        self.seconds[seat] = round(self.seconds.get(seat, 0.0) + seconds, 3)


@dataclass
class _Round:
    """What one round needs: its number, ladders, prompts, transports and rules."""

    number: int
    ladders: dict
    prompt_of: Callable[[str], str]
    transports: Mapping
    held: set
    allow_same_vendor: bool
    log: _Log


def _opted_in(rung: Rung) -> bool:
    """Whether a rung may run: always without an opt-in, else only when it is "1"."""
    return not rung.opt_in or os.environ.get(rung.opt_in) == "1"


def _first_free_rung(rnd: _Round, seat: str, start: int, held: set) -> Optional[int]:
    """The first rung at or after ``start`` that may run and whose vendor is not
    already seated. A rung lacking its opt-in is skipped here, before it can hold
    a vendor that another seat's fallback might need in the same wave."""
    ladder = rnd.ladders[seat]
    for idx in range(start, len(ladder)):
        rung = ladder[idx]
        if not _opted_in(rung):
            rnd.log.add(seat, rnd.number, rung, "skipped", f"paid rung: runs only with {rung.opt_in}=1")
            continue
        if rnd.allow_same_vendor or rung.vendor not in held:
            return idx
        rnd.log.add(seat, rnd.number, rung, "refused", f"vendor clash: {rung.vendor} already seated")
    return None


def _assign_wave(rnd: _Round, pending: list, nxt: dict, answered: dict) -> dict:
    """Give each pending seat its next vendor-free rung, in seat order."""
    held = set(rnd.held) | {rung.vendor for _idx, rung, _out in answered.values()}
    wave: dict = {}
    for seat in pending:
        idx = _first_free_rung(rnd, seat, nxt[seat], held)
        if idx is None:
            continue  # ladder exhausted: the seat drops out, degraded
        nxt[seat] = wave[seat] = idx
        held.add(rnd.ladders[seat][idx].vendor)
    return wave


def _run_wave(rnd: _Round, wave: dict) -> dict:
    """Call every assigned rung in parallel."""
    if not wave:
        return {}
    with ThreadPoolExecutor(max_workers=len(wave)) as pool:
        futures = {seat: pool.submit(_call_rung, rnd.transports, rnd.ladders[seat][idx],
                                     rnd.prompt_of(seat))
                   for seat, idx in wave.items()}
        return {seat: fut.result() for seat, fut in futures.items()}


def _settle(rnd: _Round, wave: dict, outcomes: dict, answered: dict, nxt: dict) -> list:
    """Record the wave in seat order; return the seats that must try their next rung."""
    retry = []
    for seat, idx in wave.items():
        rung, out = rnd.ladders[seat][idx], outcomes[seat]
        rnd.log.add(seat, rnd.number, rung, out.kind, out.reason, out.seconds)
        if out.kind == "answered":
            answered[seat] = (idx, rung, out)
        else:
            nxt[seat] = idx + 1
            retry.append(seat)
    return retry


def _run_round(rnd: _Round, start: dict) -> dict:
    """Climb each seat's ladder from ``start[seat]`` until it answers or runs out.

    Returns ``{seat: (rung_index, rung, outcome)}`` for the seats that answered.
    """
    answered: dict = {}
    nxt = dict(start)
    pending = list(start)
    while pending:
        wave = _assign_wave(rnd, pending, nxt, answered)
        pending = _settle(rnd, wave, _run_wave(rnd, wave), answered, nxt)
    return answered


# --- prompts and parsing -----------------------------------------------------------


def round1_prompt(seat: str, question: str, context: str = "") -> str:
    """Round 1: answer independently, position first, refuter named."""
    ctx = f"\nCONTEXT\n{context.strip()}\n" if context.strip() else ""
    return (
        f'You hold the "{seat}" seat in a three-seat deliberation. The other seats '
        "are held by models from other vendors, and in the next round each seat "
        "will try to falsify the others.\n\n"
        "Answer the question on its own merits.\n"
        "- Put your position in the first sentence.\n"
        "- Give the evidence or reasoning behind it, and say what you are unsure of.\n"
        "- Name the observation that would prove your position wrong.\n\n"
        f"QUESTION\n{question.strip()}\n{ctx}"
    )


def round2_prompt(seat: str, question: str, positions: dict) -> str:
    """Round 2 of 2: falsify each other seat under an exact section header."""
    others = [o for o in positions if o != seat]
    blocks = "\n".join(f"--- position of {o} ---\n{positions[o]}\n" for o in others)
    headers = "\n".join(f"=== FALSIFY {o} ===" for o in others)
    return (
        f'You hold the "{seat}" seat. This is round 2 of 2: debate and falsify.\n\n'
        f"QUESTION\n{question.strip()}\n\n"
        f"YOUR ROUND-1 POSITION\n{positions.get(seat, '(none)')}\n\n"
        f"THE OTHER SEATS' POSITIONS\n{blocks}\n"
        "For each other seat write one section, starting with its exact header line:\n"
        f"{headers}\n"
        "Each section holds four lines:\n"
        "CLAIM: its central claim in one sentence.\n"
        "EVIDENCE: the evidence it offers.\n"
        "REFUTER: the observation that would show the claim is wrong.\n"
        "VERDICT: REFUTED, WEAKENED or STANDS, then one line on why.\n"
        'End with a section headed "=== OWN POSITION ===" saying whether your own '
        "position changed, and why. Attack the claim, not the model.\n"
    )


_SECTION_RE = re.compile(
    r"^[ \t>*#]*={2,}[ \t]*(?:FALSIFY[ \t]+([A-Za-z0-9_.-]+)|OWN[ \t]+POSITION)[ \t]*={2,}[ \t*]*$",
    re.I | re.M)
_VERDICT_RE = re.compile(r"VERDICT\s*[:\-]\s*\**\s*(REFUTED|WEAKENED|STANDS)", re.I)


def split_falsifications(reply: str, others: list[str]) -> dict[str, str]:
    """Split a round-2 reply into one falsification per other seat.

    A seat whose section is missing or empty gets the whole reply, marked, so
    nothing the seat wrote is lost to a formatting slip.
    """
    marks = list(_SECTION_RE.finditer(reply))
    found: dict[str, str] = {}
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(reply)
        name = (mark.group(1) or "").lower()
        if name and name not in found:
            found[name] = reply[mark.end():end].strip()
    whole = reply.strip()
    return {o: found.get(o) or f"[no section for {o}; whole round-2 reply]\n{whole}"
            for o in others}


def verdict_of(falsification: str) -> str:
    """REFUTED, WEAKENED or STANDS as the falsifier stated it, else UNSTATED."""
    found = _VERDICT_RE.search(falsification)
    return found.group(1).upper() if found else "UNSTATED"


# --- the synthesis brief (for the main thread; never a model call) ---------------


def _seat_lines(core: dict) -> str:
    lines = ["SEATS"]
    for seat in core["seats"]:
        rung = core["rung_per_seat"].get(seat)
        if rung is None:
            lines.append(f"- {seat}: no rung answered")
        elif seat == core["main_thread_seat"]:
            lines.append(f"- {seat}: the main thread (this session)")
        else:
            lines.append(f"- {seat}: {rung} ({core['model_per_seat'][seat]}, "
                         f"{core['vendor_per_seat'][seat]})")
    if core["degraded"]:
        lines.append(f"- degraded: {', '.join(core['degraded'])} (a fallback stood in, "
                     "or the seat did not answer)")
    return "\n".join(lines)


def _position_lines(core: dict) -> str:
    lines = ["ROUND 1 -- POSITIONS"]
    for seat in core["seats"]:
        if seat in core["positions"]:
            lines.append(f"--- {seat} ---\n{core['positions'][seat]}")
        elif seat == core["main_thread_seat"]:
            lines.append(f"--- {seat} ---\n(held by the main thread: state its position below)")
    return "\n".join(lines)


def _falsification_lines(core: dict) -> str:
    if not core["falsifications"]:
        return ""
    lines = ["ROUND 2 -- FALSIFICATIONS"]
    for seat, targets in core["falsifications"].items():
        for other, text in targets.items():  # O(1): at most two other seats per falsifier
            lines.append(f"--- {seat} on {other} (verdict: {verdict_of(text)}) ---\n{text}")
    return "\n".join(lines)


def _contest(verdicts: dict) -> str:
    stated = set(verdicts.values()) - {"UNSTATED"}
    if stated & {"REFUTED", "WEAKENED"} or len(stated) > 1:
        return "contested"
    return "no seat refuted or weakened it" if stated else "no verdict stated"


def _disagreement_lines(core: dict) -> str:
    lines = ["WHERE THE SEATS DISAGREE"]
    if not core["falsifications"]:
        lines.append("- round 2 did not run, so no seat has tested another; weigh them yourself.")
        return "\n".join(lines)
    for target in core["positions"]:
        verdicts = {s: verdict_of(t[target])
                    for s, t in core["falsifications"].items() if target in t}  # O(1): three seats
        if verdicts:
            said = ", ".join(f"{s} {v}" for s, v in verdicts.items())
            lines.append(f"- {target}'s position: {said} -- {_contest(verdicts)}")
    return "\n".join(lines)


def _task_lines(core: dict) -> str:
    main = core["main_thread_seat"]
    lines = ["YOUR TASK"]
    if core["label"] == SINGLE_SEAT_LABEL:
        lines.append(f"THIS IS A SINGLE SEAT, NOT A COUNCIL. Only {', '.join(core['seats_used'])} "
                     "answered: treat it as one model's view, and say so.")
    if main and main not in core["positions"]:
        lines.append(f"You hold the {main} seat. First state your own position. Then falsify "
                     "each position above in the same four lines (CLAIM, EVIDENCE, REFUTER, VERDICT).")
    elif main:
        lines.append(f"You hold the {main} seat: your position is above and the other seats "
                     "have tested it. Falsify each other position in the same four lines.")
    lines.append("Synthesise. Keep each claim attached to the seat that made it. Where seats "
                 "disagree, say which side the evidence favours and what observation would "
                 "settle it. Do not average the positions into a compromise. Say what remains "
                 "unresolved, and name any degraded seat and the rung that stood in for it.")
    return "\n".join(lines)


def synthesis_prompt(core: dict) -> str:
    """The main thread's brief: positions, falsifications and disagreements, laid out."""
    parts = ["/ultrathink trio -- synthesis brief for the main thread",
             f"QUESTION\n{core['question']}"]
    if core["context"].strip():
        parts.append(f"CONTEXT\n{core['context'].strip()}")
    parts += [_seat_lines(core), _position_lines(core), _falsification_lines(core),
              _disagreement_lines(core), _task_lines(core)]
    return "\n\n".join(p for p in parts if p) + "\n"


# --- the seal ------------------------------------------------------------------------


def recompute(record: dict) -> str:
    """Re-derive a record's seal from its own core: equals ``record['sha256']`` unless edited."""
    return canonical_sha256(record["core"])


def leaf_decision(core: dict, sha: str) -> dict:
    """What the shared IDR chain learns: the seal and seat/rung metadata, never prose."""
    return {"sha256": sha, "label": core["label"], "seats_used": core["seats_used"],
            "rung_per_seat": core["rung_per_seat"], "rung_round2": core["rung_round2"],
            "vendor_per_seat": core["vendor_per_seat"], "degraded": core["degraded"]}


def _leaf_inputs(core: dict) -> dict:
    return {"seats": core["seats"], "rounds": core["rounds"],
            "allow_same_vendor": core["allow_same_vendor"],
            "main_thread_seat": core["main_thread_seat"]}


def _append_leaf(core: dict, sha: str, idr_path) -> Optional[str]:
    """Append the hash-only IDR leaf. Fail-open: the sha256 is the mandatory seal."""
    try:
        from lib.precog.idr import append_idr, build_idr

        idr = build_idr(prompt=f"ultrathink-trio:{sha}", fingerprint="ultrathink-trio",
                        decision=leaf_decision(core, sha), predecessor_idr=None, depth=0,
                        kind="ultrathink-trio", inputs=_leaf_inputs(core))
        append_idr(idr, path=Path(idr_path) if idr_path is not None else None)
        return idr.id
    except Exception as exc:  # noqa: BLE001 -- provenance over the mandatory sha256
        logger.warning("ultrathink trio: IDR leaf not written (%s); sha256-only provenance",
                       _reason(exc))
        return None


def _record_dir(record_dir) -> Path:
    """``$CLAUDE_DIR/state/ultrathink``, resolved when called, never at import."""
    if record_dir is not None:
        return Path(record_dir)
    from lib.claude_dir import get_claude_dir

    return get_claude_dir() / "state" / "ultrathink"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# --- the result -------------------------------------------------------------------


@dataclass
class TrioResult:
    """One deliberation: positions, falsifications, the ladder's path, and the seal."""

    question: str
    positions: dict
    falsifications: dict
    seats_used: list
    rung_per_seat: dict
    degraded: list
    latencies: dict
    synthesis_prompt: str
    record_path: str
    idr_leaf_id: Optional[str]
    sha256: str
    label: str = COUNCIL_LABEL
    rung_round2: dict = field(default_factory=dict)
    vendors: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)
    seats_requested: list = field(default_factory=list)
    allow_same_vendor: bool = False
    main_thread_seat: Optional[str] = None

    @property
    def single_seat(self) -> bool:
        return self.label == SINGLE_SEAT_LABEL

    def to_dict(self) -> dict:
        return asdict(self)


def _ordered(values: dict, order: list) -> dict:
    return {s: values[s] for s in order if s in values}


# --- one deliberation ----------------------------------------------------------------


class _Trio:
    """One deliberation's state. Each method is one phase; ``deliberate`` runs them."""

    def __init__(self, question: str, context: str, order: list, main_seat: Optional[str],
                 transports: dict, allow_same_vendor: bool) -> None:
        self.question, self.context, self.order = question.strip(), context, order
        self.main_seat, self.allow_same = main_seat, allow_same_vendor
        self.session = session_rung()
        self.transports = transports
        self.ladders = {s: LADDER[s] for s in order if s != main_seat}
        self.log = _Log()
        self.positions: dict[str, str] = {}
        self.round1: dict = {}
        self.round2: dict = {}
        self.falsifications: dict[str, dict[str, str]] = {}
        self.ran_round2 = False

    def _round(self, number: int, prompt_of: Callable[[str], str]) -> _Round:
        held = {self.session.vendor} if self.main_seat else set()
        return _Round(number, self.ladders, prompt_of, self.transports, held,
                      self.allow_same, self.log)

    def round_one(self, fable_position: str) -> None:
        rnd = self._round(1, lambda s: round1_prompt(s, self.question, self.context))
        self.round1 = _run_round(rnd, {s: 0 for s in self.ladders})
        found = {s: out.text for s, (_i, _r, out) in self.round1.items()}
        if self.main_seat and fable_position.strip():
            found[self.main_seat] = fable_position.strip()
        self.positions = _ordered(found, self.order)
        self._check_disjoint(self.round1)

    def voices(self) -> int:
        """Seats with a position, plus the main thread when it will state its own."""
        pending_main = self.main_seat is not None and self.main_seat not in self.positions
        return len(self.positions) + int(pending_main)

    def require_quorum(self, single_seat_ok: bool) -> None:
        heard = self.voices()
        if heard >= 2 or (heard == 1 and single_seat_ok):
            return
        raise UltrathinkUnavailable(self._refusal(heard), self.log.attempts)

    def round_two(self) -> None:
        speakers = [s for s in self.order if s in self.round1]
        if len(self.positions) < 2 or not speakers:
            return
        self.ran_round2 = True
        rnd = self._round(2, lambda s: round2_prompt(s, self.question, self.positions))
        self.round2 = _run_round(rnd, {s: self.round1[s][0] for s in speakers})
        self._check_disjoint(self.round2)
        for seat in speakers:
            if seat in self.round2:
                others = [o for o in self.positions if o != seat]
                self.falsifications[seat] = split_falsifications(self.round2[seat][2].text, others)

    def _check_disjoint(self, answered: dict) -> None:
        """F-1 again, over the set that actually answered. The ladder's refusals
        should make this unreachable; it stays as the check that cannot drift."""
        if self.allow_same:
            return
        models = [_bare(rung.model) for _idx, rung, _out in answered.values()]
        if self.main_seat:
            models.append(_bare(self.session.model))
        for i, model in enumerate(models):
            assert_disjoint(model, models[:i] + models[i + 1:])

    def _refusal(self, heard: int) -> str:
        what = ("no seat answered" if heard == 0 else
                "one seat answered; single_seat_ok=True accepts one model's view, labelled as such")
        tried = "; ".join(f"{a['seat']} r{a['round']} {a['rung']} {a['outcome']}"
                          + (f" ({a['reason']})" if a["reason"] else "") for a in self.log.attempts)
        clashed = sorted({a["seat"] for a in self.log.attempts if a["outcome"] == "refused"}
                         - set(self.round1))
        hint = (f"; no vendor-disjoint rung remained for {', '.join(clashed)}"
                " (allow_same_vendor=True permits a shared vendor)") if clashed else ""
        return f"/ultrathink trio refused, rather than fabricate a council: {what} ({tried}){hint}"

    def degraded(self) -> list[str]:
        """Seats that fell back in either round, or did not answer."""
        out = []
        for seat in self.ladders:
            first = self.round1.get(seat)
            fell = first is None or first[0] != 0
            if self.ran_round2 and first is not None:
                second = self.round2.get(seat)
                fell = fell or second is None or second[0] != first[0]
            if fell:
                out.append(seat)
        return out

    def core(self, rounds: int) -> dict:
        """The sealed body: a pure function of the run (no clock, no path)."""
        rung1 = {s: r.label for s, (_i, r, _o) in self.round1.items()}
        served = {s: o.served for s, (_i, _r, o) in self.round1.items()}
        vendors = {s: r.vendor for s, (_i, r, _o) in self.round1.items()}
        if self.main_seat:
            rung1[self.main_seat] = self.session.label
            served[self.main_seat] = self.session.model
            vendors[self.main_seat] = self.session.vendor
        return {
            "format": RECORD_FORMAT, "question": self.question, "context": self.context,
            "seats": self.order, "rounds": rounds, "main_thread_seat": self.main_seat,
            "allow_same_vendor": self.allow_same, "positions": self.positions,
            "falsifications": self.falsifications,
            "seats_used": [s for s in self.order if s in rung1],
            "rung_per_seat": _ordered(rung1, self.order),
            "rung_round2": {s: r.label for s, (_i, r, _o) in self.round2.items()},
            "model_per_seat": _ordered(served, self.order),
            "vendor_per_seat": _ordered(vendors, self.order),
            "degraded": self.degraded(),
            "label": SINGLE_SEAT_LABEL if self.voices() == 1 else COUNCIL_LABEL,
            "attempts": self.log.attempts,
        }

    def seal(self, rounds: int, record_dir, idr_path) -> TrioResult:
        """Write the record, then the hash-only IDR leaf, and return the result."""
        core = self.core(rounds)
        sha = canonical_sha256(core)
        brief = synthesis_prompt(core)
        path = _record_dir(record_dir) / f"{sha}.json"
        record = {"sha256": sha, "core": core, "synthesis_prompt": brief,
                  "latencies": self.log.seconds, "idr_leaf_id": None}
        _write_json(path, record)  # the record first: never a leaf without its record
        record["idr_leaf_id"] = _append_leaf(core, sha, idr_path)
        _write_json(path, record)
        return TrioResult(
            question=self.question, positions=core["positions"],
            falsifications=core["falsifications"], seats_used=core["seats_used"],
            rung_per_seat=core["rung_per_seat"], degraded=core["degraded"],
            latencies=dict(self.log.seconds), synthesis_prompt=brief, record_path=str(path),
            idr_leaf_id=record["idr_leaf_id"], sha256=sha, label=core["label"],
            rung_round2=core["rung_round2"], vendors=core["vendor_per_seat"],
            attempts=core["attempts"], seats_requested=list(self.order),
            allow_same_vendor=self.allow_same, main_thread_seat=self.main_seat)


def _session_is_fable(flag: Optional[bool]) -> bool:
    """An explicit flag wins; otherwise ``$GRIP_SESSION_MODEL`` naming Fable."""
    if flag is not None:
        return bool(flag)
    return "fable" in os.environ.get("GRIP_SESSION_MODEL", "").lower()


def _validate(question: str, seats, rounds: int, single_seat_ok: bool) -> list:
    if not str(question or "").strip():
        raise ValueError("a question is required")
    order = list(seats)
    unknown = [s for s in order if s not in LADDER]
    if unknown:
        raise ValueError(f"unknown seat(s) {unknown}; known: {sorted(LADDER)}")
    if len(set(order)) != len(order):
        raise ValueError(f"a seat may sit once: {order}")
    if len(order) < 2 and not single_seat_ok:
        raise ValueError("a council needs at least two seats (single_seat_ok=True accepts one)")
    if rounds not in (1, 2):
        raise ValueError("rounds is 1 (positions) or 2 (positions, then debate and falsify)")
    return order


def deliberate(question: str, context: str = "", seats=DEFAULT_SEATS, rounds: int = 2,
               transports=None, allow_same_vendor: bool = False, *,
               single_seat_ok: bool = False, session_fable: Optional[bool] = None,
               fable_position: str = "", record_dir=None, idr_path=None) -> TrioResult:
    """Run the trio: round 1 in parallel, round 2 debate and falsify, then seal.

    ``transports`` replaces the live transports (a mapping by kind, or one
    callable for every kind); tests inject it so nothing reaches the network.
    ``session_fable`` (default: ``$GRIP_SESSION_MODEL``) hands the fable seat
    to the main thread; ``fable_position`` is that thread's own round-1 answer.
    Raises :class:`UltrathinkUnavailable` when fewer than two seats answer and
    ``single_seat_ok`` is False, and writes nothing in that case.
    """
    order = _validate(question, seats, rounds, single_seat_ok)
    main = "fable" if "fable" in order and _session_is_fable(session_fable) else None
    trio = _Trio(question, context, order, main, _resolve_transports(transports),
                 allow_same_vendor)
    trio.round_one(fable_position)
    trio.require_quorum(single_seat_ok)
    if rounds == 2:
        trio.round_two()
    return trio.seal(rounds, record_dir, idr_path)


# --- the footer and the CLI ----------------------------------------------------------


def fallback_notes(result: TrioResult) -> list[str]:
    """One line per rung that did not answer, with its reason."""
    notes = []
    for att in result.attempts:
        if att["outcome"] == "answered":
            continue
        why = f" -- {att['reason']}" if att["reason"] else ""
        notes.append(f"{att['seat']} r{att['round']} {att['rung']}: {att['outcome']}{why}"[:160])
    return notes


def _seat_line(result: TrioResult, seat: str) -> str:
    rung = result.rung_per_seat.get(seat)
    if rung is None:
        return f"{seat:<9}: no rung answered"
    if seat == result.main_thread_seat:
        return f"{seat:<9}: session (the main thread)"
    second = result.rung_round2.get(seat)
    late = f", round 2 by {second}" if second and second != rung else ""
    return f"{seat:<9}: {rung}  {result.latencies.get(seat, 0.0):.1f}s{late}"


def render_footer(result: TrioResult) -> str:
    """The /ultrareason-style card: rung per seat, latency, degraded, record, seal."""
    lines = ["┌─ /ultrathink · trio ─────────────────────────────────"]
    lines += [f"│ {_seat_line(result, seat)}" for seat in result.seats_requested]
    lines += [f"│   • {note}" for note in fallback_notes(result)]
    if result.degraded:
        lines.append(f"│ degraded : {', '.join(result.degraded)}")
    vendors = " · ".join(f"{s} {v}" for s, v in result.vendors.items())
    rule = "same vendor ALLOWED, not disjoint" if result.allow_same_vendor else "disjoint"
    lines.append(f"│ vendors  : {vendors} ({rule})")
    if result.single_seat:
        lines.append(f"│ label    : {SINGLE_SEAT_LABEL}")
    lines.append(f"│ record   : {result.record_path}")
    lines.append(f"│ sealed   : sha256 {result.sha256[:16]} · IDR {result.idr_leaf_id or 'not written'}")
    lines.append("└──────────────────────────────────────────────────────")
    return "\n".join(lines)


def _read_optional(path: Optional[str]) -> str:
    return Path(path).read_text(encoding="utf-8") if path else ""


def _parse_args(argv: Optional[list]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="grip-ultrathink",
        description="/ultrathink trio: three frontier seats from three vendors answer in "
                    "parallel, then debate and falsify each other.")
    parser.add_argument("question", nargs="+", help="the question to deliberate")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--seats", default=",".join(DEFAULT_SEATS),
                        help="comma-separated seats (default: %(default)s)")
    parser.add_argument("--rounds", type=int, choices=(1, 2), default=2,
                        help="1 = positions only; 2 = positions, then debate and falsify")
    parser.add_argument("--allow-same-vendor", action="store_true",
                        help="let a fallback share a vendor with another seat (recorded)")
    parser.add_argument("--single-seat-ok", action="store_true",
                        help="return one seat's answer, labelled 'single seat, not a council'")
    parser.add_argument("--session-fable", action="store_true", default=None,
                        help="this session runs on Fable, so the main thread holds the "
                             "fable seat (default: read $GRIP_SESSION_MODEL)")
    parser.add_argument("--fable-position-file",
                        help="with --session-fable: a file holding the main thread's position")
    parser.add_argument("--context-file", help="a file of extra context for round 1")
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    """CLI: deliberate, then print the synthesis brief and the footer (or JSON)."""
    args = _parse_args(argv)
    try:
        context = _read_optional(args.context_file)
        position = _read_optional(args.fable_position_file)
    except OSError as exc:
        print(f"grip-ultrathink: {exc}", file=sys.stderr)
        return 64
    try:
        result = deliberate(
            " ".join(args.question), context=context,
            seats=[s.strip() for s in args.seats.split(",") if s.strip()],
            rounds=args.rounds, allow_same_vendor=args.allow_same_vendor,
            single_seat_ok=args.single_seat_ok, session_fable=args.session_fable,
            fable_position=position)
    except ValueError as exc:
        print(f"grip-ultrathink: {exc}", file=sys.stderr)
        return 64
    except RoundtableError as exc:  # UltrathinkUnavailable, RoundtableVendorError
        print(f"grip-ultrathink: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        print(render_footer(result), file=sys.stderr)
    else:
        print(result.synthesis_prompt)
        print(render_footer(result))
    return 0


if __name__ == "__main__":
    # The shebang is `/usr/bin/env python3`, which need not be an interpreter
    # that can dual-sign. Without this the tool still runs and still records:
    # it just writes a forgeable demo-sealed IDR leaf when a real seal was one
    # interpreter away (lib/pq_reexec.py). Imported as top-level `pq_reexec`
    # off THIS checkout, as lib/roundtable.py does and for the same reason.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))

    sys.exit(main())
