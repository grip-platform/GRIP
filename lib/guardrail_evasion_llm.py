"""Layer 2: LLM semantic inference on top of the mechanical regex gate.

Architecture (operator-directed 2026-05-15, "add an LLM layer inference
on top of the mechanical REGEX guardrail"):

    L1  lib/guardrail_evasion_detector.evaluate()   — regex, deterministic,
        runs always, ~0ms. DENY here short-circuits (no LLM).
    L2  this module                                 — semantic, catches
        obfuscation L1's keyword/secret-shape regex cannot (char-code or
        base64 identifier construction, getattr/__import__ assembly,
        euphemistic intent that dodges the keyword list).

Discipline (non-negotiable for a PreToolUse gate):
  * Pre-filter — the LLM is called ONLY when content is plausibly
    obfuscation-shaped. The vast majority of Write/Edit never hits the
    model (latency + cost). L1 already handles the obvious cases.
  * FAIL-OPEN — any LLM error/timeout/parse-failure/unavailability
    returns None (allow). L1 is the mechanical floor; L2 is additive
    recall. A safety layer that hangs or blocks the dev loop when the
    model is down is itself a defect.
  * BUDGET — the model call gets _LLM_BUDGET_S seconds and is abandoned
    after that (fail-open). The hook that runs this module has its own
    timeout, and since GRIP#7930 a hook that times out BLOCKS the edit, so
    an unbounded model call turned fail-open into fail-closed. The adapter
    does not honour a per-call timeout, so the budget is enforced here, on
    a daemon thread that cannot hold the process open after it answers.
  * Cache — keyed by sha256(content); PreToolUse is a fresh process per
    call, so an on-disk cache avoids re-calling the model on tool
    retries / identical edits.
  * DIP — the model caller is injected (default routes through the
    canonical HAL AI syscall). Tests inject a mock; no network in tests.
    (Applying this session's own SOLID-DIP lesson.)

Pure-ish module: the only side effect is the bounded JSON cache.
Tested by tests/test_guardrail_evasion_llm.py (HAL mocked).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable

from lib.guardrail_evasion_detector import Verdict, _is_exempt, evaluate

_CACHE = Path.home() / ".claude" / "state" / "guardrail-evasion-llm-cache.json"
_CACHE_TTL_S = 7 * 24 * 3600
_CACHE_MAX = 500
#: Seconds the model call may take before L2 gives up and defers to L1. It
#: must sit well inside the binding's timeout in config/hooks.registry.json
#: (5 s), because interpreter start-up, imports, L1 and the cache need time
#: too; tests/test_guardrail_evasion_llm.py pins that headroom. Measured
#: 2026-10-13 on Windows with no HAL server: the old 8 s bound, which the
#: adapter does not honour anyway, ran to 8.4 s, and every escalated edit was
#: refused.
_LLM_BUDGET_S = 3.0

# Pre-filter: obfuscation *shapes* L1's keyword/secret regex may miss.
# Conservative — escalate to the model only on a plausible signal.
_SUSPICION = re.compile(
    r"chr\s*\(|ord\s*\(|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}"
    r"|base64|b64decode|codecs\.decode|bytes\.fromhex|fromhex\s*\("
    r"|\.decode\s*\(|unhexlify|rot13|getattr\s*\([^,]+,\s*['\"]"
    r"|__import__\s*\(|\beval\s*\(|\bexec\s*\("
    r"|(['\"][A-Za-z0-9_\-]{1,20}['\"]\s*\+\s*){2,}"  # 3+ literal concat chain
    r"|#.*\b(scanner|guardrail|guard|gate|hook|lint|sentinel|secret|env\s*var|"
    r"detection|protection|flagged|caught|whitelist|allowlist)\b",
    re.IGNORECASE,
)

_PROMPT = (
    "You are a security gate. Decide if the following code edit is "
    "OBFUSCATING ITSELF to evade a safety/secret scanner, linter, or "
    "guardrail (e.g. building an identifier from char codes/base64/split "
    "fragments, dynamic getattr/import to hide a name, or a comment whose "
    "intent is to dodge a scanner). Legitimate dynamic code, normal string "
    "building, and merely *discussing* scanners are NOT violations. "
    "Respond with ONE line of strict JSON, no prose: "
    '{\"verdict\": \"ALLOW\" or \"DENY\", \"reason\": \"<=20 words\"}.\n\n'
    "FILE: {path}\nEDIT:\n```\n{content}\n```"
)


def _hal_caller(prompt: str) -> str:
    """Default model caller — routes through the canonical HAL AI syscall.

    Lazy import so an absent/broken HAL fails open (returns "") rather than
    breaking the gate. Never raises.
    """
    try:
        from lib.hal_llm_adapter import LlamaEngine  # canonical AI syscall

        eng = LlamaEngine()
        out = eng.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=120,
            timeout=_LLM_BUDGET_S,
        )
        return out if isinstance(out, str) else str(out or "")
    except Exception:
        return ""


def _load_cache() -> dict:
    try:
        data = json.loads(_CACHE.read_text())
        now = time.time()
        return {k: v for k, v in data.items() if now - v.get("ts", 0) < _CACHE_TTL_S}
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        if len(cache) > _CACHE_MAX:
            items = sorted(cache.items(), key=lambda kv: kv[1].get("ts", 0))
            cache = dict(items[-_CACHE_MAX:])
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(cache))
    except Exception:
        pass


def _parse_verdict(raw: str) -> Verdict | None:
    """Strict-ish parse of the model's JSON line. Unparseable -> None (open)."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    verdict = str(obj.get("verdict", "")).strip().upper()
    if verdict == "DENY":
        reason = str(obj.get("reason", "obfuscation to evade a safety scanner"))
        return Verdict(
            allowed=False,
            signal="llm-semantic",
            evidence=reason[:160],
            reason=(
                f"LLM semantic layer flagged guardrail evasion: {reason[:160]}. "
                f"Fix the root cause; never obfuscate to slip past a safety "
                f"control. See rules/no-guardrail-evasion.md."
            ),
        )
    return None  # ALLOW or anything non-DENY -> defer to L1's allow


def _outbound(content: str, file_path: str) -> str | None:
    """The edit as the outside model may see it, or None when it must stay here.

    The default caller is an outside model (HAL routes it to Groq). Only a file
    inside a GRIP checkout goes to it (lib/grip_checkout.py), and its text passes
    the redaction shield whole, before any cut. Without either, nothing leaves.
    """
    try:
        from lib.grip_checkout import grip_checkout_root
        from lib.review_redaction import WITHHELD, redact
    except Exception:  # noqa: BLE001 -- no boundary or no shield: the edit stays here
        return None
    if not file_path or grip_checkout_root(file_path) is None:
        return None
    scrubbed = redact(content)
    return None if scrubbed == WITHHELD else scrubbed


def _call_within_budget(
    fn: Callable[[str], str], prompt: str, budget_s: float
) -> str | None:
    """Return ``fn(prompt)``, or None when it raised or overran ``budget_s``.

    The call runs on a daemon thread, so an abandoned call cannot hold the
    process open after the gate has answered.
    """
    answer: dict[str, str | None] = {"raw": None}

    def _run() -> None:
        try:
            answer["raw"] = fn(prompt)
        except Exception:  # noqa: BLE001 -- fail-open: a model error is no opinion
            answer["raw"] = None

    worker = threading.Thread(target=_run, name="guardrail-evasion-l2", daemon=True)
    worker.start()
    worker.join(budget_s)
    return None if worker.is_alive() else answer["raw"]


def evaluate_llm(
    content: str, file_path: str = "", caller: Callable[[str], str] | None = None
) -> Verdict | None:
    """L2 only. Returns a DENY Verdict, or None (no opinion -> allow).

    None when: exempt, not suspicious, a default-caller edit that may not leave
    this machine, cache says allow, or any failure, including a model call that
    overran the budget (fail-open). Never raises. L1 still judges every edit,
    wherever it lives.
    """
    if not content or _is_exempt(file_path):
        return None
    if not _SUSPICION.search(content):
        return None
    text = content if caller is not None else _outbound(content, file_path)
    if text is None:
        return None

    key = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()
    cache = _load_cache()
    if key in cache:
        if cache[key].get("deny"):
            return _parse_verdict(cache[key].get("raw", ""))
        return None

    prompt = (_PROMPT.replace("{path}", file_path or "<unknown>")
              .replace("{content}", text[:4000]))
    raw = _call_within_budget(caller or _hal_caller, prompt, _LLM_BUDGET_S)
    if raw is None:
        return None  # fail-open, and not cached: the model gave no answer

    verdict = _parse_verdict(raw)
    cache[key] = {"ts": time.time(), "deny": verdict is not None, "raw": raw or ""}
    _save_cache(cache)
    return verdict


def combined_evaluate(
    content: str, file_path: str = "", caller: Callable[[str], str] | None = None
) -> Verdict:
    """The gate's full decision: L1 (regex) then, only if L1 allows, L2 (LLM).

    L1 DENY short-circuits — no model call. L2 can only ADD a denial on
    top of an L1 allow; it never overturns an L1 deny and never relaxes.
    """
    l1 = evaluate(content, file_path)
    if not l1.allowed:
        return l1
    l2 = evaluate_llm(content, file_path, caller)
    return l2 if l2 is not None else l1
