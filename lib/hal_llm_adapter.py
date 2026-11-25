"""HALLLMAdapter — GRIP's canonical AI syscall, backed by HAL's RAILLM runtime.

Primary path (GRIP #2722, transport B): an HTTP POST to hal-server's
``/api/infer`` endpoint (HAL #331), which wraps ``grip_infer`` (the
RAILLM-over-happi default runtime, HAL #328 / ADR-0001) in-process on the
HAL side. ``grip_infer`` cascades CCH -> Kimi -> cheap -> local beneath a
single happi envelope, so GRIP is decoupled from any one provider's
context window.

If hal-server is unreachable (not running), returns non-200, or returns
``ok=false``, the call transparently falls back to the legacy fail-safe
chain — it is never fail-hard:

  0. HAL /api/infer (RAILLM)    — primary; provider-agnostic, audit-capable
  1. HAL HTTP server (:3850)    — legacy: /api/query routing, LimitSentinel
  2. Direct Groq API            — legacy: Llama-3.3-70b free tier
  3. Direct Gemini API          — legacy: gemini-3.5-flash-lite free tier
  4. Direct xAI API             — legacy: grok, paid credits
  5. Neutral fallback           — 0.5 sentinel JSON (Goodhart protection)

Transport seam: the grip_infer hop is isolated in ``_grip_infer_call`` as a
single HTTP POST. The HTTP boundary is the clean syscall surface
(syscall-doctrine): no cross-repo import, no subprocess, no namespace
collision. This is transport B — it superseded the interim subprocess
bridge (transport C, #2726) once HAL #331 shipped the endpoint.

Rate-limit recovery: a 429 on a legacy rung triggers a 60s in-process block +
writes ``cooldown_until`` to ``~/.hal/state/hal/limits.json`` (shared with
HAL's LimitSentinel for cross-process awareness).

Usage:
    from lib.hal_llm_adapter import HALLLMAdapter   # or: LlamaEngine (alias)
    adapter = HALLLMAdapter()
    text = adapter.generate("Score this code diff", json_mode=True)
    reply = adapter.chat([{"role": "user", "content": "hello"}])
    audited = adapter.infer("decide X", audit=True)   # -> dict incl idr_ref
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # type-only — no runtime import (syscall-doctrine: adapter stays import-light)
    from lib.decision_record import Decision

logger = logging.getLogger(__name__)

__all__ = [
    "HALLLMAdapter",
    "HALResult",
    "HALToolResult",
    "HALToolCall",
    "LlamaEngine",
    "hear",
    "infer_with_tools",
]

_HAL_BASE = f"http://localhost:{os.environ.get('HAL_PORT', '3850')}"
_HAL_TIMEOUT_S = 20.0
_DIRECT_TIMEOUT_S = 30.0
_RATE_LIMIT_BACKOFF_S = 60  # seconds to skip a provider after 429
_NEUTRAL_JSON = '{"score": 0.5, "reasoning": "provider unavailable", "violations": []}'

# Canonical inference defaults — single source for generate()/chat() signature
# defaults AND the fail-DEGRADED legacy fallback in infer(). Centralised so the
# degraded path provably uses the SAME params as the documented happy-path
# defaults and cannot silently drift (GRIP#2747 HAL-Council instinct).
_DEFAULT_MAX_TOKENS = 512
_DEFAULT_TEMPERATURE = 0.1

# ---------------------------------------------------------------------------
# Economical-by-default routing (V>>-ratified 2026-06-11, H-HAL-ECON-DEFAULT)
# ---------------------------------------------------------------------------
# GRIP's high-volume PROGRAMMATIC LLM calls (council speakers, evo, broly
# deliberators, classification, summarisation, the copilot-delegate-class
# cheap calls) reach grip_infer through this adapter WITHOUT an explicit
# ``model``. Pre-2026-06-11 that meant grip_infer's fixed ladder primary —
# ``anthropic/claude-opus-4-8`` via CCH (Claude Premium) — FIRST, on every
# such call. For ECONOMICS the global default is flipped to a cheap, fast
# cloud model; the RAILLM cascade (CCH -> Kimi -> cheap -> local) still runs
# as the failover BENEATH it, so a cheap-rung outage degrades to a working
# provider rather than failing hard (never a blanket hard-downgrade).
#
# SCOPED, not blanket (rules/llm-augmentation-pattern.md — cheap L2, capable
# floor): this default ONLY captures calls that pass no explicit ``model``.
# Any caller that NEEDS a capable model passes ``model=`` and overrides this
# (precedence: explicit model > this default). The AGORA judge + speakers do
# NOT route through this adapter at all — they dispatch via
# ``lib/hal_council_dispatch.py`` with their own hardcoded provider strings
# (``_JUDGE_PROVIDER = gemini/gemini-3.1-pro-preview``), so the loop's
# exogenous anchor (rules/exogenous-anchor-law.md) is exempt BY CONSTRUCTION,
# never captured by a cheap default here.
#
# Reversible: set ``GRIP_ECON_DEFAULT_MODEL`` to a different address to retune,
# or to ``""`` / ``off`` to restore the prior CCH-primary default exactly
# (``_resolve_econ_default`` then returns None -> body carries ``model=""`` ->
# byte-identical to pre-2026-06-11). Prior value: implicit ladder primary
# (``anthropic/claude-opus-4-8`` via CCH). New value: the cheap default below.
_ECON_DEFAULT_MODEL = "groq/openai/gpt-oss-120b"


def _resolve_econ_default() -> Optional[str]:
    """The economical default model for programmatic calls that pass no
    explicit ``model``. Env ``GRIP_ECON_DEFAULT_MODEL`` overrides the
    constant; an empty / ``off`` / ``none`` value disables the default
    (returns None -> grip_infer uses its own ladder primary, the exact
    pre-2026-06-11 behaviour). Total + fail-safe: never raises."""
    raw = os.environ.get("GRIP_ECON_DEFAULT_MODEL")
    if raw is None:
        return _ECON_DEFAULT_MODEL or None
    val = raw.strip()
    if val.lower() in ("", "off", "none", "false", "0"):
        return None
    return val

# Shared sentinel state — written by HAL server, read here for cross-process awareness
_SENTINEL_STATE = os.path.expanduser("~/.hal/state/hal/limits.json")

# Telemetry: idr-gap marker written on every degraded non-audited inference
_DEGRADED_MARKER_LOG = os.path.expanduser("~/.hal/logs/hal-down-degraded.jsonl")

# Provenance-verified usage ledger (lib/llm_usage_ledger). Optional + fail-open:
# telemetry must NEVER break the AI syscall. Absent in non-GRIP contexts -> no-op.
try:
    from lib.llm_usage_ledger import record_usage as _record_usage
except Exception:  # pragma: no cover - import guard (non-GRIP context)
    def _record_usage(*_a, **_k) -> None:  # type: ignore[misc]
        return None


# ---------------------------------------------------------------------------
# Typed result — callers on the audited IDR path cannot mistake a degraded
# answer for an audited one (operator decision, V>>, 2026-05-16)
# ---------------------------------------------------------------------------


@dataclass
class HALResult:
    """Typed result from HALLLMAdapter.infer().

    Fields:
        text:     Inference answer. None when hal_down=True on an audit call.
        audited:  True only when HAL handled the call with a live idr_ref.
                  False on ALL degraded or hal-down paths.
        degraded: True when legacy chain answered but HAL was unreachable.
        hal_down: True when HAL was unreachable AND audit=True — must refuse.
        reason:   Human-readable reason on non-happy paths; empty string otherwise.
        provider: HAL-reported provider that served this call (e.g. anthropic-cch).
        model:    HAL-reported model id — the actual route (e.g. claude-opus-4-7).
        idr_ref:  Signed-IDR ref when audited; re-verifiable against the chain.
        decision: Optional delegated-decision anatomy (lib/decision_record.Decision)
                  — why/confidence/uncertainties/assumptions/falsifier. None on
                  deterministic, legacy, and HAL-down paths (NEVER fabricated —
                  a model-down path must not invent a decision it did not make).
    """

    text: Optional[str]
    audited: bool
    degraded: bool = False
    hal_down: bool = False
    reason: str = ""
    provider: Optional[str] = None   # HAL-reported provider that served this call
    model: Optional[str] = None      # HAL-reported model id (the actual route taken)
    idr_ref: Optional[object] = None  # signed-IDR ref (dict {'sha256':…} or str) — re-verifiable
    decision: Optional["Decision"] = None  # delegated-decision anatomy; None on deterministic/legacy/HAL-down

    def must_refuse(self) -> bool:
        """True when the audit path was requested but HAL was unreachable (fail-CLOSED)."""
        return self.hal_down and self.text is None

    def provenance(self) -> str:
        """One-line honest disclosure of which model produced this answer.

        Per-request LLM-provenance (V>> 2026-06-08; AI Craftspeople Guild
        identity-disclosure doctrine). States the ACTUAL model HAL routed to —
        server-reported, never a GRIP-side claim — so GRIP cannot self-certify a
        model it did not run. Re-verifiable: when ``audited`` the ``idr_ref``
        ties to a signed IDR, so a label disagreeing with the signed record fails
        verification (Alex Bunardzik's "disclose your nature" rendered as V>>'s
        "disclose your provenance, re-checkably" — green can go red).
        """
        if not self.model and not self.provider:
            return "provenance: unknown (no model reported)"
        label = " ".join(
            part for part in (self.model, f"via {self.provider}" if self.provider else "")
            if part
        )
        short = self._idr_short()
        if self.audited:
            # Audited is ALWAYS surfaced — with the re-verifiable sha when present,
            # else a bare "audited" marker. Never silently dropped (AGORA #3435).
            return f"{label} · audited idr:{short}" if short else f"{label} · audited"
        if self.degraded:
            return f"{label} · degraded (unaudited)"
        return label

    def _idr_short(self) -> str:
        """Short, re-verifiable IDR handle for the disclosure line.

        ``idr_ref`` may be a signed dict (``{'sha256': ...}`` — HAL's audited
        path) or a bare string; return the first 12 hex of the sha (or of the
        string). Empty when there is no ref. The full ref stays on the object
        for chain verification — only the *display* is shortened.
        """
        ref = self.idr_ref
        if isinstance(ref, dict):
            return (ref.get("sha256") or "")[:12]
        if isinstance(ref, str):
            return ref[:12]
        return ""  # unknown type (bytes/other) → no handle, never garbage (AGORA #3435)


# Canonical typed refusal for audit-down path (singleton — immutable dataclass)
_HAL_DOWN_REFUSAL = HALResult(text=None, audited=False, hal_down=True, reason="HAL_DOWN")


# ---------------------------------------------------------------------------
# Tool-use typed results — `infer_with_tools` routes through hal-server's
# /api/infer-with-tools (HAL W3) which drives an Anthropic Messages tool-loop
# over a capability-locked, sandbox-bound Read/Grep/Glob dispatcher. Returns
# typed values so callers cannot mistake an error for a successful answer.
# ---------------------------------------------------------------------------


@dataclass
class HALToolCall:
    """One tool invocation that fired during a tool-loop run.

    ``input`` is the raw arg dict the model sent. ``is_error`` is True
    when the SandboxedDispatcher blocked the call (capability-lock or
    sandbox escape) — the tool_result fed back to the model carried
    ``is_error=True`` and the model saw an error message in place of
    real output.
    """
    tool: str
    input: dict
    is_error: bool
    iteration: int


@dataclass
class HALToolResult:
    """Typed result from ``HALLLMAdapter.infer_with_tools``.

    Fields mirror the ``/api/infer-with-tools`` JSON response one-for-one
    so a caller wanting to log or audit the run reads the same shape
    server-side and client-side.

    - ``ok``: True only when the loop completed and HAL returned ok=true.
              Any HAL fail-safe (sandbox refusal, CCH unavailable, import
              failure, loop crash) sets ok=False; ``error_code``/
              ``error_message`` carry the reason.
    - ``text``: Final assistant text. Empty string on any non-ok path.
    - ``tool_calls``: Ordered list of every tool invocation the model
              made, including BLOCKED ones (visible audit trail).
    - ``iterations``: Total chat_fn calls made (1 = no tools fired).
    - ``hit_iteration_cap``: True when the model kept emitting tool_use
              past ``max_iterations`` — the loop terminated to prevent
              runaway. Caller should treat the partial trace as
              indicative, not authoritative.
    - ``hal_down``: True when the entire HTTP transport failed
              (connection refused, timeout, malformed body) — same
              fail-CLOSED contract as :class:`HALResult` for the
              non-tools path.
    - ``error_code`` / ``error_message``: When ok=False, the server-side
              cause. None when ok=True or hal_down=True (no server reply).
    """
    ok: bool
    text: str
    tool_calls: list  # list[HALToolCall]
    iterations: int = 0
    hit_iteration_cap: bool = False
    hal_down: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    def must_refuse(self) -> bool:
        """True when the loop did not complete successfully.

        Symmetric with :meth:`HALResult.must_refuse` — a caller on the
        audited IDR path uses this to decide whether to emit
        the result. True iff: HAL down OR loop returned ok=False.
        """
        return self.hal_down or not self.ok


_HAL_TOOLS_DOWN_REFUSAL = HALToolResult(
    ok=False, text="", tool_calls=[], iterations=0,
    hit_iteration_cap=False, hal_down=True,
    error_code="hal_transport_failed",
    error_message="hal-server /api/infer-with-tools unreachable",
)


def _strict_bool(value, *, error_default: bool = False) -> bool:
    """Strict boolean projection from an untrusted source.

    Python's ``bool(x)`` is dangerously loose for over-the-wire bool
    fields — ``bool("false")`` returns ``True`` (any non-empty string
    is truthy), ``bool(0.0)`` returns ``False``, ``bool([0])`` returns
    ``True``. A malformed server returning ``is_error: "false"`` as a
    string would silently flip the field's meaning.

    This helper returns the value ONLY if it is already a ``bool``
    instance; anything else defaults to ``error_default``. For tool
    error flags, ``error_default=True`` is the fail-CLOSED choice:
    treat type-confusion as an error rather than a silent success.
    For ``ok``-style success flags, ``error_default=False`` is the
    fail-CLOSED choice: treat type-confusion as failure.

    AGORA Council #3286 advisory (this PR): the over-the-wire bool
    fields ``is_error`` / ``ok`` / ``hit_iteration_cap`` must survive
    a defensive type check. Test mutation killed:
    ``isinstance(x, bool)`` → ``bool(x)`` regresses on string ``"false"``.
    """
    if isinstance(value, bool):
        return value
    return error_default


def _write_degraded_marker(prompt_prefix: str) -> None:
    """Append one JSONL idr-gap telemetry line. Best-effort — never raises."""
    try:
        os.makedirs(os.path.dirname(_DEGRADED_MARKER_LOG), exist_ok=True)
        entry = json.dumps({"ts": time.time(), "event": "hal_down_degraded",
                            "prompt_prefix": prompt_prefix[:80]})
        with open(_DEGRADED_MARKER_LOG, "a") as fh:
            fh.write(entry + "\n")
    except Exception:
        pass


# --- grip_infer transport seam (GRIP #2722, transport B: hal-server HTTP) ---
_GRIP_INFER_TIMEOUT_S = 45.0  # RAILLM ladder may cascade; bounded read timeout
_HEAR_TIMEOUT_S = 120.0  # multimodal audio: file read + encode + provider call (matches grip-hear's REST bound)
_grip_infer_lock = threading.Lock()
_grip_infer_hint_emitted = False  # one-time "hal-server unreachable" hint


def _sentinel_check_cooldown(provider: str) -> bool:
    """Return True if provider is in cooldown per HAL's persistent sentinel state file."""
    try:
        with open(_SENTINEL_STATE) as fh:
            state = json.load(fh).get(provider, {})
        return time.time() < (state.get("cooldown_until") or 0)
    except (OSError, json.JSONDecodeError, KeyError):
        return False


def _sentinel_write_cooldown(provider: str, until: float) -> None:
    """Write provider cooldown into the shared sentinel state (cross-process visibility).

    Uses .tmp → os.replace() for atomicity so concurrent HAL server writes are safe.
    """
    try:
        os.makedirs(os.path.dirname(_SENTINEL_STATE), exist_ok=True)
        data: dict = {}
        try:
            with open(_SENTINEL_STATE) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass
        data.setdefault(provider, {})["cooldown_until"] = until
        tmp = _SENTINEL_STATE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, _SENTINEL_STATE)
    except Exception:
        pass


def _keychain(service: str) -> Optional[str]:
    """Fetch API key from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


class _ProviderBackoff:
    """Thread-safe provider backoff registry with cross-process sentinel awareness.

    Uses wall-clock time (time.time) so cooldown timestamps are compatible with
    HAL's LimitSentinel state file (~/.hal/state/hal/limits.json).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blocked: dict[str, float] = {}  # provider -> unblock_wall_time

    def block(self, provider: str, duration_s: float = _RATE_LIMIT_BACKOFF_S) -> None:
        until = time.time() + duration_s
        with self._lock:
            self._blocked[provider] = until
        _sentinel_write_cooldown(provider, until)
        logger.info("HALLLMAdapter: %s blocked for %.0fs (rate-limited)", provider, duration_s)

    def is_blocked(self, provider: str) -> bool:
        with self._lock:
            until = self._blocked.get(provider, 0)
            if time.time() < until:
                return True
            self._blocked.pop(provider, None)
        # In-process block expired — cross-check the shared sentinel state file
        return _sentinel_check_cooldown(provider)

    def status(self) -> dict[str, float]:
        with self._lock:
            now = time.time()
            return {p: max(0.0, t - now) for p, t in self._blocked.items() if t > now}


_backoff = _ProviderBackoff()



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


def _post_json(url: str, payload: dict, headers: dict, timeout_s: float) -> dict:
    """POST JSON, return parsed response dict. Raises on HTTP errors.

    Every model route in this module sends through here, so the payload's prose
    passes the redaction shield whole first. A failed scrub raises ShieldFailed,
    an OSError, which each caller already treats as a failed send.
    Billing policy is checked before scrubbing or sending.
    """
    data = json.dumps(_shield(payload, to=url)).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        **headers,
    })
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


# --- grip_infer transport seam (GRIP #2722, transport B: the ONE HTTP hop) -


def _emit_grip_infer_hint(reason: str) -> None:
    """One-time, non-spammy operator hint when the primary path is down."""
    global _grip_infer_hint_emitted
    with _grip_infer_lock:
        if _grip_infer_hint_emitted:
            return
        _grip_infer_hint_emitted = True
    logger.warning(
        "HALLLMAdapter: HAL grip_infer unavailable (%s) — using legacy "
        "provider chain. Start hal-server (pm2 hal-server, :3850) to enable "
        "the RAILLM default path",
        reason,
    )


_INFER_WITH_TOOLS_TIMEOUT_S = 120.0  # tool loops can run multiple iterations


def _infer_with_tools_call(
    prompt: str,
    *,
    sandbox_root: str,
    tools: Optional[list] = None,
    model: Optional[str] = None,
    max_iterations: int = 8,
    system: Optional[str] = None,
    max_tokens: int = 4096,
) -> Optional[dict]:
    """Transport for ``/api/infer-with-tools`` (HAL W3).

    Posts to hal-server's tool-loop route over the same HTTP boundary
    ``_grip_infer_call`` uses for ``/api/infer``. Returns the raw response
    dict on success (HTTP 200 — including server-side ``ok=false`` shapes),
    or None on any transport failure (connection refused, timeout,
    non-2xx, malformed body) so the caller can return a fail-CLOSED
    ``HALToolResult(hal_down=True)``. Never fail-hard.

    The dispatcher's capability lock is enforced **server-side**; this
    transport is a thin pipe — it does not duplicate or re-check the
    allowlist. Defence-in-depth lives on the HAL side per
    ``rules/untrusted-content-capability-lock.md``.
    """
    payload: dict = {
        "prompt": prompt,
        "sandbox_root": sandbox_root,
        "max_iterations": max_iterations,
        "max_tokens": max_tokens,
    }
    if tools is not None:
        payload["tools"] = tools
    if model:
        payload["model"] = model
    if system:
        payload["system"] = system
    try:
        return _post_json(
            f"{_HAL_BASE}/api/infer-with-tools",
            payload, {}, _INFER_WITH_TOOLS_TIMEOUT_S,
        )
    except Exception as exc:
        _emit_grip_infer_hint(f"hal-server /api/infer-with-tools: {exc}")
        return None


def _usage_tokens(usage: object) -> tuple[int, int]:
    """Extract ``(input, output)`` token counts from a provider/server usage block.

    Single source for the key-name fallback so the primary (grip_infer:
    ``input_tokens`` / ``prompt_tokens``) and legacy (OpenAI-compat:
    ``prompt_tokens`` / ``completion_tokens``) paths cannot drift — a provider
    schema change can no longer make one path silently record 0 while the other
    succeeds (AGORA #3444 Speaker C). Non-dict / missing / non-numeric → 0,
    never fabricated (R0).
    """
    u = usage if isinstance(usage, dict) else {}
    tin = u.get("input_tokens") or u.get("prompt_tokens") or 0
    tout = u.get("output_tokens") or u.get("completion_tokens") or 0
    try:
        return int(tin or 0), int(tout or 0)
    except (TypeError, ValueError):
        return 0, 0


def _record_grip_infer_usage(result: Optional[dict], audit: bool) -> None:
    """Count one primary-path (grip_infer) provider call in the usage ledger.

    Fail-open by construction (``record_usage`` swallows); guarded here too so a
    malformed result never touches the syscall return path. Tokens come from the
    server ``usage`` block when present — the normalised GripInferResult drops
    them, so absence -> 0, never fabricated (R0).
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return
    tin, tout = _usage_tokens(result.get("usage"))
    _record_usage(
        result.get("provider"), result.get("model"),
        input_tokens=tin, output_tokens=tout,
        audited=bool(audit), idr_ref=result.get("idr_ref"),
        caller="grip_infer", path="primary",
    )


def _grip_infer_call(
    prompt: str,
    model: Optional[str],
    audit: bool,
    *,
    media: Optional[list] = None,
    content_parts: Optional[list] = None,
    timeout_s: float = _GRIP_INFER_TIMEOUT_S,
    no_failover: bool = False,
    max_tokens: Optional[int] = None,
) -> Optional[dict]:
    """Transport B (GRIP #2722): POST the request to hal-server's
    ``/api/infer`` endpoint (HAL #331), which wraps ``grip_infer`` in-process
    on the HAL side. Returns the normalised GripInferResult dict, or None on
    ANY transport failure — connection refused (hal-server down), non-200,
    timeout, or unparseable body — so the caller uses the legacy fail-safe
    chain. Never fail-hard. The HTTP boundary is the clean syscall surface:
    no cross-repo import, no subprocess, no namespace collision.

    Multimodal (HAL #414, additive): ``media`` is a list of local file paths
    HAL reads + base64-encodes server-side; ``content_parts`` is a pre-built
    OpenAI parts list. Both are added to the body ONLY when truthy, so the
    text-only body is byte-identical when both are None/empty (a media call
    needs a longer ``timeout_s`` — audio encode + provider call).

    Economical default (H-HAL-ECON-DEFAULT, 2026-06-11): when the caller
    passes no explicit ``model``, the body carries the economical default
    (``_resolve_econ_default()``) so high-volume programmatic calls route to
    a cheap provider instead of CCH/Premium. Explicit ``model`` ALWAYS wins
    (precedence preserved). The env-disabled / empty case resolves to None ->
    ``model=""`` -> grip_infer's own ladder primary (pre-flip behaviour).
    """
    eff_model = _resolve_econ_default() if model is None else model
    body: dict = {"prompt": prompt, "model": eff_model or "", "audit": bool(audit)}
    # A pinned model that HAL cannot serve is SILENTLY failed over by default —
    # HAL's own error text for the suppressed case reads "its rung failed and
    # HAL silently failed over". A caller who pinned a model on purpose (a
    # council seat, a cross-provider check) needs the failure, not a substitute
    # wearing the requested seat's name. Opt-in so every existing caller is
    # byte-identical.
    if no_failover:
        body["no_failover"] = True
    if media:
        body["media"] = media
    if content_parts:
        body["content_parts"] = content_parts
    # HAL#648: the answer budget, sent only when a caller sets one, so HAL's
    # own default applies otherwise and every existing body is unchanged.
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    try:
        result = _post_json(
            f"{_HAL_BASE}/api/infer",
            body, {}, timeout_s,
        )
    except Exception as exc:  # ConnectionRefused/HTTPError/timeout/bad JSON
        _emit_grip_infer_hint(f"hal-server /api/infer: {exc}")
        return None
    _record_grip_infer_usage(result, audit)
    return result


# --- typed decisions: hal-server POST /api/decide (HAL docs/decide.md) -------
#: A decision is one call of about half a second to a decision model. A caller
#: in a 5 s Stop hook must still have time to fall back, so the hop is bounded
#: well under that.
_GRIP_DECIDE_TIMEOUT_S = 2.5


def _shield_decision(body: dict, to: str) -> dict:
    """``body`` scrubbed as a decision body: every string at every depth, keys never rewritten."""
    try:
        from review_redaction import shield_decision_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_decision_payload
        except ImportError as exc:  # no shield, no send
            raise OSError("the decision shield is unavailable, so nothing was sent") from exc
    return shield_decision_payload(body, to=to)


def _query_grip_decide(state, questions: dict, *, model: Optional[str] = None,
                       timeout_s: float = _GRIP_DECIDE_TIMEOUT_S) -> Optional[dict]:
    """Ask HAL's decision model typed questions about ``state``, all in one call.

    Returns HAL's result dict (``ok``, ``reason``, ``answers``, ``model``,
    ``usage``), or None on any transport failure: hal-server down, a server
    without the route, a timeout. The caller then falls back to its own method.
    The body is scrubbed as a decision body before it leaves, and a key the
    shield would change refuses the send; ``_post_json`` scrubs it again,
    which changes nothing.
    """
    url = f"{_HAL_BASE}/api/decide"
    body = {"state": state, "questions": dict(questions)}
    if model:
        body["model"] = model
    try:
        result = _post_json(url, _shield_decision(body, url), {}, timeout_s)
    except Exception as exc:  # ShieldFailed/ConnectionRefused/HTTPError/timeout/bad JSON
        # Quietly: a hal-server without the route is expected until it is
        # restarted onto HAL main, and the one-time grip_infer hint would then
        # both say the wrong path is down and be spent before the real one fails.
        logger.debug("HALLLMAdapter: hal-server /api/decide gave no answer (%s); "
                     "the caller falls back", type(exc).__name__)
        return None
    return result if isinstance(result, dict) else None


# --------------------------------------------------------- gateway fallback

_HAL_GATEWAY_BASE = os.environ.get("HAL_GATEWAY_BASE", "http://127.0.0.1:4010")


def _gateway_token() -> Optional[str]:
    """The gateway's client Bearer PLACEHOLDER, read at call time.

    Never a provider key: the gateway DISCARDS the client's Authorization header
    and substitutes its own per-provider keychain key
    (``lib/hal/gateway_egress.py``). Keeping the placeholder non-Anthropic is
    deliberate — an id the gateway's router does not recognise falls through to
    the verbatim Anthropic relay, and this token makes that path fail loudly
    with 401 instead of quietly spending the subscription (bin/grip-dsh).
    """
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "grip-hal-gateway-token", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        tok = (out.stdout or "").strip()
        return tok or None
    except Exception:
        return None


def _gateway_call(prompt: str, model: str, timeout_s: float,
                  max_tokens: Optional[int] = None) -> Optional[dict]:
    """Serve one pinned request through the HAL GATEWAY instead of /api/infer.

    WHY THIS EXISTS. HAL is two services with different provider support, and
    GRIP was only ever talking to the narrower one. ``/api/infer`` (:3850) runs
    the RAILLM ladder, which in practice serves gemini; the GATEWAY (:4010) is
    an OpenAI-compatible surface that routes to real providers and is what every
    wired harness points at. Measured 2026-09-20: ``openai/gpt-6-astra`` and
    ``openai/gpt-5.6-sol`` answer at the gateway and are refused by /api/infer
    with ``provider_pin_violation``.

    The consequence of the gap was not a missing feature but a SILENT one: every
    multi-model council in GRIP requested N models, had N-1 of them refused, and
    fell back to one model wearing N names.

    Only reached when a PIN was explicitly refused, so the ordinary path is
    byte-identical. The provider prefix is stripped because the gateway wants
    the bare id — ``openai/gpt-6-astra`` sent verbatim is rejected by OpenAI as
    an invalid model ID.
    """
    token = _gateway_token()
    if not token:
        return None
    bare = model.split("/")[-1] if model else ""
    if not bare:
        return None
    body: dict = {"model": bare, "messages": [{"role": "user", "content": prompt}]}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens  # the caller's budget survives the reroute
    try:
        result = _post_json(
            f"{_HAL_GATEWAY_BASE}/v1/chat/completions",
            body,
            {"Authorization": f"Bearer {token}"},
            timeout_s,
        )
    except Exception:
        return None
    return _gateway_reply(result, model, bare)


def _gateway_reply(result: object, model: str, bare: str) -> Optional[dict]:
    """Normalise one gateway chat-completions reply, or None when it has no text.

    Split out of :func:`_gateway_call` unchanged, so the transport and the
    reading of its reply can each be held to the KISS bound.
    """
    if not isinstance(result, dict):
        return None
    choices = result.get("choices") or []
    if not choices:
        return None
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not text:
        return None
    served = result.get("model") or bare
    logger.info("HAL gateway served pinned model %s (refused by /api/infer)", served)
    return {
        "ok": True,
        "text": text,
        "provider": (model.split("/")[0] if "/" in model else None),
        "model": served,
        "idr_ref": None,
        "via": "hal-gateway",
    }


def _no_answer_reason(result: Optional[dict]) -> str:
    """Why an ``/api/infer`` result is not an answer, or ``""`` when it is one.

    The case this exists for: a reasoning model spends the whole answer budget
    on its reasoning. HAL used to return ``ok: true`` with empty text and no
    error code, so the failure had no name. Measured 2026-10-05 on
    deepseek-v4-pro with a 13,457-character prompt: ``output_tokens`` 16384,
    which is HAL's default budget, and 0 characters of text. Since HAL#655
    (2026-10-06) HAL refuses that case itself as ``answer_budget_exhausted``
    and accepts ``max_tokens``; the empty-text branch below still names it on
    a hal-server that predates the change.

    The refusal case names HAL's error code and leaves out its free-text
    message, which is built from an exception and is not ours to print.
    """
    if not result:
        return "HAL did not answer"
    if not result.get("ok"):
        return f"HAL refused the call ({result.get('error_code') or 'no error code'})"
    if (result.get("text") or "").strip():
        return ""
    # ``usage`` comes from another process. _usage_tokens is this module's one
    # reader of it: a block that is not a map, or a count that is not a number,
    # reads as 0, so this fail-safe path can neither raise nor name a count
    # that was never reported.
    _, spent = _usage_tokens(result.get("usage"))
    if spent:
        return (f"{spent} output tokens spent and no text written: "
                "the answer budget ran out before the answer began")
    return "HAL reported success with empty text"


def _voice_no_answer(result: Optional[dict], model: Optional[str], pin: bool, reason: str) -> None:
    """Log why a call gave no answer, as one WARNING, in the two cases that have
    no other voice.

    HAL said ok and wrote nothing. Or HAL refused a pinned call, which has no
    ladder behind it. An unreachable HAL already has its own one-time hint, and
    an unpinned refusal falls through to the legacy chain as before.
    """
    if not result:
        return
    if pin or result.get("ok"):
        logger.warning("grip_infer: no answer from %s: %s",
                       result.get("model") or model or "the default model", reason)


# HAL#648: the budget for the one retry after HAL's default budget (16,384) runs
# out. Room for a reasoning model to finish thinking and still answer, measured
# 2026-10-05 direct to DeepSeek v4 Pro: a 13,457-character prompt ran out at
# 16,384 after 146 s and answered at 64,000 in 123 s; a 20 KB prompt answered at
# 64,000 in 223 s. Billing is per token generated, not per cap. HAL sends the
# value unclamped; a provider that rejects one above its model's output cap gets
# it halved and resent (HAL's lib/hal/providers/openai_compat.py, _handle_http_error).
_BUDGET_RETRY_TOKENS = 64000
# HAL's refusals of a pinned call that the gateway may still be able to serve.
_PIN_REFUSALS = ("provider_pin_violation", "unknown_model")


def _error_code(result: Optional[dict]) -> Optional[str]:
    """HAL's error code on an /api/infer result, or None."""
    return result.get("error_code") if isinstance(result, dict) else None


def _retry_with_budget(result: Optional[dict], prompt: str, model: Optional[str],
                       audit: bool, call_kwargs: dict) -> Optional[dict]:
    """One retry with room to answer when HAL's default budget ran out on the
    model's reasoning (HAL#648). A budget the caller set is a ceiling, so a call
    that set one is never retried above it, however small it was."""
    if _error_code(result) != "answer_budget_exhausted":
        return result
    if call_kwargs.get("max_tokens") is not None:
        return result
    return _grip_infer_call(prompt, model, audit,
                            **{**call_kwargs, "max_tokens": _BUDGET_RETRY_TOKENS})


def _query_grip_infer(
    prompt: str, *, model: Optional[str] = None, audit: bool = False, json_mode: bool = False,
    no_failover: bool = False, timeout_s: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Optional[dict]:
    """Primary path: GRIP's canonical AI syscall via HAL RAILLM over HTTP
    (#2722 transport B → hal-server /api/infer, HAL #331).

    Returns a normalised dict ``{text, ok, provider, model, idr_ref, ...}`` on
    success, or None to signal the caller to use the legacy fail-safe chain.
    json_mode appends a strict-JSON directive and rejects non-JSON output so
    the 'json_mode callers never get parse failures' contract is preserved.

    ``timeout_s`` bounds each hop's read separately: /api/infer, its one budget
    retry, and the gateway fallback. Omitted, the module bound
    _GRIP_INFER_TIMEOUT_S applies and ``_grip_infer_call`` receives no timeout
    argument. It exists because a reasoning seat -- DeepSeek v4 Pro in the
    /ultrathink trio -- answering a long debate prompt can need longer than the
    45 seconds the ladder default allows.

    ``max_tokens`` sets the answer budget (HAL#648) and is a ceiling: the client
    never sends more than the caller asked for. Only a call that sets no budget,
    and so runs on HAL's default, is retried once at ``_BUDGET_RETRY_TOKENS``
    when that default runs out; setting ``max_tokens`` at all, even to HAL's
    default, turns that retry off. A caller who knows its prompt is long sets
    the budget, and a timeout that can hold the answer, up front.
    """
    eff_prompt = prompt + "\n\nRespond with strict JSON only." if json_mode else prompt
    # AN EXPLICIT PIN IMPLIES no_failover, and this is the line that makes the
    # whole thing work. Without it the ladder SILENTLY substitutes whatever rung
    # it can reach and returns ok=True, so a caller asking for Astra gets gemini
    # and a success code — there is no refusal left to detect and no fallback
    # can ever fire. Pinning a model means wanting THAT model; a substitute
    # wearing its name is the failure, not the recovery.
    #
    # Callers that pass no model are untouched: the ladder cascades as before.
    pin = bool(model)
    call_kwargs: dict = {"no_failover": no_failover or pin}
    if timeout_s is not None:
        call_kwargs["timeout_s"] = timeout_s
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens
    result = _grip_infer_call(eff_prompt, model, audit, **call_kwargs)
    result = _retry_with_budget(result, eff_prompt, model, audit, call_kwargs)
    # A pin the ladder cannot serve is not a dead end: the gateway can. Keyed on
    # HAL's own explicit refusal codes (a pinned bare id is unknown_model since
    # HAL#649), so nothing else changes route.
    if model and _error_code(result) in _PIN_REFUSALS:
        # The caller's budget goes along, and only when set, so an unbudgeted
        # call reaches the gateway exactly as before.
        budget = {} if max_tokens is None else {"max_tokens": max_tokens}
        gw = _gateway_call(
            eff_prompt, model, _GRIP_INFER_TIMEOUT_S if timeout_s is None else timeout_s,
            **budget)
        if gw is not None:
            result = gw
    reason = _no_answer_reason(result)
    if reason:
        _voice_no_answer(result, model, pin, reason)  # ``None`` alone cannot say why
        return None
    if json_mode and not _looks_like_json(result["text"]):
        return None  # preserve json contract → fall back to response_format path
    return result


def _looks_like_json(text: str) -> bool:
    s = (text or "").strip()
    return s[:1] in ("{", "[")


def _parse_hal_decision(result: Optional[dict], *, audit: bool) -> Optional["Decision"]:
    """Parse a delegated-decision anatomy from a HAL ``/api/infer`` result, if present.

    THE one GRIP-side chokepoint where a delegated decision crosses from HAL into a
    ``HALResult`` (Session A — Decision Anatomy). Contract, in order:

      * Only the DELEGATED path carries anatomy. ``audit=False`` (non-audited internal
        inference) returns None — a non-delegated call has no decision to surface.
      * Surface ONLY when HAL actually emits a ``decision`` mapping. Today
        ``grip_infer`` emits none, so this returns None and the field is byte-identical
        to the pre-wire shape; it activates the moment HAL starts emitting it.
      * Validated through ``lib.decision_record.from_model_dict`` — the SMT gate +
        anatomy/honesty invariants applied here, never per-callsite. A surface-graft
        or self-certifying anatomy is REJECTED, not surfaced.
      * Fail-open + NEVER-FABRICATE: any absence / wrong type / DecisionError resolves
        to None. A model-down or malformed path must not invent a decision the model
        did not make (mirrors the provenance + IDR ``_HAL_DOWN_REFUSAL`` honesty anchor).
        Telemetry must never break the syscall, so this swallows by construction.
    """
    if not audit or not isinstance(result, dict):
        return None
    raw = result.get("decision")
    if not isinstance(raw, dict):
        return None
    try:
        from lib.decision_record import DecisionError, from_model_dict
    except ImportError:  # non-GRIP context — no validator, surface nothing
        return None
    try:
        return from_model_dict(raw)
    except DecisionError:
        # The decision is inadmissible (surface graft) or self-certifying — the
        # never-fabricate contract: surface NOTHING rather than a junk Decision.
        return None
    # A non-DecisionError (e.g. a real validator bug) is NOT a malformed-decision
    # case — it PROPAGATES so the breakage is observable, never silently swallowed
    # into a fake decision=None (AGORA #3636 Speaker C, valid concern).


def _query_hal_server(prompt: str, max_tokens: int, json_mode: bool) -> Optional[str]:
    """Route through HAL HTTP server (port 3850). Returns text or None."""
    if _backoff.is_blocked("hal"):
        return None
    try:
        payload: dict = {"prompt": prompt, "provider": "fallback"}
        if json_mode:
            payload["json_mode"] = True
        resp = _post_json(f"{_HAL_BASE}/api/query", payload, {}, _HAL_TIMEOUT_S)
        return resp.get("text") or resp.get("response")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _backoff.block("hal")
        return None
    except Exception as exc:
        logger.debug("HAL server query failed: %s", exc)
        return None


def _query_direct(
    provider: str,
    base_url: str,
    keychain_service: str,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> Optional[str]:
    """Direct provider call via OpenAI-compatible API."""
    if _backoff.is_blocked(provider):
        return None

    api_key = _keychain(keychain_service)
    if not api_key:
        logger.debug("HALLLMAdapter: no key for %s", provider)
        return None

    headers = {"Authorization": f"Bearer {api_key}"}
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    try:
        resp = _post_json(f"{base_url}/chat/completions", body, headers, _DIRECT_TIMEOUT_S)
        content = resp["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _backoff.block(provider)
            logger.warning("HALLLMAdapter: 429 from %s — backed off %.0fs", provider, _RATE_LIMIT_BACKOFF_S)
        return None
    except Exception as exc:
        logger.debug("Direct %s query failed: %s", provider, exc)
        return None
    # Legacy/direct path: shared extractor so primary + legacy never key-drift (AGORA #3444 C).
    tin, tout = _usage_tokens(resp.get("usage") if isinstance(resp, dict) else None)
    _record_usage(
        provider, model,
        input_tokens=tin, output_tokens=tout,
        audited=False, caller="legacy_direct", path="legacy",
    )
    return content


# Direct provider chain: (provider_name, base_url, keychain_service, model)
# Extend by appending a tuple — no other code changes needed.
#
# CLASSIFICATION (agentic-hygiene baked-literal sweep, 2026-05-19): this is
# a DELIBERATE fail-safe roster, NOT stale defaults. The models are
# intentionally the cheap/most-available SKU per provider for a last-resort
# chain. Do NOT route these through hal_model_resolver: the shim resolves
# family -> catalogue-PRIMARY, which would silently UPGRADE the fail-safe
# (e.g. gemini -> frontier gemini-3.1-pro-preview) — the exact bug the
# lib/providers/gemini.py ruling avoids. The catalogue is authoritative
# only over the *validity* of the catalogue-listed entries (groq, gemini),
# asserted by tests/test_hal_llm_adapter_fallback_roster.py. The xAI rung was
# the literal `grok-3` until 2026-11-24, when a probe showed xAI answers that
# retired id with grok-4.3 (the reply's model field); it now names grok-4.3,
# so the rung calls the same model and says so.
_DIRECT_PROVIDERS: list[tuple[str, str, str, str]] = [
    ("groq",   "https://api.groq.com/openai/v1",                         "grip-groq",   "openai/gpt-oss-120b"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "grip-gemini", "gemini-3.5-flash-lite"),
    ("xai",    "https://api.x.ai/v1",                                     "grip-xai",    "grok-4.3"),
]


def _legacy_chain(prompt: str, max_tokens: int, temperature: float, json_mode: bool) -> Optional[str]:
    """Fail-safe fallback: HAL HTTP server then the direct provider chain.

    Returns text, or None if the entire legacy chain is exhausted (the caller
    then returns the neutral sentinel).
    """
    result = _query_hal_server(prompt, max_tokens, json_mode)
    if result:
        return result
    for provider, base_url, keychain_service, model in _DIRECT_PROVIDERS:
        result = _query_direct(
            provider=provider,
            base_url=base_url,
            keychain_service=keychain_service,
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )
        if result:
            return result
    return None


def _route_text(prompt: str, *, max_tokens: int, temperature: float, json_mode: bool) -> str:
    """Shared routing core for generate()/chat(): grip_infer primary → legacy
    fail-safe chain → neutral sentinel. Always returns a str (never raises).
    """
    primary = _query_grip_infer(prompt, json_mode=json_mode)
    if primary:
        return primary["text"]
    legacy = _legacy_chain(prompt, max_tokens, temperature, json_mode)
    if legacy:
        return legacy
    logger.error("HALLLMAdapter: all providers exhausted — returning neutral sentinel")
    return _NEUTRAL_JSON if json_mode else ""


def _messages_to_prompt(messages: list[dict]) -> str:
    """Flatten a chat message list to a single prompt (KISS — no template engine)."""
    if len(messages) == 1 and messages[0].get("role") == "user":
        return str(messages[0].get("content", ""))
    return "\n".join(f'{m.get("role", "user")}: {m.get("content", "")}' for m in messages)


class HALLLMAdapter:
    """GRIP's canonical AI syscall — LlamaEngine-compatible, RAILLM-backed.

    Drop-in anywhere LlamaEngine is used (PrincipledScorer, meta_inference_daemon,
    the guardrail-evasion L2 layer). The public surface is intentionally stable:
    callers are byte-unchanged across the #2722 redirect.
    """

    def connect(self) -> "HALLLMAdapter":
        """No-op — HAL adapters need no warm-up. Returns self for chaining."""
        return self

    def generate(
        self,
        prompt: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = _DEFAULT_TEMPERATURE,
        json_mode: bool = False,
    ) -> str:
        """Generate text via the RAILLM primary, legacy fail-safe on miss.

        Returns the neutral JSON sentinel on total chain exhaustion so callers
        using json_mode never get parse failures. Signature unchanged (#2722).
        """
        return _route_text(
            prompt, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode
        )

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = _DEFAULT_TEMPERATURE,
        timeout: Optional[float] = None,  # accepted for caller compat; see below
        json_mode: bool = False,
    ) -> str:
        """Chat-style entrypoint (messages in, text out). Used by the
        guardrail-evasion L2 semantic layer. Routes through the same core as
        generate(); returns a str (never raises).
        """
        # `timeout` is accepted but not honoured: the bound lives inside
        # _query_grip_infer (_GRIP_INFER_TIMEOUT_S) and threading a per-call
        # value through _route_text and the legacy chain is a wider change than
        # this parameter justifies while no caller passes one. Until then it is
        # at least not SILENT — a caller asking for 5s and getting 45 should
        # find out from the logs rather than from a stopwatch. See
        # declared-input-must-reach-the-actor.md.
        if timeout is not None:
            logger.warning(
                "HALLLMAdapter.chat: timeout=%s was passed but is not honoured; "
                "the effective bound is _GRIP_INFER_TIMEOUT_S=%ss",
                timeout, _GRIP_INFER_TIMEOUT_S,
            )
        return _route_text(
            _messages_to_prompt(messages),
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )

    def infer(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        audit: bool = True,
        no_failover: bool = False,
        timeout_s: Optional[float] = None,
    ) -> HALResult:
        """Policy-gated audited inference (#2722 G3, policy 2026-05-16).

        Returns a typed HALResult so callers on the audited IDR path cannot
        silently emit an un-audited answer.

        audit=True  (the audited IDR path) — fail-CLOSED on HAL-down:
            Returns HALResult(text=None, hal_down=True, audited=False).
            Caller MUST check result.must_refuse() before emitting any output.

        audit=False (non-audited internal inference) — fail-DEGRADED:
            Legacy chain answer wrapped with degraded=True, audited=False,
            reason="HAL_DOWN" + one WARN log + idr-gap telemetry marker.
            Caller can use the answer but MUST NOT present it as audited.

        audit=False with no_failover=True — the caller wants this model or
            nothing, so the legacy chain (a different model) never runs:
            HALResult(text="", audited=False, reason="NOT_SERVED").

        timeout_s bounds the read on the primary ``/api/infer`` hop (its one
            budget retry and the gateway fallback inherit it too — see
            ``_query_grip_infer``). Forwarded to ``_query_grip_infer`` ONLY
            when set, so an existing caller sends the exact same request it
            always did and the module default (``_GRIP_INFER_TIMEOUT_S``,
            45s) applies. A council seat reading an 8,000-character target
            (GRIP#9187, /ultraread) can need longer than that default to
            answer.

        Happy path (HAL up): HALResult(text=..., audited=audit).
        """
        query_kwargs: dict = {"model": model, "audit": audit, "no_failover": no_failover}
        if timeout_s is not None:
            query_kwargs["timeout_s"] = timeout_s
        primary = _query_grip_infer(prompt, **query_kwargs)
        if primary:
            return HALResult(
                text=primary.get("text", ""),
                audited=audit,
                provider=primary.get("provider"),
                model=primary.get("model"),
                idr_ref=primary.get("idr_ref"),
                # Delegated-decision anatomy (Session A): surfaced ONLY when HAL
                # emits a ``decision`` payload AND this is the delegated/audit
                # path. Parsed through the lib.decision_record chokepoint (SMT
                # gate + honesty validate) and fail-open to None — a malformed
                # or absent payload NEVER fabricates a Decision the model did
                # not make (mirrors the HAL-down honesty anchor). Today HAL's
                # /api/infer carries no ``decision`` key, so this resolves to
                # None and the field stays byte-identical to pre-wire; it
                # activates the moment HAL starts emitting the anatomy.
                decision=_parse_hal_decision(primary, audit=audit),
            )

        # HAL is unreachable — apply the operator-decided policy gate
        if audit:
            logger.warning(
                "HALLLMAdapter: HAL unreachable on audit=True call — "
                "refusing (fail-CLOSED). The audited IDR guarantee cannot be met."
            )
            return _HAL_DOWN_REFUSAL

        # The legacy chain is the failover a no_failover caller refused. Run
        # anyway, it filled an /ultrajudge seat pinned to one model with
        # another model's answer, counted under the pinned name (2026-10-05).
        if no_failover:
            logger.warning("HALLLMAdapter: %s not served and no_failover=True — no answer", model)
            return HALResult(text="", audited=False, reason="NOT_SERVED")

        # audit=False — fail-DEGRADED: serve but mark explicitly
        logger.warning(
            "HALLLMAdapter: HAL unreachable on audit=False call — degraded mode"
        )
        _write_degraded_marker(prompt)
        legacy = _legacy_chain(prompt, _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE, False)
        return HALResult(
            text=legacy or (_NEUTRAL_JSON),
            audited=False,
            degraded=True,
            reason="HAL_DOWN",
        )

    def close(self) -> None:
        """No-op — no resources to release."""

    @property
    def loaded(self) -> bool:
        """Always True — HAL adapters are always ready."""
        return True

    def provider_status(self) -> dict:
        """Current backoff status for all legacy providers."""
        return _backoff.status()

    def __repr__(self) -> str:
        blocked = list(_backoff.status().keys())
        return f"HALLLMAdapter(transport=hal-server/api/infer, blocked={blocked or 'none'})"


# Canonical public alias — guardrail_evasion_llm.py and other callers import
# `LlamaEngine` as the stable AI-syscall name (syscall-doctrine §AI syscall).
LlamaEngine = HALLLMAdapter


# ---------------------------------------------------------------------------
# infer_with_tools — module-level syscall (NOT a HALLLMAdapter method).
# Deliberately a free function: the tool-loop is stateless on the GRIP side
# (no backoff bookkeeping; the entire fail-safe lives server-side in HAL's
# ``/api/infer-with-tools`` route). Living as a module-level function keeps
# the adapter class lean (ISP) and matches syscall-doctrine — every external
# boundary call is a shim, this one is the AI-tool-loop shim.
# ---------------------------------------------------------------------------


def infer_with_tools(
    prompt: str,
    *,
    sandbox_root: str,
    tools: Optional[list] = None,
    model: Optional[str] = None,
    max_iterations: int = 8,
    system: Optional[str] = None,
    max_tokens: int = 4096,
) -> HALToolResult:
    """Drive an Anthropic Messages tool-loop on HAL's CCH subscription
    with a capability-locked, sandbox-bound Read/Grep/Glob dispatcher.

    The model can call Read/Grep/Glob to inspect files under
    ``sandbox_root``, but cannot Bash/Write/Edit anything — those names
    are rejected by the dispatcher's hardcoded allowlist on the HAL
    side. ``tools`` is an optional subset of ``{"Read", "Grep", "Glob"}``;
    omit it to allow all three. Any forbidden name in the caller's list
    is filtered server-side BEFORE schemas reach the model (defence-
    in-depth — the dispatcher itself re-checks per tool_use).

    ``sandbox_root`` MUST be an existing directory the operator trusts
    the model to read from. The HAL side rejects ``/``, ``/etc``,
    ``/bin``, ``/sbin``, ``/root``, etc., AND symlinks resolving into
    those — fail-CLOSED on bad root.

    Returns :class:`HALToolResult`. Callers gating on audit should call
    :meth:`HALToolResult.must_refuse` BEFORE emitting any output —
    that returns True on HAL-down AND on server-side ok=false (sandbox
    refusal, no allowed tools, CCH unavailable, loop crash). The
    transport is fail-CLOSED, not fail-DEGRADED: there is no legacy
    provider chain for tool loops — only HAL drives the loop because
    only HAL holds the CCH OAuth seat and the SandboxedDispatcher.
    """
    response = _infer_with_tools_call(
        prompt,
        sandbox_root=sandbox_root,
        tools=tools,
        model=model,
        max_iterations=max_iterations,
        system=system,
        max_tokens=max_tokens,
    )
    if response is None:
        return _HAL_TOOLS_DOWN_REFUSAL
    tool_calls = [
        HALToolCall(
            tool=str(tc.get("tool") or ""),
            input=tc.get("input") if isinstance(tc.get("input"), dict) else {},
            # error_default=True — fail-CLOSED on type confusion.
            # A malformed response with ``is_error: "false"`` would
            # silently flip to True under ``bool()``. Treat as error.
            is_error=_strict_bool(tc.get("is_error"), error_default=True),
            iteration=int(tc.get("iteration") or 0),
        )
        for tc in (response.get("tool_calls") or [])
    ]
    return HALToolResult(
        # error_default=False on ``ok`` — fail-CLOSED: type confusion
        # is treated as failure, never as success.
        ok=_strict_bool(response.get("ok"), error_default=False),
        text=str(response.get("text") or ""),
        tool_calls=tool_calls,
        iterations=int(response.get("iterations") or 0),
        # error_default=False — hit_iteration_cap False on bad type
        # avoids spuriously asserting the loop terminated cleanly.
        hit_iteration_cap=_strict_bool(
            response.get("hit_iteration_cap"), error_default=False,
        ),
        hal_down=False,
        error_code=response.get("error_code"),
        error_message=response.get("error_message"),
    )


# ---------------------------------------------------------------------------
# hear — module-level multimodal-audio syscall (NOT a HALLLMAdapter method).
# Like ``infer_with_tools``, a deliberate free function: hearing is stateless
# on the GRIP side (no backoff bookkeeping; HAL owns the provider ladder), so
# living module-level keeps the adapter class lean (ISP) and matches
# syscall-doctrine — every external boundary call is a shim; this one is the
# audio-in shim (HAL #414).
# ---------------------------------------------------------------------------


def hear(
    media_path: str,
    *,
    prompt: str = "",
    model: str = "gemini/gemini-3.5-flash-lite",
) -> Optional[str]:
    """Route an audio file through HAL's ``/api/infer`` media path (HAL #414)
    and return the model's grounded description.

    HAL reads + base64-encodes the file server-side and dispatches via an
    audio-capable provider, so GRIP gets failover + audit + provider-agnostic
    routing for sound.

    Returns the description text on success, or ``None`` — a TYPED miss — on
    ANY failure (HAL unreachable, ok=false, or empty text).

    It NEVER degrades to the text-only legacy chain: that chain cannot carry
    audio, so degrading would silently DROP the sound (the exact bug HAL #414
    fixed). Audio has no meaningful text fallback, so on a miss the caller
    decides the next rung (e.g. a local model, then a direct REST call).
    ``audit=False`` — the catalogue/hearing path is not on the audited IDR surface.
    """
    result = _grip_infer_call(
        prompt, model, audit=False,
        media=[str(media_path)],
        timeout_s=_HEAR_TIMEOUT_S,
    )
    if result and result.get("ok") and (result.get("text") or "").strip():
        return result["text"]
    logger.warning(
        "hear: HAL multimodal path miss for %s (unreachable / ok=false / empty) "
        "— returning None (typed miss); NOT degrading to the text-only legacy "
        "chain (would drop the audio)",
        media_path,
    )
    return None
