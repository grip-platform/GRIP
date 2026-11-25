#!/usr/bin/env python3
"""lib/roundtable.py — the Knighted Roundtable council.

A three-tier council that spends a sliver of FRONTIER quota once to DESIGN a
judging, then lets CHEAP (DeepSeek-heavy) seats do the iterative deliberation,
under a vendor-disjoint rotating frontier judge.

    Tier 0  ROUNDTABLE — frontier advisors (rotate 3-of-4) emit a judging PLAN:
             {seats, roles, bespoke_prompts, rotation, criterion}
    Tier 1  KNIGHTS — cheap, vendor-diverse seats deliberate under that plan
    Tier 2  JUDGE — rotating frontier model, vendor-disjoint from the knights

This file is STEP 1: the Tier-0 emitter plus the three falsifier guards the
build pre-registered:

    F-1  vendor-disjointness is enforced IN CODE (:func:`assert_disjoint`),
         not prose. A judge and speaker that share a vendor is refused.
    F-2  the plan and its seal are deterministic pure functions of the inputs
         (:func:`assemble_plan` + :func:`plan_sha256`) — replay yields the
         identical sha256, so a tampered plan is distinguishable by recompute.
    F-3  a roundtable whose EVERY advisor degrades REFUSES to emit a plan
         (:class:`RoundtableUnavailableError`) — a fabricated plan wears a
         council's clothes and is worse than none.

Composition, not re-implementation: the chat transport reuses
``lib.sovereign_council.provider_key`` for the credential ladder. Anthropic
advisors (Opus 5.5 / Fable 5.1) ride ``claude -p`` because the HAL gateway
does not know ``claude-*`` ids (recorded in
memory/reasoning_crew_kimi_opus5_astra_deepseek.md); the invocation there was
smoke-tested 2026-09-13/23.

The live transport is the default ``call`` and is deliberately thin; tests
inject a stub so no test ever touches the network or a subprocess.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# --- vendor model -----------------------------------------------------------
#
# The exogenous-anchor-law constraint, made a lookup instead of a comment: every
# seat resolves to a vendor, and :func:`assert_disjoint` refuses a judge whose
# vendor appears among the speakers (producer == verifier is the one shape the
# law forbids). A name missing from VENDOR is resolved by the family classifier
# GRIP already keeps (lib/model_attribution.py), so a NEW id of a known family
# lands on its vendor. Only a truly unknown name maps to itself — and that alone
# was never safe: before the classifier fallback, claude-fable-5-1 mapped to
# itself and a Fable 5.1 judge passed as disjoint from an Opus 5.5 speaker, two
# Anthropic minds (measured 2026-09-24, the Opus 5.5 onboarding).

VENDOR = {
    # DeepSeek (the knight workhorse)
    "deepseek": "deepseek",
    "deepseek-v4-pro": "deepseek",
    "deepseek-flash": "deepseek",
    # xAI
    "xai": "xai",
    "grok-4.6": "xai",
    "grok-4.20-0309-reasoning": "xai",
    # OpenAI
    "openai": "openai",
    "astra": "openai",
    "gpt-6-astra": "openai",
    # Anthropic
    "opus": "anthropic",
    "claude-opus-5-5": "anthropic",
    "fable": "anthropic",
    "claude-fable-5": "anthropic",
    # Google
    "gemini": "google",
    "gemini-pro": "google",
    "gemini-flash": "google",
    "gemini-3.5-flash-lite": "google",
    "gemini-argon": "google",
    "gemini-4-argon": "google",
    # others
    "groq": "meta",
    "qwen": "alibaba",
    "qwen3.5-flash": "alibaba",
}


def _family_vendor(model_id: str) -> str:
    """The vendor lib/model_attribution.py assigns a model id, or ``unknown``."""
    try:
        from lib.model_attribution import vendor_of as attribution_vendor
    except ImportError:
        try:
            from model_attribution import vendor_of as attribution_vendor
        except ImportError:
            return "unknown"
    return attribution_vendor(model_id)


def vendor_of(seat: str) -> str:
    """Resolve a seat name (or model id) to its vendor; an unknown name is itself."""
    if seat in VENDOR:
        return VENDOR[seat]
    family = _family_vendor(seat)
    return family if family != "unknown" else seat


# --- rosters ----------------------------------------------------------------

# The roundtable (Tier 0): four DISTINCT vendors. The rotation seats the two
# non-Anthropic advisors always and alternates the Anthropic seat, so a run is
# never three Anthropic minds and never drops to two vendors.
ADVISORS = [
    {"name": "xai",   "model": "grok-4.6",        "vendor": "xai",       "transport": "chat"},
    {"name": "astra", "model": "gpt-6-astra",     "vendor": "openai",    "transport": "council"},
    {"name": "opus",  "model": "claude-opus-5-5", "vendor": "anthropic", "transport": "claude-p"},
    {"name": "fable", "model": "claude-fable-5",  "vendor": "anthropic", "transport": "claude-p"},
]

# Chat-transport endpoint + keychain service, keyed by advisor name. The
# ``claude-p`` advisors are not here: they ride the Claude Code CLI, not a URL.
_ADVISOR_ENDPOINTS = {
    "xai":   ("https://api.x.ai/v1/chat/completions",        "grok-4.6",    "grip-xai"),
    "gemini-argon": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                     "gemini-4-argon", "grip-gemini"),
}

# Gemini 4 Argon as an opt-in Google advisor (GA 2026-10-13). It is not in ADVISORS,
# so the default rotation is unchanged. Pass NON_ANTHROPIC_ADVISORS to emit_plan for
# a Tier 0 of three vendors with no Anthropic mind (V>> 2026-10-30: lean on GPT and
# Google, not only Anthropic).
ARGON_ADVISOR = {"name": "gemini-argon", "model": "gemini-4-argon", "vendor": "google", "transport": "chat"}
NON_ANTHROPIC_ADVISORS = [a for a in ADVISORS if a["vendor"] != "anthropic"] + [ARGON_ADVISOR]

# The knights (Tier 1): DeepSeek-heavy, vendor-diverse, cheap. The full roster
# is the plan's ceiling; the emitted plan may seat any subset.
KNIGHTS = [
    {"name": "deepseek",       "model": "deepseek-v4-pro",           "vendor": "deepseek", "transport": "chat"},
    {"name": "deepseek-flash", "model": "deepseek-flash",            "vendor": "deepseek", "transport": "chat"},
    {"name": "qwen",           "model": "qwen3.5-flash",             "vendor": "alibaba",  "transport": "chat"},
    {"name": "gemini-flash",   "model": "gemini-3.5-flash-lite",     "vendor": "google",   "transport": "chat"},
]

# --- errors -----------------------------------------------------------------


class RoundtableError(RuntimeError):
    """Base for every roundtable refusal (distinct from a bare RuntimeError)."""


class RoundtableVendorError(RoundtableError):
    """Raised by :func:`assert_disjoint` when producer == verifier (F-1)."""


class RoundtableUnavailableError(RoundtableError):
    """Raised when every advisor degraded and no plan could be emitted (F-3).

    Carries every advisor's result in ``advisors``, and names each error in the
    message, so the cause survives the refusal. It used to be dropped: a KERN
    run's planner failed once and the reason could not be recovered (GRIP#6653).
    """

    def __init__(self, message: str, advisors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.advisors = list(advisors or [])


# --- F-1: vendor disjointness, enforced -------------------------------------


def assert_disjoint(judge: str, speakers: list[str]) -> None:
    """Refuse a judge whose vendor appears among the speakers.

    The Gemini-removal lesson from ``lib/sovereign_council.py`` (a Google
    speaker under a Google judge) was enforced by comment until now; this is
    the mechanical version. Raises :class:`RoundtableVendorError` on the first
    offending speaker, naming both sides.
    """
    judge_vendor = vendor_of(judge)
    for speaker in speakers:
        speaker_vendor = vendor_of(speaker)
        if speaker_vendor == judge_vendor:
            raise RoundtableVendorError(
                f"judge {judge!r} ({judge_vendor}) shares a vendor with speaker "
                f"{speaker!r} ({speaker_vendor}); exogenous-anchor-law.md forbids "
                "producer == verifier."
            )


# --- F-2: deterministic plan + seal -----------------------------------------
#
# The plan is a pure function of (prompt, advisor outputs): no clocks, no set
# iteration order, no randomness. The seal is a sha256 over a canonical JSON
# serialisation, so an identical plan re-seals to an identical digest.

_PLAN_FIELDS = ("seats", "roles", "bespoke_prompts", "rotation", "criterion")

#: Defaults when no advisor supplied a field — a plan must still be well-formed.
_DEFAULTS = {
    "seats": ["deepseek", "qwen", "gemini-flash"],
    "roles": {},
    "bespoke_prompts": {},
    "rotation": ["round-1", "round-2"],
    "criterion": "stop when two consecutive rounds agree",
}


def _strip_fence(raw: str) -> str:
    """Unwrap a ```json ... ``` block, including an unclosed one."""
    text = raw.strip()
    if not text.startswith("```"):
        return text
    body = text[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    return body.split("```", 1)[0].strip()


def _parse_advisor(raw: str) -> dict:
    """Parse one advisor's reply to a dict, or {} on any failure."""
    try:
        parsed = json.loads(_strip_fence(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _canonical_bytes(plan: dict) -> bytes:
    """Canonical, byte-stable JSON — the substrate the seal hashes."""
    return json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")


def plan_sha256(plan: dict) -> str:
    """Deterministic sha256 over the canonical plan bytes (F-2)."""
    return hashlib.sha256(_canonical_bytes(plan)).hexdigest()


def assemble_plan(prompt: str, advisor_outputs: dict[str, str]) -> dict:
    """Deterministically merge advisor replies into a plan.

    ``advisor_outputs`` maps advisor name -> raw reply text. Advisors are
    consulted in SORTED name order, and the first non-empty value for each plan
    field wins — so the same inputs always produce the byte-identical plan
    (F-2). Missing fields fall back to :data:`_DEFAULTS`.
    """
    parsed = [
        (name, _parse_advisor(raw))
        for name, raw in sorted(advisor_outputs.items())
    ]

    def _pick(field: str):
        for _name, obj in parsed:
            value = obj.get(field)
            if value not in (None, "", [], {}):
                return value
        return _DEFAULTS[field]

    plan = {"prompt": prompt}
    for field in _PLAN_FIELDS:
        plan[field] = _pick(field)
    return plan


# --- rotation ----------------------------------------------------------------


def rotate_advisors(advisors: list[dict] | None = None, seed: int = 0) -> list[dict]:
    """Deterministic 3-of-4 rotation.

    The two non-Anthropic advisors are always seated; the Anthropic seat
    alternates by ``seed`` parity. This keeps three vendors on every run and
    never lets a run collapse to all-Anthropic.
    """
    roster = advisors or ADVISORS
    non_anthropic = [a for a in roster if a["vendor"] != "anthropic"]
    anthropic = [a for a in roster if a["vendor"] == "anthropic"]
    chosen = list(non_anthropic)
    if anthropic:
        chosen.append(anthropic[seed % len(anthropic)])
    return chosen


# --- live transport (injectable; the default ``call``) -----------------------


def _claude_p_call(model: str, prompt: str, system: str) -> dict:
    """One Anthropic advisor call via the Claude Code CLI (``claude -p``).

    The globally-set ``ANTHROPIC_API_KEY`` has a dead credit balance and shadows
    the Max OAuth login, so it is unset for the call (smoke-tested 2026-09-13).

    ``system`` goes in through ``--append-system-prompt``, the counterpart of
    the chat transport's system message. Without it a Claude planner was asked
    the question but never told the answer must be plan JSON (GRIP#6653).

    The call is text-only (lib/claude_text_only.py): an advisor answers, it never
    acts, and without the lock it could run any Bash command settings.json
    approves. A lock that cannot load sends nothing.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    try:
        argv = ["claude", "-p", "--model", model, *_text_only_flags()]
    except OSError as exc:
        return {"ok": False, "model": model, "text": f"{type(exc).__name__}: {exc}"}
    try:
        proc = subprocess.run(  # both texts pass the redaction shield; a failed scrub is an OSError
            argv + ["--append-system-prompt", _shield(system, to=argv), _shield(prompt, to=argv)],
            capture_output=True, text=True, timeout=180, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "model": model, "text": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "model": model,
                "text": (proc.stderr or f"claude exit {proc.returncode}").strip()[:200]}
    return {"ok": True, "model": model, "text": proc.stdout.strip()}


def _text_only_flags() -> tuple:
    """The flags that leave a ``claude -p`` advisor no tool, in every invocation
    shape; when they cannot be loaded it raises OSError and nothing is sent."""
    try:
        from claude_text_only import TEXT_ONLY_FLAGS
    except ImportError:
        try:
            from lib.claude_text_only import TEXT_ONLY_FLAGS
        except ImportError as exc:  # no lock, no call: an unlocked seat could act
            raise OSError("the text-only lock is unavailable, so nothing was sent") from exc
    return TEXT_ONLY_FLAGS


def _shield(value, to=None):
    """``value`` scrubbed by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send: callers treat OSError as a failed send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(value, to=to)



def _provider_key_lookup():
    """The credential ladder, from the ONE module object everyone else holds.

    lib/sovereign_council.py can be bound under two names: ``lib.sovereign_council``
    (every package-style caller, and every test, which patches ``provider_key``
    on that object) and the sibling ``sovereign_council`` (what a run by file
    path binds, GRIP#8430). GRIP#8434 imported the sibling name here first,
    which creates a SECOND module object when the package form is already
    loaded, so a stub on the first was never seen: locally the keychain
    answered and nothing failed, while in CI, with no xai key, the roundtable's
    advisor calls reached the real resolver and three tests went red on every
    PR from 2026-10-17 01:55 UTC. So: take whichever name is ALREADY loaded,
    package form first, and import only when neither is. The import order
    keeps #8434's reason for the sibling form: a flat-module lookup is immune
    to a foreign REGULAR ``lib`` package later on sys.path, which would
    otherwise displace this checkout's namespace-package ``lib/`` (Codex P2 on
    that PR, reproduced with a decoy carrying lib/__init__.py).
    """
    for name in ("lib.sovereign_council", "sovereign_council"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "provider_key"):
            return module.provider_key
    try:
        from sovereign_council import provider_key
    except ImportError:
        from lib.sovereign_council import provider_key
    return provider_key


def _chat_call(name: str, messages: list[dict]) -> dict:
    """One chat-transport advisor call against its OpenAI-compatible endpoint.

    The messages pass the redaction shield inside the try, so a failed scrub is
    reported like any other failed call and nothing is sent."""
    provider_key = _provider_key_lookup()

    url, model, service = _ADVISOR_ENDPOINTS[name]
    try:
        body = {"model": model, "messages": messages, "max_tokens": 4000, "temperature": 0.4}
        req = urllib.request.Request(
            url, data=json.dumps(_shield(body, to=url)).encode(),
            headers={"Authorization": f"Bearer {provider_key(service)}",
                     "Content-Type": "application/json", "User-Agent": "grip-roundtable/1.0"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.load(resp)
        choices = data.get("choices") or []
        text = (choices[0].get("message", {}) or {}).get("content", "") if choices else ""
        return {"ok": bool(text), "model": model, "text": text.strip()}
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            detail = exc.read().decode(errors="replace")[:120]
        return {"ok": False, "model": model,
                "text": f"{type(exc).__name__}: {exc} {detail}".strip()}


def _live_advisor_call(advisor: dict, prompt: str, t0: float) -> dict:
    """Dispatch one advisor call by transport. Returns an ok/text/model dict."""
    start = time.time() - t0
    if advisor["transport"] == "claude-p":
        res = _claude_p_call(advisor["model"], prompt, _ADVISOR_SYSTEM)
    else:
        msgs = [
            {"role": "system", "content": _ADVISOR_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        if advisor["transport"] == "council":
            try:
                from sovereign_council import provider_call
            except ImportError:
                from lib.sovereign_council import provider_call
            res = provider_call(advisor["name"], msgs, t0)
        else:
            res = _chat_call(advisor["name"], msgs)
    res["name"] = advisor["name"]
    res["vendor"] = advisor["vendor"]
    res["start"] = start
    res["end"] = time.time() - t0
    return res


#: The instruction each advisor receives. It must return ONLY the plan JSON.
_ADVISOR_SYSTEM = (
    "You are one of three frontier advisors designing how a council of CHEAP "
    "models will judge a question. Return ONLY compact JSON, no prose, with "
    'keys: "seats" (list of seat names chosen from '
    "deepseek, deepseek-flash, qwen, gemini-flash), "
    '"roles" (seat -> role: reasoner, devil, pragmatist, integrator), '
    '"bespoke_prompts" (seat -> one-line system instruction), '
    '"rotation" (ordered round labels), and "criterion" (the stop condition).'
)


# --- F-3 + orchestration ------------------------------------------------------


def emit_plan(prompt: str, advisors: list[dict] | None = None,
              call=_live_advisor_call) -> dict:
    """Tier 0: frontier advisors emit the judging plan IN PARALLEL.

    Refuses (F-3) when every advisor degraded — a fully-canned plan wearing a
    roundtable's clothes is worse than none (the ``ultrajudge.py`` contract).
    """
    roster = advisors or rotate_advisors()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=len(roster)) as ex:
        results = [f.result() for f in
                   [ex.submit(call, adv, prompt, t0) for adv in roster]]

    advisors_ok = sum(1 for r in results if r.get("ok"))
    if advisors_ok == 0:
        causes = "; ".join(f"{r.get('name', '?')}: {str(r.get('text') or 'no output')[:200]}"
                           for r in results)
        raise RoundtableUnavailableError(
            f"every frontier advisor degraded; refusing to emit a fabricated plan ({causes})",
            advisors=results)

    outputs = {r["name"]: r.get("text", "") for r in results}
    plan = assemble_plan(prompt, outputs)
    return {
        "plan": plan,
        "plan_sha": plan_sha256(plan),
        "advisors": results,
        "advisors_ok": advisors_ok,
        "total": time.time() - t0,
    }


def _require_shape(result, keys: tuple[str, ...], boundary: str) -> dict:
    """Validate a seam's return: a dict carrying ``keys``, else a named error.

    A broken seam (an injected ``emit``/``convene``, or a future refactor of
    ``sovereign_council.roundtable``) must raise a :class:`RoundtableError`
    naming the gap, never a bare KeyError that a caller has to decode.
    """
    if not isinstance(result, dict):
        raise RoundtableError(
            f"{boundary} must return a dict; got {type(result).__name__}")
    missing = [k for k in keys if k not in result]
    if missing:
        raise RoundtableError(
            f"{boundary} returned an unexpected shape; missing key(s): "
            + ", ".join(missing))
    return result


# run_roundtable's seams. ``emit`` and ``convene`` exist for hermetic tests: a
# test injects both and never touches the network or a subprocess. Both are
# shape-checked by _require_shape, so a malformed return is a loud
# RoundtableError, never a bare KeyError. ``emit`` must return a dict carrying
# _EMIT_KEYS; ``convene`` (the delegated ``sovereign_council.roundtable``) must
# return _CONVENE_KEYS. ``idr_id`` and ``speakers_ok`` are optional.
_EMIT_KEYS = ("plan", "plan_sha", "advisors", "advisors_ok")
_CONVENE_KEYS = ("verdict", "record", "sha")


def _composed(plan_result: dict, verdict: dict) -> dict:
    """One record from both halves: the orchestrator's plan, then the
    consultant, panel, judge and seal."""
    return {
        **{k: plan_result[k] for k in _EMIT_KEYS},
        **{k: verdict[k] for k in _CONVENE_KEYS},
        "idr_id": verdict.get("idr_id"),
        "speakers_ok": verdict.get("speakers_ok"),
    }


def run_roundtable(prompt: str, advisor: str = "astra",
                   speakers: list[str] | None = None,
                   judge_name: str | None = None,
                   emit=emit_plan, convene=None) -> dict:
    """Compose the Knight's Roundtable across its two halves.

    Tier 0b (ORCHESTRATOR) — frontier advisors emit the judging PLAN
    (seats/roles/bespoke prompts/rotation/criterion): :func:`emit_plan`, this
    module. Tier 0a+1+2 (CONSULTANT + panel + judge + seal) — delegates to
    ``lib.sovereign_council.roundtable``, whose consultant sharpens the
    question, the panel deliberates under it, and the judge renders a sealed,
    IDR-linked verdict (the other half of the design, merged in PR #6566).

    The plan and its ``plan_sha`` are RETURNED alongside the sealed verdict.
    They are not yet sealed or stored: ``convene`` never receives them, so only
    the verdict half is sealed (GRIP#6653). The consultant brief, not the plan,
    is what steers the panel. The seams are described above.
    """
    plan_result = _require_shape(emit(prompt), _EMIT_KEYS, "emit")
    if convene is None:
        from lib import sovereign_council as sc  # lazy: no cycle, no dep at import
        convene = sc.roundtable
    verdict = _require_shape(
        convene(prompt, advisor=advisor, speakers=speakers, judge_name=judge_name),
        _CONVENE_KEYS, "convene")
    return _composed(plan_result, verdict)


def main(argv: list[str] | None = None) -> int:
    """CLI: emit a roundtable plan for a prompt and print it, sealed.

    The plan and its sha256 go to stdout; a fully-degraded roundtable is a
    non-zero exit (F-3), never a fabricated plan printed as a success.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="roundtable",
        description="Knighted Roundtable — frontier advisors design a judging plan.")
    parser.add_argument("prompt", nargs="+", help="the question to design a judging for")
    args = parser.parse_args(argv)
    prompt = " ".join(args.prompt)

    try:
        result = emit_plan(prompt)
    except RoundtableUnavailableError as exc:
        print(f"roundtable: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result["plan"], indent=2, sort_keys=True))
    print(f"# plan_sha256 {result['plan_sha']}")
    print(f"# advisors_ok {result['advisors_ok']} in {result['total']:.2f}s")
    return 0


if __name__ == "__main__":
    # This shebang is `/usr/bin/env python3`, which need not be an interpreter
    # that can dual-sign. Without this the tool still runs and still records --
    # it just writes a forgeable demo-sealed record when a real seal was one
    # interpreter away (lib/pq_reexec.py).
    #
    # Imported as top-level `pq_reexec` off THIS checkout, never as
    # `lib.pq_reexec`: by now `lib` may already be bound to another tree's
    # package, and the import would miss the file or read another branch's copy.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))

    # The repo root goes on sys.path too, mirroring the `_ROOT` pattern in
    # lib/sovereign_council.py. Without it only lib/ is on the path, and the
    # package-style import inside _chat_call (`from lib.sovereign_council
    # import provider_key`) cannot resolve when this file is run by path
    # (`grip-py lib/roundtable.py "<question>"`) -- GRIP#8430. `-m
    # lib.roundtable` never hit this, because the module form already puts
    # the invoking cwd on the path.
    #
    # Moved to the FRONT unconditionally, never merely inserted if absent:
    # `lib` is a namespace package (no __init__.py), so when THIS checkout's
    # root is already SOMEWHERE on sys.path (e.g. via an inherited
    # PYTHONPATH) but behind a foreign checkout or the shared install, the
    # old "insert only if missing" guard left it there -- and the earlier
    # foreign `lib/sovereign_council.py` won the namespace-package submodule
    # search instead (PR #8434 review, Codex: reproduced with
    # `PYTHONPATH=<foreign-root>:<this repo>`, which resolved the foreign
    # tree's module even though this root was technically "on the path").
    # Stripping any existing occurrence before inserting is what makes "this
    # checkout wins" true regardless of what PYTHONPATH already contained.
    _repo_root = str(_Path(__file__).resolve().parent.parent)
    _sys.path[:] = [_p for _p in _sys.path if _p != _repo_root]
    _sys.path.insert(0, _repo_root)

    sys.exit(main())
