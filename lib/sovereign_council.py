"""GRIP Sovereign Council core — a multi-LLM council that runs with ZERO Anthropic.

The reusable library extracted from ``bin/grip-sovereign-council`` at its 3rd
consumer (the CLI itself, ``bin/grip-sovereign-demo``, and now
``bin/grip-council-endpoint``) — the factoring the CLI's own header flagged:
"At a 3rd consumer, factor into lib/…​ — not before (YSH inverse / YAGNI)."

The council pattern (dogfoods AGORA / exogenous-anchor on a pure non-Anthropic
substrate):

  1. FAN-OUT   — the question is answered IN PARALLEL by N non-Anthropic speakers.
  2. JUDGE     — a *different* non-Anthropic model ranks the answers and
                 synthesises the consensus (exogenous to each speaker — no model
                 grades its own homework; exogenous-anchor-law.md, on other LLMs).
  3. SEAL      — the verdict is sealed with deterministic, tamper-evident
                 provenance: a sha256 over {prompt, every answer, judge verdict},
                 plus a signed IDR leaf. Edit any byte and ``recompute`` /
                 ``--verify`` reports TAMPERED.

Every unit of cognition here — speakers AND judge — is a non-Anthropic model,
with ONE operator-directed exception: the ``fable`` SPEAKER seat (V>>
2026-09-24, temporary while the astra key is credit-dead), counted honestly
in every sealed record's ``anthropic_calls`` and refused as judge.
GRIP (the Anthropic REPL) only orchestrates. Provider keys are read in-process
from the env -> Keychain -> /proc ladder; never printed.

What a STRANGER can and cannot verify, stated plainly because the distinction
decides what a sealed record is worth. INTEGRITY is checkable by anyone,
always, with no keys and no GRIP install: ``recompute`` re-derives the sha256
over {prompt, every answer, judge verdict} from the record's own bytes, so a
single altered character reports TAMPERED, and ``resolve_record_path`` lets
that run against the file wherever it lies. ATTRIBUTION — that THIS operator's
keys sealed it, rather than anyone who can write a JSON file — needs the
operator's PUBLISHED public keys, supplied via ``--pubkeys`` or
``GRIP_COUNCIL_VERIFY_KEYS`` and consumed by :func:`load_verify_keys`; without
them the signatures on the IDR leaf are present but unchecked, and the verdict
line says exactly that rather than implying more. Publishing the keys is the
operator's decision; the affordance to consume them is this tool's.

Public API:
  REGISTRY, DEFAULT_SPEAKERS, DEFAULT_JUDGE   — the roster
  premise_advisory(prompt) -> str | None       — Gate-0 seed premise check
  fan_out(prompt, speakers) -> (results, total)
  judge(prompt, results, judge_name) -> verdict
  seal(prompt, results, verdict) -> (record, sha)
  run_council(prompt, speakers=None, judge_name=None) -> dict   — one-call council
  recompute(record) -> sha                     — deterministic re-hash (verify)
  _verify_idr / _council_merkle_root           — IDR provenance helpers
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# Ensure both repo root and lib/ are on sys.path so IDR/audit_chain imports
# resolve whether imported as ``sovereign_council`` (lib on path) or
# ``lib.sovereign_council`` (root on path), from a bin, a test, or the endpoint.
_ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# CI-vs-Mac key ladder: env (DEEPSEEK_API_KEY...) -> GRIP_<P>_API_KEY -> Keychain
# -> Linux /proc. Lets this council run unchanged on Mac dev AND in Linux CI/VPS.
from provider_secret import resolve_provider_key_or_raise  # noqa: E402
# RFC 8785 canonical JSON. Imported for the VERIFIER only at this stage:
# `canonical_bytes` honours a record's own declared format and defaults to
# the legacy form, so nothing about how records are SEALED changes here.
import jcs  # noqa: E402

# The canonical install-directory resolver (rules/path-portability.md). Used for
# STATE_DIR below and deliberately NOT for _ROOT above -- see the note there.
from claude_dir import get_claude_dir  # noqa: E402
import throttle_flag  # noqa: E402  the read-only half of the tick's throttle flag, for the Claude seat
from claude_text_only import TEXT_ONLY_FLAGS  # noqa: E402  the Claude seat answers, it never acts

# Council-seed premise gate (task #474): the family-B doctrine clause from
# rules/dispatch-economics.md Gate 0 — a council asked about a CONCLUSION
# ("is the tool bad?") dutifully confirms a false premise; the seed must
# carry a ground-truth citation for the premise itself. Fail-open import,
# but never SILENT fail-open: one boot-time warning per process makes
# infrastructure rot audible without ever blocking a council (AGORA #4038).
try:
    from premise_reproduce_gate import classify_dispatch_prompt as _classify_premise  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    _classify_premise = None
    logging.getLogger(__name__).warning(
        "confab premise gate unavailable — council seeds run unchecked: %s", _exc)

logger = logging.getLogger(__name__)

# name -> (OpenAI-compatible chat endpoint, model, keychain service)
REGISTRY = {
    "deepseek":   ("https://api.deepseek.com/v1/chat/completions", "deepseek-v4-pro", "grip-deepseek"),
    # deepseek-reasoner: a RETAINED ALIAS of the default `deepseek` leg — both
    # deliberately resolve to deepseek-v4-pro (one backend, not two personas).
    # The old distinct models (deepseek-chat + deepseek-reasoner) were BOTH
    # retired at the provider (HTTP 400, probed 2026-07-26); v4-pro is the current
    # reasoning flagship and DeepSeek ships no separate "more-reasoning" variant,
    # so the keys coincide. Kept as a named alias for callers that reference it
    # (recall design councils — seals 8acc9c14, bf070637). Default `deepseek` uses
    # v4-pro per V>>'s directive (reasoning, not the v4-flash chat/fast tier).
    "deepseek-reasoner": ("https://api.deepseek.com/v1/chat/completions", "deepseek-v4-pro", "grip-deepseek"),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-3.5-flash-lite", "grip-gemini"),
    "gemini-pro": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-3.1-pro-preview", "grip-gemini"),
    # Gemini 4 Argon, GA 2026-10-13 (vendor changelog). Seatable by name as a judge;
    # DEFAULT_JUDGE stays gemini-pro, because moving it is GRIP#8566's decision, held
    # while H834 is open (Argon and 3.1 Pro agreed on 8 of 20 replayed PRs).
    "gemini-argon": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-4-argon", "grip-gemini"),
    "groq":       ("https://api.groq.com/openai/v1/chat/completions", "openai/gpt-oss-120b", "grip-groq"),
    # grok-3 retired at the provider; grok-4.5 live-verified against
    # api.x.ai/v1/models 2026-07-17.
    "xai":        ("https://api.x.ai/v1/chat/completions", "grok-4.5", "grip-xai"),
    # Qwen4-Max (GA 2026-11-16) on Alibaba's Token Plan, the flat-fee subscription
    # HAL's qwen-token-plan provider uses, so a sitting adds no per-token spend.
    # Seatable by name, not on the default panel. GRIP#9181: the Opus-advice
    # study wanted a Qwen speaker and the council had none to seat.
    "qwen":       ("https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                   "qwen4-max", "grip-qwen-token-plan"),
}
# GEMINI REMOVED FROM THE PANEL, 2026-08-19 — it shared a vendor with the judge.
#
# The judge is gemini-3.1-pro and a speaker was gemini-3.5-flash-lite (formerly
# gemini-2.5-flash, repointed 2026-11-24, GRIP#9389), so Google both
# argued and adjudicated. That is the producer-equals-verifier shape
# exogenous-anchor-law.md exists to forbid, and AGORA's own roster already
# refuses it in as many words: "Gemini is deliberately EXCLUDED from the speaker
# ladder: a Google speaker under a Google judge violates producer != verifier".
# The council had the defect AGORA had already fixed - and it is the council
# GRIP uses to adjudicate its own doctrine, including, on the day this was
# found, the doctrine about what counts as exogenous.
#
# No favouritism was observed: across two runs on 2026-08-19 the judge chose xai
# both times. The structure is the objection, not an observed outcome.
#
# openai takes the vacated seat, which keeps FOUR-provider diversity (deepseek /
# xai / openai / groq, all distinct vendors and none of them Google). Its
# "quota-dead, every call HTTP 429" note from 2026-07-17 was RE-PROBED on
# 2026-08-19 and is stale: both gpt-4o-mini and gpt-5.6-sol return 200 with a
# correct answer. V>> funded the account on 2026-08-18.
#
# deepseek-reasoner is a non-default alias of `deepseek` (both v4-pro; see
# above). Reasoning legs (deepseek / xai / openai) carry a 90s read timeout
# below — they reason on every call, so the 45s default is too tight.
# Astra keeps its default chair; both OpenAI seats use the ChatGPT subscription.
DEFAULT_SPEAKERS = ["deepseek", "xai", "astra", "groq"]
DEFAULT_JUDGE = "gemini-pro"

# SUBSCRIPTION-AUTHENTICATED SEATS (V>> 2026-09-24: "We need to use
# the operator's own Codex ChatGPT subscription (business), not API
# keys, actually. … use Fable as a temporary replacement seat"). Both are
# local-CLI spawns billed to a subscription login, never an API key, and both
# are EXPLICIT OPT-INS via --speakers — the default roster above is
# deliberately unchanged, so the council's constitution does not widen.
#
# - "codex": OpenAI via the Codex CLI (`codex exec`), read-only sandbox, in a
#   neutral cwd, final answer via -o. The seat PINS the built-in provider and
#   the model, because "the login's default" turned out to be poisoned:
#   ~/.codex/config.toml carries a GRIP-MANAGED block (the gg++ codex harness
#   wiring) pinning model=deepseek-v4-pro via the hal-gateway provider, and
#   `codex exec` inherited it and exited 1 with the gateway down (measured
#   2026-09-24). `-c model_provider=openai` selects OpenAI, and every -c sits
#   AFTER `exec` on purpose: once the subcommand carries a -c of its own, Codex
#   drops root-level ones, the default hal-gateway provider takes over, and
#   HAL sends a gpt- model to the metered API key (measured 2026-10-05).
#   `-c forced_login_method=chatgpt`, with OPENAI_API_KEY and CODEX_API_KEY
#   both stripped, keeps the login on the subscription rather than a key. It
#   does not keep a full window off workspace credits: Codex still tries the
#   credit pool ("Your workspace is out of credits", limit_id "premium"), and
#   only a zero balance stops a charge
#   (rules/gpt-on-the-subscription-never-credits.md).
#   The model is gpt-6-astra at max reasoning effort (V>> 2026-09-24: "It
#   should be GPT 6 Astra Max effort"), overridable via $GRIP_CODEX_MODEL, on
#   the operator's ChatGPT team login.
# - "fable": Anthropic Claude Fable 5 via `claude -p`, tools allowlisted to
#   nothing. strip_env is LOAD-BEARING: with ANTHROPIC_API_KEY inherited the
#   CLI prefers the key over the claude.ai login and died on "Credit balance
#   is too low" (measured 2026-09-24); with it stripped, the same call
#   answered on the subscription. An Anthropic seat weakens speaker
#   exogeneity, which is why it is operator-directed, temporary, counted in
#   ``anthropic_calls``, and refused as judge (rules/exogenous-anchor-law.md
#   — the verifier is never the author's family).
SUBPROCESS_SPEAKERS = {
    "codex": {
        "argv": ["codex", "exec", "--sandbox", "read-only",
                 "--skip-git-repo-check", "--cd", "/tmp",
                 "-c", "model_provider=openai",
                 "-c", "forced_login_method=chatgpt",
                 "-c", "model_reasoning_effort=max"],
        "model": "gpt-6-astra",
        "family": "openai",
        "answer_via_file": True,
        "model_env": "GRIP_CODEX_MODEL",
        "pin_model": True,
        "strip_env": ("OPENAI_API_KEY", "CODEX_API_KEY"),
    },
    # Text-only: an empty --allowedTools only pre-approves tools, and a seat
    # launched with it ran Bash on 2026-11-11 (lib/claude_text_only.py).
    "fable": {
        "argv": ["claude", "-p", "--model", "claude-fable-5", *TEXT_ONLY_FLAGS],
        "model": "claude-fable-5",
        "family": "anthropic",
        "strip_env": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    },
}
for _seat, _model in (("openai", "gpt-5.6-sol"), ("astra", "gpt-6-astra")):
    SUBPROCESS_SPEAKERS[_seat] = {**SUBPROCESS_SPEAKERS["codex"],
                                 "model": _model, "model_env": None}
ANTHROPIC_SPEAKERS = frozenset(
    n for n, s in SUBPROCESS_SPEAKERS.items() if s["family"] == "anthropic")

#: The account a subscription seat bills: one login per vendor family. Named so
#: it can never collide with a keychain service (those are ``grip-<vendor>``).


def account_of(seat: str) -> str:
    """The billing account behind a seat: an HTTP seat's keychain service, or a
    subscription seat's login. This is the axis the diversity and
    producer-is-not-verifier checks compare on, for both kinds of seat."""
    if seat in SUBPROCESS_SPEAKERS:
        return "subscription-" + SUBPROCESS_SPEAKERS[seat]["family"]
    return REGISTRY[seat][2]


def model_of(seat: str) -> str:
    """The model id a seat sends, for either kind of seat."""
    if seat in SUBPROCESS_SPEAKERS:
        return SUBPROCESS_SPEAKERS[seat]["model"]
    return REGISTRY[seat][1]
# STATE, not CODE -- and that distinction IS the bug this line used to carry.
#
# `_ROOT` above is `__file__`-relative, correctly: it resolves CODE, putting THIS
# tree's lib/ on sys.path, which is what you want when running from a worktree.
# State must go the other way. Resolved from `__file__`, a council run inside a
# git worktree wrote its verdict record into THAT worktree, where routine
# housekeeping deletes it -- while the IDR leaf it sealed went to the shared
# chain via CLAUDE_DIR (lib/precog/idr.py:26) and survived. One event, two
# stores, two resolution rules, nothing reconciling them.
#
# Measured 2026-09-14 (GRIP#5668): 450 sealed verdicts in the chain against 311
# readable records, 55 orphans still in live worktrees. The audit found ZERO
# canonical records with a missing leaf, so the defect caused ABSENCE, never
# corruption -- the store was incomplete but sound, and this keeps it sound.
STATE_DIR = get_claude_dir() / "state" / "sovereign-council"
UA = "grip-sovereign/1.0"  # explicit UA: some provider CDNs (Groq) 403 a UA-less request

# Per-speaker read timeout (seconds). Default 45; reasoning speakers get more so a
# live-but-deliberating model isn't cut off — grok-4.5 exceeded 45s on a heavy prompt
# (observed 45.08s, 2026-07-26), and the deepseek v4-pro / -reasoner legs reason on
# every call. Fast speakers (groq/gemini) keep 45s so a DEAD provider still fails fast.
_DEFAULT_READ_TIMEOUT_S = 45


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive integer override from the environment, else ``default``.

    A missing variable is the normal case. A value that is not an integer, or
    is zero or negative, is logged and the default stands, so a typo in a
    launch environment can never cut a seat to a zero budget or a zero timeout.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s: %r, using default %d", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Invalid %s: %d, using default %d", name, value, default)
        return default
    return value


# Astra's seat is a Codex subprocess; GRIP_COUNCIL_ASTRA_TIMEOUT_S (seconds)
# raises or lowers its read timeout for a long prompt without a code change.

_READ_TIMEOUT_S = {"xai": 90, "deepseek": 90, "deepseek-reasoner": 90, "openai": 300,
                   # The budget AGORA's seat trial set for Qwen4-Max (GRIP#9510).
                   "qwen": 150,
                   "astra": _env_positive_int("GRIP_COUNCIL_ASTRA_TIMEOUT_S", 300),
                   # CLI spawns pay process start-up + agent overhead on top
                   # of inference; a 90s ceiling cuts a healthy seat mid-answer.
                   "fable": 240, "codex": 300}


def _seat_env_key(name: str) -> str:
    """The environment suffix for one seat: upper case, '-' as '_'."""
    return name.upper().replace("-", "_")


def read_timeout_for(name: str) -> int:
    """The read timeout one seat gets, in seconds.

    ``GRIP_COUNCIL_READ_TIMEOUT_S_<SEAT>`` overrides the table for one seat and
    one run, the way ``GRIP_COUNCIL_ASTRA_TIMEOUT_S`` already does for Astra. A
    sitting over a 107K-character Internet-Draft cut xai off at 91.6 s against
    its 90 s ceiling (2026-10-17), so one complete speaker answered of four. The
    table and the 45 s default stand when the variable is unset, and a value
    that is not a positive integer is logged and ignored, as for every other
    council integer.
    """
    return _env_positive_int(f"GRIP_COUNCIL_READ_TIMEOUT_S_{_seat_env_key(name)}",
                             _READ_TIMEOUT_S.get(name, _DEFAULT_READ_TIMEOUT_S))


def http_model_for(name: str) -> str:
    """The model id an HTTP seat sends.

    ``GRIP_COUNCIL_MODEL_<SEAT>`` replaces the registry's model for one run at the
    SAME endpoint and key: a groq seat stays at api.groq.com and a deepseek seat
    at api.deepseek.com, so a rate-limited or format-blind model can be swapped
    for a sibling without a code change, and the destination the OpenAI billing
    guard judges never moves. Unset or blank leaves the registry's model.
    """
    override = os.environ.get(f"GRIP_COUNCIL_MODEL_{_seat_env_key(name)}", "").strip()
    return override or REGISTRY[name][1]

# Per-process key cache (AGORA #3995 DEFER, task #430). The lock guards the
# cache — the one genuinely shared mutable structure — and serialises first-time
# resolution, so concurrent speaker threads never scan /proc simultaneously and
# every speaker in this process sees ONE consistent key per service. A bare lock
# around the read alone would change nothing (each read is already independent;
# serialising our readers cannot stop the target process rotating keys between
# reads) — the memoisation is what buys consistency. Successes only; a failed
# resolution raises and is never cached. Rotating a key under a long-lived
# endpoint requires a process restart (ADR 0012, Concurrency model).
_KEY_CACHE: "dict[str, str]" = {}
_KEY_CACHE_LOCK = threading.Lock()


def provider_key(service: str) -> str:
    """Resolve a provider API key via the env -> Keychain -> /proc ladder.

    ``allow_proc_scan``: on Linux (the VPS) a provider key lives in the running
    hal-server process env, not this short-lived process — the /proc rung reads
    it there so a headless council resolves keys without a local .env. No-op on
    macOS (no /proc), where the env vars + Keychain rungs already serve.

    Memoised per process under a lock: one resolution per service, shared by
    every concurrent speaker thread (see _KEY_CACHE above).
    """
    with _KEY_CACHE_LOCK:
        key = _KEY_CACHE.get(service)
        if key is None:
            key = resolve_provider_key_or_raise(service, allow_proc_scan=True)
            _KEY_CACHE[service] = key
        return key


def _grip_root() -> pathlib.Path:
    """GRIP repository root (parent of this lib/ dir)."""
    return pathlib.Path(__file__).resolve().parent.parent


def _seal_idr(payload: dict, sha: str, *, _idr_path: "pathlib.Path | None" = None) -> "str | None":
    """Append a signed IDR leaf for this verdict. Returns the leaf id, or None if lib unavailable."""
    try:
        from lib.precog.idr import build_idr, append_idr
        idr = build_idr(
            prompt=payload["prompt"],
            fingerprint="sovereign-council",
            decision={"judge": payload["judge"], "sha256": sha},
            predecessor_idr=None,
            depth=0,
            kind="sovereign-council-verdict",
            inputs={"speakers": payload["speakers"]},
        )
        append_idr(idr, path=_idr_path)
        return idr.id
    except Exception as exc:  # noqa: BLE001 — IDR is OPTIONAL provenance over the
        # MANDATORY sha256; a dead IDR path must degrade the record, never crash the
        # council. Logged at WARNING (not a silent print) so a VPS operator sees a
        # seal-transport failure in pm2 logs (AGORA #3995 R0).
        logger.warning("IDR signing degraded (%s: %s); sha256-only provenance",
                       type(exc).__name__, exc)
        return None


def _content_tamper_line(idr, sha: str, idr_id: str) -> "str | None":
    """Hold the record's hash against the one the SIGNED leaf committed to.

    Returns a status line when they disagree, else ``None``.

    Without this the verification is theatre against the one attacker tamper-evidence
    exists to stop: someone who can WRITE the record file. The sha256 leg is
    self-consistent by construction -- ``recompute()`` hashes the record's own
    {prompt, speakers, judge}, so anyone who edits the content and rehashes it produces
    a record that agrees with itself. The leaf's HMAC covers the LEAF, not the record,
    so it stays VERIFIED while the record says something else entirely. Both legs
    passed and the verdict printed PROVEN.

    Measured 2026-09-17 against the real record ``7dc01f0b739be243``: rewriting the
    judge's synthesis to the OPPOSITE verdict and recomputing sha256 yielded
    ``PROVEN ✓ (sha256 + IDR HMAC — dual tamper-evident provenance)``. The leaf had
    committed to the true hash the whole time; the forged record carried another;
    nothing compared them. The anchor built at the call site is derived FROM the
    record's own sha, so it cannot disagree with it either.

    The signed leaf is the only value here an attacker cannot forge without the
    ed25519 private key, so it -- never the record's agreement with itself -- is what
    the record must be held against. Found by running the falsification experiment a
    non-Anthropic council named for this exact claim (seal ``7dc01f0b739be243``).
    """
    sealed = idr.decision.get("sha256", "") if isinstance(idr.decision, dict) else ""
    if not sealed:
        # A pre-cross-check leaf sealed no hash; the HMAC leg still applies. Silent
        # degradation is correct ONLY because there is nothing to compare against.
        return None
    if not sha:
        return (f"IDR leaf  : {idr_id} — CONTENT TAMPERED ✗ (record carries no sha256; "
                f"leaf sealed {sealed[:16]})")
    if sealed != sha:
        return (f"IDR leaf  : {idr_id} — CONTENT TAMPERED ✗ (leaf sealed {sealed[:16]}, "
                f"record carries {sha[:16]})")
    return None


#: The dual-custody scheme tag. A record carrying it is signed Ed25519 +
#: ML-DSA-65, and is the only kind an outsider can check with published keys.
_DUAL_SCHEME = "ed25519+ml-dsa-65"


def load_verify_keys(path: "str | None") -> dict:
    """Load the ``{key_fingerprint: public_key_hex}`` map a reader verifies with.

    Same data shape GRASP uses for ``GRASP_VERIFY_KEYS`` (``grasp/keys.py``
    ``_verify_keys_map``), so an operator publishes ONE map and either tool
    consumes it. Resolution: an explicit ``--pubkeys`` file wins, else the
    ``GRIP_COUNCIL_VERIFY_KEYS`` environment variable, which holds the JSON
    inline or, as a convenience, the path of a file containing it.

    GRASP's loader returns ``{}`` on malformed input. This one RAISES, and the
    difference is deliberate: an empty map renders here as "no keys supplied",
    which is indistinguishable from never having tried — so a reader who
    mistypes their key file would be told the RECORD is unverifiable rather
    than that their FILE is broken. Every value is hex-decoded at load time for
    the same reason: a bad key must fail where the reader can fix it, not later
    as an absent fingerprint.

    Returns ``{}`` only when no source was supplied at all.
    """
    import os
    if path:
        return _parse_verify_keys(pathlib.Path(path).read_text(encoding="utf-8"), path)
    raw = os.environ.get("GRIP_COUNCIL_VERIFY_KEYS", "").strip()
    if not raw:
        return {}
    candidate = pathlib.Path(raw)
    if not raw.startswith("{") and candidate.is_file():
        return _parse_verify_keys(candidate.read_text(encoding="utf-8"), raw)
    return _parse_verify_keys(raw, "$GRIP_COUNCIL_VERIFY_KEYS")


def _parse_verify_keys(blob: str, source: str) -> dict:
    """Parse + validate one key map, naming *source* in every error."""
    try:
        parsed = json.loads(blob)
    except ValueError as exc:
        raise ValueError(f"verification keys at {source} are not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"verification keys at {source} must be a JSON object mapping "
            f"key fingerprint -> public key hex, got {type(parsed).__name__}")
    for fingerprint, hexed in parsed.items():
        if not isinstance(hexed, str):
            raise ValueError(
                f"verification key {fingerprint!r} at {source} must be a hex string")
        try:
            bytes.fromhex(hexed)
        except ValueError as exc:
            raise ValueError(
                f"verification key {fingerprint!r} at {source} is not hex: {exc}") from exc
    return dict(parsed)


def resolve_record_path(arg: str,
                        state_dir: "pathlib.Path | None" = None) -> pathlib.Path:
    """Resolve a ``--verify`` / ``--show`` argument to the record file it names.

    A sealed council record that ships with a document has to be verifiable
    WHERE IT LIES. Resolving every argument under ``STATE_DIR`` meant a reader
    first had to copy the file into a directory that exists only on a GRIP
    install — and because the id branch truncates to 16 characters, a path
    handed in anyway became a mangled lookup such as
    ``<STATE_DIR>/private/var/fol.json`` instead of an error naming the file.

    An argument is a PATH when it exists on disk, contains a separator, or ends
    in ``.json``; otherwise it is a content-address prefix resolved under
    ``STATE_DIR`` exactly as before. A short sha is hex, so it satisfies none of
    the three. A path that looks like a path and is missing is returned as
    itself, so the caller reports the file the reader actually named.

    ``state_dir`` is taken from the CALLER rather than read off this module,
    because the CLI binds its own ``STATE_DIR`` name at import and that is the
    one its tests substitute. Reading the module global here would silently
    ignore the caller's store.
    """
    import os
    candidate = pathlib.Path(arg).expanduser()
    if candidate.is_file() or os.sep in arg or arg.endswith(".json"):
        return candidate
    return (state_dir if state_dir is not None else STATE_DIR) / f"{arg[:16]}.json"


def _custody_fragment(audit: object) -> str:
    """``custody=… scheme=…`` — and, for the forgeable seal, why it matters.

    The whole of GRIP#6419: custody lives on the record and was never printed,
    so the strongest seal GRIP produces and the one anybody holding a shared
    key can forge rendered identically. ``audit_custody_tier`` is reused rather
    than re-derived, so a pre-marker record is classified by the same rule here
    as everywhere else.
    """
    from lib.precog.idr import CUSTODY_DEMO, audit_custody_tier
    tier = audit_custody_tier(audit)
    scheme = audit.get("scheme", "?") if isinstance(audit, dict) else "?"
    if tier == CUSTODY_DEMO:
        tier = f"{CUSTODY_DEMO} — FORGEABLE: shared-key seal, not attributable"
    return f"custody={tier}  scheme={scheme}"


def _key_bytes(keys: dict, fingerprint: "str | None") -> "bytes | None":
    """The raw public key published under *fingerprint*, or None if absent.

    ``load_verify_keys`` has already proved every value decodes, so ``None``
    here means genuinely not supplied — never quietly unusable.
    """
    hexed = keys.get(fingerprint) if fingerprint else None
    return bytes.fromhex(hexed) if isinstance(hexed, str) else None


def _pq_backend_ready() -> bool:
    """True iff this interpreter can perform ML-DSA-65 work. Never raises."""
    try:
        from lib.aide.key_provisioning import pq_backend_available
        return bool(pq_backend_available())
    except Exception:  # noqa: BLE001 — a verifier degrades, it never crashes
        return False


def _external_leaf_check(idr, keys: dict) -> "tuple[object, str, bool] | None":
    """Check the leaf against the READER's own keys: ``(verdict, what, full)``.

    ``None`` when external keys cannot speak to this record at all — an
    HMAC-sealed leaf needs a shared secret an outsider must never be given, so
    it falls back to the in-house check rather than reading as unverifiable.

    ``full`` is False when only the Ed25519 limb could be checked, and the
    caller MUST NOT render that as dual custody: the dual seal is an AND.
    """
    from lib.audit_chain import Verdict
    from lib.idr_forest import signed_entry_hash
    audit = idr.audit if isinstance(idr.audit, dict) else {}
    if audit.get("scheme") != _DUAL_SCHEME:
        return None
    ed_fp = (audit.get("ed25519") or {}).get("key_fingerprint")
    mldsa_fp = (audit.get("mldsa65") or {}).get("key_fingerprint")
    ed_raw = _key_bytes(keys, ed_fp)
    if ed_raw is None:
        return Verdict.DEGRADED, (
            f"the supplied keys do not include the Ed25519 fingerprint {ed_fp} "
            f"this record declares"), False
    message = signed_entry_hash(idr).encode()
    mldsa_raw = _key_bytes(keys, mldsa_fp)
    if mldsa_raw is not None and _pq_backend_ready():
        from lib.pq_dual_sign import dual_verify_external
        return dual_verify_external(message, audit, ed_raw, mldsa_raw), (
            "signatures: Ed25519 + ML-DSA-65, both checked against the "
            "supplied public keys"), True
    from lib.pq_dual_sign import ed25519_limb_verify_external
    reason = ("this build of cryptography carries no ML-DSA-65 (needs >=48)"
              if mldsa_raw is not None else
              f"the supplied keys do not include the ML-DSA-65 fingerprint {mldsa_fp}")
    return ed25519_limb_verify_external(message, audit, ed_raw), (
        "signature: Ed25519 only, checked against the supplied public key; "
        f"ML-DSA-65 NOT CHECKED ({reason})"), False


def _in_house_what(audit: object) -> str:
    """What the IN-HOUSE check established — including whom it asks you to trust.

    Worth saying out loud: the in-house dual check resolves its verifying keys
    from GRIP's own ``state/audit/keys/``, which is exactly the material an
    outsider cannot accept, because whoever can forge the record can swap the
    key beside it. A clean in-house result is GRIP checking itself.
    """
    scheme = audit.get("scheme") if isinstance(audit, dict) else None
    if scheme == _DUAL_SCHEME:
        return ("signatures: Ed25519 + ML-DSA-65, checked against GRIP's own "
                "pinned keys — not an outsider's check")
    return "seal: HMAC-SHA256 under the key every GRIP install shares"


def _degraded_reason(audit: object, external_what: "str | None") -> str:
    """Why nothing could be checked — naming the one thing that would fix it."""
    if external_what:
        return f"DEGRADED: {external_what}"
    if isinstance(audit, dict) and audit.get("scheme") == _DUAL_SCHEME:
        return ("DEGRADED: no verification keys supplied (--pubkeys); "
                "signatures present but unchecked")
    return "DEGRADED: this signature scheme cannot be checked here"


def _leaf_outcome(idr, in_house, keys: dict) -> "tuple[str, str, bool | None]":
    """``(state, what_was_checked, ok)`` for one leaf — the reader's keys first.

    Supplied keys OUTRANK the in-house result when they apply, because they are
    strictly stronger evidence: they do not ask the reader to trust GRIP's own
    key directory.
    """
    from lib.audit_chain import Verdict
    external = _external_leaf_check(idr, keys) if keys else None
    verdict, what, full = external or (in_house, _in_house_what(idr.audit), True)
    if verdict is Verdict.VERIFIED and full:
        return "VERIFIED ✓", what, True
    if verdict is Verdict.VERIFIED:
        return "ED25519-ONLY ✓ — dual custody NOT fully checked", what, None
    if verdict is Verdict.BROKEN:
        return "BROKEN ✗", what, False
    return _degraded_reason(idr.audit, external and what), "", None


def _leaf_line(idr_id: str, state: str, what: str, audit: object) -> str:
    """The one verdict line: state, custody, scheme, and what was actually checked."""
    tail = f"  ({what})" if what else ""
    return f"IDR leaf  : {idr_id} — {state}  {_custody_fragment(audit)}{tail}"


def _no_leaf_result(idr_id: str, _idr_path: "pathlib.Path | None") -> tuple:
    """``(line, ok)`` when the referenced leaf is not in the chain being read.

    Two situations that look identical from inside the lookup and are opposite
    in meaning, so they are separated by asking whether a chain is there at all:

    * **A chain exists and does not contain this leaf** — the id was injected.
      A tamper signal, unchanged.
    * **There is no chain here** — the reader was simply never given it. The
      signed leaf lives in the operator's ``state/precog/idr.jsonl`` and does
      NOT travel with a council record, so this is the NORMAL state for the
      stranger GRIP#6430 is written for.

    Measured 2026-09-22 on the real shipped seal ``09defe4df1413685.json``
    under a fresh ``CLAUDE_DIR``: the second case reported
    ``TAMPERED ✗ (sha256 ok but IDR signature BROKEN)`` on a sound record.
    That is a false accusation against the operator's own evidence, and worse
    than silence — it would discredit a genuine record the first time an
    outsider ran the command the document advertises.
    """
    if _chain_exists(_idr_path):
        return f"IDR leaf  : {idr_id} — NOT FOUND in chain", False
    return (f"IDR leaf  : {idr_id} — DEGRADED: the signed leaf did not travel with "
            f"this record and no chain here holds it (point --idr at the JSONL "
            f"carrying it to check the signature)"), None


def _chain_exists(_idr_path: "pathlib.Path | None") -> bool:
    """Is there an IDR chain on this machine to have looked in? Never raises."""
    try:
        from lib.precog.idr import _default_idr_path
        return (_idr_path or _default_idr_path()).exists()
    except Exception:  # noqa: BLE001 — unknown degrades to "no chain", the safer read
        return False


def _locate_leaf(record: dict, _idr_path: "pathlib.Path | None"):
    """``(early_result, idr)`` — exactly one is non-None.

    Everything that settles the leaf line WITHOUT a signature check: an absent
    or malformed id, an unimportable IDR lib, a leaf missing from the chain, or
    a record whose content disagrees with the hash the signed leaf committed to.
    """
    idr_id = record.get("idr_id")
    if idr_id is None:
        return ("IDR leaf  : absent — sha256-only provenance (pre-IDR record)", None), None
    if idr_id == "":
        # seal() writes idr_id ONLY on a truthy id, so an empty string is an
        # injected/malformed leaf — a tamper signal, not a graceful degradation
        # (AGORA #3995 R0). idr_id is not in the sha256 payload, so flag it here.
        return ("IDR leaf  : malformed — empty idr_id (tamper signal)", False), None
    root = str(_grip_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from lib.precog.idr import read_idr_chain
    except ImportError as exc:
        return (f"IDR leaf  : {idr_id} — lib unavailable ({exc}); "
                f"sha256-only provenance", None), None
    idr = {leaf.id: leaf for leaf in read_idr_chain(path=_idr_path)}.get(idr_id)
    if idr is None:
        return _no_leaf_result(idr_id, _idr_path), None
    mismatch = _content_tamper_line(idr, record.get("sha256", ""), idr_id)
    if mismatch is not None:
        return (mismatch, False), None
    return None, idr


def _in_house_verdict(idr, sha: str):
    """The forest's own verdict for one leaf, or an early ``(line, ok)`` on error."""
    try:
        from lib.idr_forest import (IdrForestError, add_idr, empty_forest,
                                    verify_chain_integrity)
    except ImportError as exc:
        return (f"IDR leaf  : {idr.id} — lib unavailable ({exc}); "
                f"sha256-only provenance", None)
    try:
        anchor = f"council:{sha[:16]}"
        return verify_chain_integrity(
            add_idr(empty_forest((anchor,)), idr, anchor_id=anchor))
    except IdrForestError as exc:
        return (f"IDR leaf  : {idr.id} — forest error: {exc}", False)


def _verify_idr(record: dict, *, _idr_path: "pathlib.Path | None" = None,
                pubkeys: "dict | None" = None) -> "tuple[str, bool | None]":
    """Verify the IDR leaf referenced in *record*.

    ``pubkeys`` is the reader's own ``{fingerprint: public key hex}`` map (see
    :func:`load_verify_keys`). When supplied and the leaf is dual-signed, the
    signature is checked against THOSE keys and GRIP's key directory is never
    consulted — the zero-trust check an outside auditor needs.

    Returns (status_line, True | False | None):
      True  — the leaf's signature verified in full
      False — tampered (BROKEN) or leaf not found in chain (tamper signal)
      None  — could not be checked: IDR absent, lib unavailable, no keys
              supplied, or only the classical limb of a dual seal was
              checkable. The line says which; "I could not check this" is
              never a quiet pass.
    """
    early, idr = _locate_leaf(record, _idr_path)
    if early is not None:
        return early
    verdict = _in_house_verdict(idr, record.get("sha256", ""))
    if isinstance(verdict, tuple):
        return verdict
    state, what, ok = _leaf_outcome(idr, verdict, pubkeys or {})
    return _leaf_line(idr.id, state, what, idr.audit), ok


#: Speaker budget. Was 1000, which was too small for a deliberative council: on
#: 2026-08-10 deepseek-v4-pro spent its entire budget reasoning and never reached
#: a verdict, costing 25% of the panel, and nothing in the sealed record said so.
#: GRIP_COUNCIL_SPEAKER_MAX_TOKENS raises or lowers it per launch, for a
#: council whose prompt needs more room than the default leaves.
_SPEAKER_MAX_TOKENS = _env_positive_int("GRIP_COUNCIL_SPEAKER_MAX_TOKENS", 3000)

#: finish_reason values meaning "ran out of budget", i.e. the answer stops
#: mid-thought and is NOT a stated position. Unknown values are treated as
#: complete, so a new provider spelling degrades to today's behaviour (a missed
#: warning) rather than poisoning a healthy council with false truncation flags.
_TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "model_length", "MAX_TOKENS"})

#: The judge's budget. Was 1200, and that is not enough for a judge that thinks
#: before it answers: a thinking model spends reasoning tokens out of the SAME
#: budget as its visible reply, so it runs out mid-sentence and emits a partial
#: answer rather than a short one.
#:
#: Observed on 2026-08-19, council `e2ff8e54c526871a`, judge
#: gemini-3.1-pro-preview: the sealed verdict reads
#: `{"best": "?", "synthesis": "json\\n{\\"best\\": \\"xai / grok-4.5\\", ...`
#: and stops mid-sentence at 206 characters. The judge had picked a winner --
#: the name is legible in the fragment -- but its reply was cut off before the
#: closing brace, so the JSON did not parse and `best` fell back to `?`. The
#: council's own decision was sealed in a form that reads like a failure.
#:
#: 8192 matches the AGORA judge (`_JUDGE_MAX_TOKENS` in lib/agora_pr_review.py),
#: which was measured against this same model family for this same reason. The
#: judge's VISIBLE output here is one sentence of JSON; the budget is for what
#: it thinks on the way there, and it is a cap, not a spend.
_JUDGE_MAX_TOKENS = 8192

#: Cap on the judge's RAW reply when it did not parse as JSON. Was 300, which
#: discarded the judge's reasoning at exactly the moment a human most needed to
#: read it — the parse had already failed, so those characters were the only
#: evidence of what went wrong. This bound stays finite against a pathological
#: response while never cutting real prose.
_JUDGE_FALLBACK_MAX_CHARS = 4000

#: What `best` says when the judge's reply did not parse. Was the bare "?",
#: which is indistinguishable from a judge that genuinely could not choose --
#: and in the truncation above, the judge HAD chosen. Naming the failure means
#: a later reader of the seal knows to look at `synthesis` for the fragment
#: rather than concluding the council reached no view.
_JUDGE_UNPARSED = "(judge reply did not parse)"


#: The OpenAI-compatible body every provider in the registry accepts -- except
#: OpenAI's own newer models, which reject two of its fields outright. Measured
#: 2026-08-19 against api.openai.com with a funded key:
#:
#:   gpt-5.6-sol  max_tokens=640            -> 400 "Unsupported parameter:
#:                                             'max_tokens' is not supported with
#:                                             this model. Use
#:                                             'max_completion_tokens' instead."
#:   gpt-5.6-sol  temperature=0.4           -> 400 "Unsupported value:
#:                                             'temperature' does not support 0.4
#:                                             with this model. Only the default
#:                                             (1) value is supported."
#:   gpt-5.6-sol  max_completion_tokens,
#:                default temperature       -> 200, finish_reason=stop, "42"
#:   gpt-4o-mini  max_tokens, temperature   -> 200, finish_reason=stop, "42"
#:   groq         max_tokens, temperature   -> 200, finish_reason=stop, "42"
#:
#: Both spellings therefore coexist across the roster and neither is safe to
#: assume. Remedies are keyed on the provider's OWN error text rather than on a
#: list of which models moved, because such a list rots silently and this is a
#: file full of comments about lists that rotted. Each remedy fires only when the
#: provider has explicitly named the offending parameter.
# (pattern the error must match, field to remove, replacement field or "")
#
# The pattern must identify the field the provider REJECTED, never the one it
# suggested. Keying on the suggestion looked equivalent and was not: a 400
# reading "Parameter 'max_completion_tokens' is not supported by this model"
# contains the marker `max_completion_tokens`, so the old form popped max_tokens
# and inserted the very parameter that had just been refused, then retried it.
# Reproduced before fixing. That is the "blind retry turns one real failure into
# two" this module's docstring forbids, arriving through the mechanism written
# to prevent it.
_BODY_REMEDIES: "tuple[tuple[str, str, str], ...]" = (
    (r"[Uu]nsupported parameter:\s*'?max_tokens'?", "max_tokens", "max_completion_tokens"),
    (r"'temperature'\s+does not support", "temperature", ""),
)


def _shield(value, to=None, env=None):
    """``value`` scrubbed by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send: callers treat OSError as a failed send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(value, to=to, env=env)



def _post_chat(url: str, model: str, service: str, messages: list,
               max_tokens: int, *, timeout: int,
               reasoning_effort: "str | None" = None) -> dict:
    """POST one chat completion, adapting the body when the provider says to.

    Starts from the body every other provider accepts and applies a remedy only
    when a 400 names the parameter it objects to. Any other status, and any 400
    naming something we have no remedy for, propagates untouched -- a blind
    retry turns one real failure into two. The messages pass the redaction
    shield first, so a council that never scrubbed its prompt is still covered,
    and the billing guard refuses a per-token OpenAI seat before any attempt.
    """
    body = {"model": model, "messages": messages,
            "max_tokens": max_tokens, "temperature": 0.4}
    body = _shield(body, to=url)
    # Sent ONLY for the legs that need it, so every other provider's body is
    # byte-unchanged and no leg is handed a parameter it might reject.
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort

    def _send() -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {provider_key(service)}",
            "Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)

    # Fields the provider has named unsupported, accumulated ACROSS attempts.
    # Without this the loop could exhaust its iterations and then fire an
    # unconditional final send carrying a field rejected on an earlier attempt:
    # reject max_tokens (swap to max_completion_tokens), then reject BOTH
    # max_completion_tokens and temperature - the second pass only removed
    # temperature, because the replacement-guard is unreachable once max_tokens
    # is already gone, and the trailing send went out with the rejected
    # replacement still in it. Reproduced: three sends, the last one carrying it.
    rejected: set = set()
    for _ in range(len(_BODY_REMEDIES) + 1):
        if rejected & set(body):
            raise _rebuilt(last_exc, last_detail)  # would resend a refused field
        try:
            return _send()
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            last_exc, last_detail = exc, exc.read().decode(errors="replace")
            rejected |= _fields_named_unsupported(last_detail)
            if not _apply_remedy(body, last_detail):
                # Re-raise with the body restored, so the caller sees the real
                # message rather than an exhausted, already-read response.
                raise _rebuilt(last_exc, last_detail)
    raise _rebuilt(last_exc, last_detail)


def _rebuilt(exc, detail: str) -> urllib.error.HTTPError:
    """The provider's error with a readable body — the original is consumed."""
    return urllib.error.HTTPError(
        exc.url, exc.code, exc.reason, exc.headers, io.BytesIO(detail.encode()))


def _fields_named_unsupported(detail: str) -> set:
    """Every parameter the provider explicitly called unsupported."""
    return set(re.findall(r"[Uu]nsupported parameter:\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", detail))


def _apply_remedy(body: dict, detail: str) -> bool:
    """Rewrite *body* in place per the provider's complaint. True if changed.

    Matches on a pattern naming the REJECTED field, and refuses to introduce a
    replacement the same error has already complained about - so a provider that
    rejects both spellings gets one honest failure rather than a retry loop
    between them.
    """
    for pattern, field, replacement in _BODY_REMEDIES:
        if field not in body or not re.search(pattern, detail):
            continue
        if replacement and re.search(rf"[Uu]nsupported parameter:\s*'?{re.escape(replacement)}'?", detail):
            continue  # the provider rejected the replacement too; do not swap into it
        value = body.pop(field)
        if replacement:
            body[replacement] = value
        return True
    return False


def _spawn_argv_and_model(spec: dict) -> tuple[list, str]:
    """The argv + model label for a subprocess seat, honouring its model_env.

    ``pin_model`` seats always pass ``-m <model>``: codex's config.toml default
    is GRIP-harness-managed and points at the HAL gateway, so leaving the model
    to "the CLI's default" seats a different provider than the subscription."""
    argv, model = list(spec["argv"]), spec["model"]
    override = os.environ.get(spec.get("model_env") or "")
    if override:
        model = override
    if spec.get("pin_model"):
        argv += ["-m", model]
    elif override:
        argv += ["-m", model]
    return argv, model


def _spawn_answer(spec: dict, argv: list, prompt: str, timeout: int) -> tuple[int, str, str]:
    """Run one CLI seat; returns (rc, answer, stderr). Answer via -o file when
    the CLI logs to stdout (codex), else stdout (claude -p). The prompt passes
    the redaction shield before the CLI sees it; a failed scrub raises and
    starts nothing."""
    env = {k: v for k, v in os.environ.items() if k not in spec.get("strip_env", ())}
    if spec.get("family") == "openai" and not env.get("CODEX_HOME"):
        try:
            from openai_billing import subscription_homes
        except ImportError:
            from lib.openai_billing import subscription_homes
        homes = subscription_homes()
        if homes:
            env["CODEX_HOME"] = str(homes[0])
    prompt = _shield(prompt, to=argv, env=env)
    out_path = None
    try:
        if spec.get("answer_via_file"):
            fd, out_path = tempfile.mkstemp(prefix="grip-council-", suffix=".txt")
            os.close(fd)
            argv = argv + ["-o", out_path]
        # UTF-8 named on both paths: text mode otherwise uses the locale, which is
        # cp1252 on Windows and breaks council prose going in and coming back.
        # errors="replace" so one undecodable byte cannot cost a seat its answer.
        proc = subprocess.run(argv, input=prompt, text=True, encoding="utf-8",
                              errors="replace", capture_output=True, env=env,
                              timeout=timeout)
        answer = (pathlib.Path(out_path).read_text(encoding="utf-8", errors="replace")
                  if out_path else proc.stdout)
        return proc.returncode, answer.strip(), (proc.stderr or "").strip()
    finally:
        if out_path:
            pathlib.Path(out_path).unlink(missing_ok=True)


def _subprocess_call(name: str, messages: list, t0: float) -> dict:
    """One subscription-authenticated CLI seat. Same contract as provider_call:
    timing + text, never raises — a dead spawn is a dead speaker, not a crash."""
    spec = SUBPROCESS_SPEAKERS[name]
    argv, model = _spawn_argv_and_model(spec)
    start = time.time() - t0
    prompt = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("content"))
    try:
        if spec.get("family") == "anthropic":
            # A headless Claude seat refuses to start while the tick's throttle flag is set (V>>'s decision of
            # 2026-10-29); the raise below makes it a dead speaker with the flag's line, and the council goes on.
            throttle_flag.refuse_if_set()
        rc, answer, err = _spawn_answer(
            spec, argv, prompt, read_timeout_for(name))
        ok = rc == 0
        if ok:
            text = answer
        else:
            # Surface the TAIL of stderr, not the head: CLI seats (codex) print a
            # version/workdir banner first and the real error LAST ("Your workspace
            # is out of credits"), so err[:90] showed only the banner and hid the
            # cause (2026-10-26 roundtable: codex/openai seats failed and the seal
            # hid the reason).
            lines = [ln for ln in err.splitlines() if ln.strip()]
            text = f"exit {rc}: {lines[-1] if lines else '(no stderr)'}"
        if ok and not text:
            text = "(exit 0 but empty answer)"
        return {"name": name, "model": model, "ok": ok, "text": text,
                "finish_reason": "stop" if ok else "error", "truncated": False,
                "start": start, "end": time.time() - t0}
    except Exception as e:  # noqa: BLE001 — a council survives a dead speaker
        return {"name": name, "model": model, "ok": False,
                "finish_reason": "error", "truncated": False,
                "text": f"{type(e).__name__}: {e}", "start": start, "end": time.time() - t0}


def provider_call(name: str, messages: list, t0: float,
                  max_tokens: int = _SPEAKER_MAX_TOKENS,
                  timeout: float | None = None) -> dict:
    """One non-Anthropic chat call. Returns timing + text; never raises.

    Always reports ``finish_reason`` and a derived ``truncated`` flag. A cut-off
    answer previously looked identical to a complete one in the sealed record,
    so a degraded council was indistinguishable from a healthy one.

    ``timeout`` lets a caller that is not a debate speaker declare its own read
    budget; omitted, the per-speaker table applies. The KERN comprehension
    probe needs it: a reasoning model answering thirty questions about a long
    rule can think for longer than the 45 seconds a fast debate speaker gets.

    Subscription CLI seats (SUBPROCESS_SPEAKERS) dispatch to _subprocess_call,
    which keeps its own table; every HTTP speaker below is non-Anthropic
    exactly as before.
    """
    if name in SUBPROCESS_SPEAKERS:
        return _subprocess_call(name, messages, t0)
    url, _registry_model, service = REGISTRY[name]
    model = http_model_for(name)
    start = time.time() - t0
    try:
        data = _post_chat(url, model, service, messages,
                          max_tokens,
                          timeout=timeout or read_timeout_for(name))
        choices = data.get("choices") or []
        msg = (choices[0].get("message") if choices else {}) or {}
        text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        finish_reason = (choices[0].get("finish_reason") if choices else "no-choices") or ""
        if not text:
            text = f"(200 but empty; finish_reason={finish_reason})"
        return {"name": name, "model": model, "ok": True, "text": text,
                "finish_reason": finish_reason,
                "truncated": finish_reason in _TRUNCATED_FINISH_REASONS,
                "start": start, "end": time.time() - t0}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:90].replace("\n", " ")
        return {"name": name, "model": model, "ok": False,
                "finish_reason": "error", "truncated": False,
                "text": f"HTTP {e.code}: {detail}", "start": start, "end": time.time() - t0}
    except Exception as e:  # noqa: BLE001 — a council survives a dead speaker
        return {"name": name, "model": model, "ok": False,
                "finish_reason": "error", "truncated": False,
                "text": f"{type(e).__name__}: {e}", "start": start, "end": time.time() - t0}


def premise_advisory(prompt: str) -> "str | None":
    """Gate-0 premise-citation check at seed-build time (task #474).

    A council seed that ASSERTS a failure premise ("X is broken / corrupted
    my data") with no reproduce evidence gets a loud advisory — asking N
    speakers about the conclusion launders an unverified premise into an
    exogenous-looking verdict (the 2026-07-14 RTK council confirmed a false
    story exactly this way). Advisory only, never a block: the family-B
    doctrine clause in ``rules/dispatch-economics.md`` Gate 0. Pure and
    fail-open — returns None when the detector is unavailable, errors, or
    the seed is clean.
    """
    if _classify_premise is None:
        return None
    # Key access stays INSIDE the guard: a detector that returns a drifted
    # schema (non-dict, renamed keys) must degrade to None like a raising
    # one — audible, never a council crash (AGORA #4038 confirmed blocker:
    # the first cut read verdict["verdict"] outside the try).
    try:
        verdict = _classify_premise(prompt)
        if verdict["verdict"] != "warn":
            return None
        reason = str(verdict["reason"])
    except Exception as exc:  # noqa: BLE001 — the check must never break a council
        logger.warning("premise gate degraded (schema drift or error): %s", exc)
        return None
    return (
        "[PREMISE-REPRODUCE] council seed asserts an unreproduced failure "
        "premise — run the <30s ground-truth test and cite it IN the seed "
        "(rules/dispatch-economics.md Gate 0): " + reason
    )


def fan_out(prompt: str, speakers: list,
            system_prompts: "dict[str, str] | None" = None) -> tuple[list, float]:
    """Fan the prompt out to every speaker IN PARALLEL. Returns (results, wall_time).

    ``system_prompts`` optionally maps a speaker name to a per-seat system
    instruction, prepended to that speaker's messages — the seam the roundtable
    orchestrator uses to steer each seat individually. Absent/None = current
    behaviour (one user message, no system prompt). Every consumer (CLI, demo,
    endpoint, run_council) passes through here, so the Gate-0 seed advisory
    fires once for all of them — on stderr, never altering the seed or results.
    """
    advisory = premise_advisory(prompt)
    if advisory:
        print(advisory, file=sys.stderr)
    sys_map = system_prompts or {}
    t0 = time.time()

    def _msgs(name: str) -> list:
        sys_prompt = sys_map.get(name)
        if sys_prompt:
            return [{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt}]
        return [{"role": "user", "content": prompt}]

    with ThreadPoolExecutor(max_workers=len(speakers)) as ex:
        results = [f.result() for f in
                   [ex.submit(provider_call, n, _msgs(n), t0) for n in speakers]]
    return results, time.time() - t0


def _strip_code_fence(raw: str) -> str:
    """Unwrap a ```json ... ``` block, including one that was never closed.

    The old form used ``lstrip("json")``, which strips CHARACTERS rather than a
    prefix, and only unwrapped when a CLOSING fence was present. A judge cut off
    mid-reply has an opening fence and no closing one, so its JSON was handed to
    the parser still wearing the literal word ``json`` -- guaranteeing the parse
    failure that then hid the real cause.
    """
    if not raw.startswith("```"):
        return raw
    body = raw[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    return body.split("```", 1)[0].strip()


def _parse_judge_reply(text: str) -> dict:
    """Turn the judge's raw reply into ``{best, synthesis}``.

    On a parse failure the raw reply is kept as the synthesis -- it is the only
    evidence of what the judge was saying -- and ``best`` names the failure
    rather than shrugging with "?", which reads like a judge that could not
    choose even when it plainly had.
    """
    raw = _strip_code_fence(text.strip())
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"best": _JUDGE_UNPARSED, "synthesis": raw[:_JUDGE_FALLBACK_MAX_CHARS]}
    if not isinstance(parsed, dict):
        return {"best": _JUDGE_UNPARSED, "synthesis": raw[:_JUDGE_FALLBACK_MAX_CHARS]}
    return {
        "best": str(parsed.get("best", _JUDGE_UNPARSED)),
        "synthesis": str(parsed.get("synthesis", raw)),
    }


def no_quorum_verdict() -> dict:
    """The verdict-shaped record of a sitting where EVERY speaker failed.

    GRIP#6557: the CLI rightly skips the judge on a dead panel (a paid call
    that cannot be informed by anything) but then handed seal() an empty stub,
    and _seal_payload crashed on verdict["judge_model"] — so the failed
    sitting left NO record, the opposite of always-record. This shape seals:
    judge model None (no judge was consulted — a fact, not a fabrication),
    judge_ok False so every caller's quorum check still fails, and a synthesis
    that says so in words. Per rules/no-quorum-is-not-a-verdict, the record
    proves the sitting happened and died; it ratifies nothing.
    """
    return {"best": "",
            "synthesis": "NO_QUORUM: every speaker failed; no judge was consulted",
            "judge_model": None, "judge_ok": False,
            "judge_finish_reason": "not-consulted", "judge_truncated": False}


def _require_exogenous_judge(judge_name: str) -> None:
    """One chokepoint for the judge constitution — judge() and roundtable()
    both call it, roundtable BEFORE any paid call is made."""
    if judge_name in ANTHROPIC_SPEAKERS:
        raise ValueError(
            "the judge must be exogenous to the author's family: an Anthropic "
            "seat may speak (operator-directed, V>> 2026-09-24) but never judge "
            "(rules/exogenous-anchor-law.md)")


def judge(prompt: str, results: list, judge_name: str) -> dict:
    """A non-Anthropic judge ranks the speakers and synthesises the consensus."""
    _require_exogenous_judge(judge_name)
    answered = [r for r in results if r["ok"]]
    cut = (" — WARNING: this answer was CUT OFF mid-sentence (the model ran out of "
           "token budget). Treat it as incomplete: it may never state a position, "
           "and any apparent verdict in it may be the speaker restating the question.")
    panel = "\n\n".join(
        f"[{r['name']} / {r['model']}]{cut if r.get('truncated') else ''}:\n{r['text']}"
        for r in answered)
    ask = (
        f"You are an impartial judge on a council of AI models. The question was:\n"
        f"\"{prompt}\"\n\nThe panel answered:\n\n{panel}\n\n"
        "Pick the single strongest answer and synthesise the council consensus in ONE "
        "sentence. Reply ONLY with compact JSON: "
        '{"best": "<model-name>", "synthesis": "<one sentence>"}'
    )
    res = provider_call(judge_name, [{"role": "user", "content": ask}], time.time(),
                        max_tokens=_JUDGE_MAX_TOKENS)
    verdict = _parse_judge_reply(res["text"])
    verdict["judge_model"] = res["model"]
    verdict["judge_ok"] = res["ok"]
    # The judge is cut off by its budget exactly as a speaker is, and until now
    # only the speakers' flags were carried into the seal. A truncated verdict
    # was sealed looking identical to a complete one -- see _JUDGE_MAX_TOKENS
    # for the run where that happened.
    verdict["judge_finish_reason"] = res.get("finish_reason", "")
    verdict["judge_truncated"] = bool(res.get("truncated", False))
    return verdict


#: Keys every speaker result must carry to be sealable. A record is evidence,
#: so a missing field is refused rather than defaulted: sealing `"model": "?"`
#: would produce a signed record asserting something nobody measured, which is
#: worse than a loud failure. Previously the omission surfaced as a bare
#: KeyError from inside a dict literal, which said what was missing but not why
#: it mattered.
_REQUIRED_RESULT_KEYS = ("name", "model", "ok", "text")


def _reject_malformed_results(results: list) -> None:
    """Refuse to seal a result missing a field the record asserts."""
    for i, r in enumerate(results):
        missing = [k for k in _REQUIRED_RESULT_KEYS if k not in r]
        if missing:
            raise ValueError(
                f"speaker result {i} ({r.get('name', 'unnamed')!r}) cannot be "
                f"sealed: missing {', '.join(missing)}. A sealed record is "
                "evidence; defaulting an absent field would sign a claim nobody "
                "measured."
            )


def _seal_payload(prompt: str, results: list, verdict: dict) -> dict:
    """The content a record's sha256 is taken over — and ONLY that content.

    Every field here is INSIDE the digest, so adding, renaming or dropping one
    changes the address of every record sealed from that moment on. Pure by
    construction — no clock, no filesystem, no environment — which is what lets
    the recipe be tested without sealing anything. Pinned byte for byte by
    tests/test_council_seal_recipe_golden.py.
    """
    return {
        "prompt": prompt,
        # finish_reason/truncated are sealed so a later reader can tell a complete
        # answer from one cut off mid-thought. .get() keeps older callers that
        # build result dicts by hand working, and never changes an existing
        # record's hash (recompute() re-reads each record's own stored speakers).
        "speakers": [{"name": r["name"], "model": r["model"], "ok": r["ok"], "answer": r["text"],
                      "finish_reason": r.get("finish_reason", ""),
                      "truncated": bool(r.get("truncated", False))}
                     for r in results],
        # The judge carries the same finish_reason/truncated pair as the
        # speakers above, for the same reason and for a sharper one: this is
        # the council's VERDICT. A verdict cut off mid-sentence used to be
        # sealed indistinguishably from a complete one, so the seal proved
        # faithfully what was said without letting a reader know it was only
        # half of it. Adding keys does not disturb existing records --
        # recompute() re-reads each record's own judge dict, so a seal made
        # before this change still verifies against its own three keys.
        "judge": {"model": verdict["judge_model"], "best": verdict["best"],
                  "synthesis": verdict["synthesis"],
                  "finish_reason": verdict.get("judge_finish_reason", ""),
                  "truncated": bool(verdict.get("judge_truncated", False))},
    }


def _seal_timings(results: list) -> list:
    """Per-speaker latency, persisted so the read timeouts above stop being
    guesses. fan_out already computes start/end for every call and seal() was
    throwing them away, which is why "openai reasons on every call, give it
    90s" could only ever be an assertion - the instrument existed and its
    output was discarded at the last step. Outside the hash, like sealed_at
    and anthropic_calls: timing is provenance about the run, not part of what
    the council SAID, and putting it inside would change nothing about
    tamper-evidence while breaking every historical record's recompute.

    A speaker missing start/end is OMITTED rather than defaulted: a call that
    never ran contributes no timing, never a fabricated zero that would read as
    an instant answer.
    """
    return [
        {"name": r.get("name", "?"), "model": r.get("model", "?"),
         "ok": bool(r.get("ok", False)),
         "seconds": round(r["end"] - r["start"], 2)}
        for r in results
        if isinstance(r.get("start"), (int, float)) and isinstance(r.get("end"), (int, float))
    ]


def _assemble_record(payload: dict, sha: str, results: list,
                     idr_id: "str | None",
                     advisor: "dict | None" = None) -> dict:
    """The record: the hashed payload, plus the provenance ABOUT the run.

    Key insertion order is load-bearing and asserted by
    tests/test_council_seal_recipe_golden.py — the record is written with
    ``indent=2``, so a reordering changes the file a reader diffs even though
    it leaves the digest untouched. ``sealed_at`` is the only non-deterministic
    input anywhere in the sealing path, and it sits here rather than in
    _seal_payload precisely so two seals of one question agree.
    """
    # anthropic_calls is COMPUTED from who actually sat, never asserted: an
    # operator-directed fable seat must be visible in the provenance rather
    # than laundered under the historical hardcoded 0. Outside the hash, like
    # sealed_at — provenance about the run, not part of what the council said.
    anthropic = sum(1 for r in results if r.get("name") in ANTHROPIC_SPEAKERS)
    if advisor and advisor.get("name") in ANTHROPIC_SPEAKERS:
        anthropic += 1
    record = dict(payload, sha256=sha,
                  anthropic_calls=anthropic,
                  sealed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    record["timings"] = _seal_timings(results)
    if advisor:
        # Roundtable attribution. The advisor's WORDS are inside the hashed
        # prompt (tampering flips verification); only this attribution sits
        # outside the hash, like timings — provenance about the run. Appended
        # after timings so legacy records keep their exact key order.
        record["advisor"] = advisor
    if idr_id:
        record["idr_id"] = idr_id
    return record


def _persist_record(record: dict, sha: str) -> pathlib.Path:
    """Write the record under STATE_DIR, addressed by the first 16 hex of its digest.

    The only filesystem seam in the sealing path. ``resolve_record_path`` is the
    reader half and joins the same ``<sha[:16]>.json`` name, so the two must keep
    agreeing; STATE_DIR is read at call time so a test can redirect it.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{sha[:16]}.json"
    # encoding is load-bearing, not tidiness. ensure_ascii=False keeps real
    # non-ASCII in the file (council answers carry em dashes and non-breaking
    # hyphens), and without an explicit encoding Python uses the locale default
    # -- cp1252 on Windows, which cannot encode them. Measured 2026-09-22: the
    # seal raised UnicodeEncodeError on U+2011 AFTER every speaker had answered,
    # losing the whole council run and leaving a 0-byte record behind.
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def seal(prompt: str, results: list, verdict: dict,
         advisor: "dict | None" = None) -> tuple[dict, str]:
    """Build a deterministic, hashable provenance record. sha256 is over content only.
    Also appends a signed IDR leaf to the GRIP IDR forest for tamper-evident provenance.

    One step per line, each its own function since GRIP#6436 broke this body up
    to get it under the KISS gate: refuse a malformed result, build the hashed
    payload, take the digest, sign a leaf over it, assemble the record around
    it, write it out. Nothing about the recipe moved — the digest expression
    stays in THIS body deliberately, because tests/test_council_seal_format.py
    pins it here as the phase-1 guard against the RFC 8785 sealer landing early.
    """
    _reject_malformed_results(results)
    payload = _seal_payload(prompt, results, verdict)
    # Deliberately no seal_format: the SEALER stays on the legacy form until
    # every checkout can verify both. A record written in the new form today
    # would read as TAMPERED on any machine running an older build, so the
    # verifier ships first and the sealer follows in a separate change.
    canonical = canonical_bytes(payload)
    sha = hashlib.sha256(canonical).hexdigest()
    idr_id = _seal_idr(payload, sha)
    record = _assemble_record(payload, sha, results, idr_id, advisor=advisor)
    _persist_record(record, sha)
    return record, sha


def canonical_bytes(payload: dict, seal_format: "str | None" = None) -> bytes:
    """The exact bytes a record's seal is taken over, per that record's OWN format.

    ONE function decides this, because `seal` and `recompute` each used to carry
    their own `json.dumps(...)` call. Two independent expressions of one property
    can drift, and if they drift here every historical record reads as TAMPERED
    while the code looks correct in both places.

    `seal_format` absent or empty means the pre-RFC-8785 form, which is what every
    record sealed before this existed carries. `jcs.dumps_for_format` refuses an
    unrecognised value rather than guessing, so a build that cannot read a
    record's format says so instead of verifying it against the wrong bytes.

    WHY THE FORMAT FIELD SITS OUTSIDE THE HASHED PAYLOAD, which looks unsafe and
    is not: a field naming which bytes to hash cannot be inside those bytes
    without circularity. It is safe because tampering with it fails CLOSED in
    both directions. Add `seal_format` to a legacy record and recompute uses JCS
    bytes, so the sha no longer matches and the record reads TAMPERED; strip it
    from a JCS record and recompute uses legacy bytes, same result. The field can
    be edited, and editing it can only ever break verification, never forge it.
    """
    return jcs.dumps_for_format(payload, seal_format)


def recompute(record: dict) -> str:
    """Re-hash the content of a sealed *record* — must equal record['sha256'] iff untampered."""
    payload = {"prompt": record["prompt"], "speakers": record["speakers"], "judge": record["judge"]}
    return hashlib.sha256(canonical_bytes(payload, record.get("seal_format"))).hexdigest()


_ADVISOR_PROMPT = (
    "You are the professional up-front ADVISOR at a Knight's Roundtable of AI "
    "models. A panel will answer the question below AFTER you; a separate "
    "judge will rank their answers. Your job is consultancy, not answering: "
    "sharpen the question, surface hidden premises and constraints, name what "
    "a strong answer must cover and the failure modes a weak one falls into. "
    "Be concise and concrete — your brief is handed verbatim to the panel.\n\n"
    "THE QUESTION:\n{prompt}"
)


def consult_advisor(prompt: str, advisor: str, t0: float) -> dict:
    """One advisor consultation — the same call contract as any speaker."""
    ask = _ADVISOR_PROMPT.format(prompt=prompt)
    return provider_call(advisor, [{"role": "user", "content": ask}], t0)


def _advisor_meta(res: dict) -> dict:
    """The unhashed attribution for the seal: who advised, how it went.
    The brief's TEXT is not here — a usable brief rides inside the hashed
    prompt; a failed one records only its error."""
    meta = {"name": res["name"], "model": res["model"], "ok": bool(res["ok"]),
            "seconds": round(res["end"] - res["start"], 2)}
    if not res["ok"]:
        meta["error"] = res["text"][:200]
    return meta


def _check_roundtable_roles(advisor: str, judge_name: str) -> None:
    """The roundtable constitution, enforced before any paid call: the judge is
    exogenous (never Anthropic), and the advisor may not also judge."""
    _require_exogenous_judge(judge_name)
    if advisor == judge_name:
        raise ValueError(
            "the advisor may not also judge: the advisor shapes the question "
            "every answer responds to, which is authorship — the verifier is "
            "never the author (rules/exogenous-anchor-law.md)")


def _seat_prompt(prompt: str, advisor: str, advisor_res: dict) -> str:
    """The prompt the panel and judge see: the original, plus the advisor's
    brief when the advisor answered. A dead advisor leaves the prompt as it was."""
    if not (advisor_res["ok"] and advisor_res["text"].strip()):
        return prompt
    return (
        f"{prompt}\n\n[ADVISOR BRIEF — {advisor} / {advisor_res['model']}]\n"
        f"{advisor_res['text'].strip()}\n[/ADVISOR BRIEF]\n"
        "Answer the original question; take the advisor's brief into "
        "account where it genuinely helps."
    )


def roundtable(prompt: str, advisor: str, speakers: "list | None" = None,
               judge_name: "str | None" = None,
               system_prompts: "dict[str, str] | None" = None) -> dict:
    """Knight's Roundtable: advisor -> panel -> judge, sealed (V>> 2026-09-24).

    The advisor is consulted FIRST and its brief is embedded in the prompt the
    panel and judge actually see — inside the hashed payload, so a doctored
    brief breaks the seal. A dead advisor degrades to a plain council sitting
    (the original prompt, no brief) with the failure sealed as provenance:
    momentum over ceremony, but never silently.

    ``system_prompts`` optionally steers each panel seat with its own system
    instruction (the roundtable orchestrator's bespoke prompts), passed straight
    through to :func:`fan_out`.

    Constitution, enforced before any paid call: the judge is exogenous
    (never Anthropic), and the advisor may not also judge — an advisor shapes
    the question every answer responds to, which is authorship
    (rules/exogenous-anchor-law.md: the verifier is never the author).
    """
    speakers = list(speakers or DEFAULT_SPEAKERS)
    judge_name = judge_name or DEFAULT_JUDGE
    _check_roundtable_roles(advisor, judge_name)
    t0 = time.time()
    advisor_res = consult_advisor(prompt, advisor, t0)
    seated_prompt = _seat_prompt(prompt, advisor, advisor_res)
    results, panel_total = fan_out(seated_prompt, speakers, system_prompts)
    # A dead panel never consults the judge (uninformable, paid) — GRIP#6557's
    # crash class; the sitting still seals under an explicit no-quorum verdict.
    verdict = (judge(seated_prompt, results, judge_name)
               if any(r["ok"] for r in results) else no_quorum_verdict())
    record, sha = seal(seated_prompt, results, verdict,
                       advisor=_advisor_meta(advisor_res))
    return {"advisor": advisor_res, "results": results, "verdict": verdict,
            "record": record, "sha": sha,
            "total": time.time() - t0,          # advisor + panel + judge
            "panel_total": panel_total,          # fan-out wall time alone —
            # what render()'s parallel-overlap statistic is defined over
            "idr_id": record.get("idr_id"),
            "speakers_ok": sum(1 for r in results if r["ok"])}


def run_council(prompt: str, speakers: "list | None" = None,
                judge_name: "str | None" = None) -> dict:
    """Convene the full council in one call: fan-out -> judge -> seal.

    The reusable entry the OpenAI-compat endpoint (bin/grip-council-endpoint) calls.
    Returns a dict: {results, verdict, record, sha, total, idr_id, speakers_ok}.
    Never raises for a dead speaker (each provider_call is self-contained); a
    caller checks ``speakers_ok`` + ``verdict['judge_ok']`` for quorum.
    """
    # Copy — never alias DEFAULT_SPEAKERS (or the caller's list) into the run:
    # the endpoint serves concurrent requests from this one module, so a shared
    # list mutated anywhere would leak across runs. Same defensive pattern as
    # the CLI (bin/grip-sovereign-council). AGORA #3995 DEFER concerns 1+2.
    speakers = list(speakers or DEFAULT_SPEAKERS)
    judge_name = judge_name or DEFAULT_JUDGE
    results, total = fan_out(prompt, speakers)
    verdict = judge(prompt, results, judge_name)
    record, sha = seal(prompt, results, verdict)
    return {
        "results": results,
        "verdict": verdict,
        "record": record,
        "sha": sha,
        "total": total,
        "idr_id": record.get("idr_id"),
        "speakers_ok": sum(1 for r in results if r["ok"]),
        # Gate-0 seed check, surfaced programmatically for endpoint callers
        # (fan_out already printed it to stderr); None when the seed is clean.
        "premise_advisory": premise_advisory(prompt),
    }


def _council_merkle_root(_idr_path: "pathlib.Path | None" = None) -> "tuple[str, int] | None":
    """(RFC6962 root, count) over every sealed council verdict — the single value that
    commits them all. Returns None if the IDR forest lib is unavailable."""
    root_path = str(_grip_root())
    if root_path not in sys.path:
        sys.path.insert(0, root_path)
    try:
        from lib.precog.idr import read_idr_chain
        from lib.idr_forest import empty_forest, add_idr, forest_merkle_root
    except ImportError:
        return None
    leaves = [lf for lf in read_idr_chain(path=_idr_path)
              if getattr(lf, "kind", "") == "sovereign-council-verdict"]
    if not leaves:
        return ("", 0)
    anchor = "council:sovereign"  # must carry an exogenous-anchor prefix (idr_forest rule)
    forest = empty_forest((anchor,))
    for lf in leaves:
        forest = add_idr(forest, lf, anchor_id=anchor)
    return (forest_merkle_root(forest), len(leaves))
