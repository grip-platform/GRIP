"""HAL Council permanent judge — Gemini Pro 2.5, always fires.

The existing `lib/gemini_judge.py` runs Gemini as a Tier-3 calibrator only
when the council disagrees or scores are borderline. The HAL Council
reviewer needs Gemini as a permanent judge seat — it sees every PR, not
just the contested ones — so it can attach a calibrated final verdict
alongside the mechanical score and the council's 4-vote MoE output.

This module lives alongside the existing gemini_judge.py (rather than
patching it) so the Tier-3 calibration path stays unchanged. Downstream
`pre_review_pipeline.py` chooses which judge mode to invoke.

### The model pin

`gemini-3.8-flash` — EXPLICIT SKU. Never the `gemini-pro` alias.

Plan senior-infra gotcha §5 is emphatic: silent upgrades of the judge
model change verdicts without changing the prompt or diff. Reproducibility
at 90-day horizons depends on pinning the exact SKU.

Repointed 2026-11-24 (GRIP#9389): the previous pin, `gemini-2.5-pro-002`,
probed 404 on `generateContent` on 2026-11-24 and is absent from
`/v1beta/openai/models` and from every vendor page found — it was already
dead, meaning this judge seat had been silently returning no verdict
(`_call` swallows the 404 body as a `KeyError` and returns `""`). Google's
deprecations page says the bare alias `gemini-2.5-pro` auto-routes to
`gemini-3.8-flash` from 2026-12-01; that page publishes no dated snapshot
for 3.8 Flash (`Versions: Stable: gemini-3.8-flash`), so the stable id
itself is the explicit pin, not a rolling alias like `gemini-pro-latest`.

The pin is exposed as a module-level constant so tests can assert it
literally and future audits can grep for the SKU string.

### Dependency injection

`judge_permanent` accepts an injected callable for the Gemini call. The
default lazily imports from `lib.gemini_judge` so production code picks
up the real client, but tests inject fakes and stay hermetic (no API key,
no subprocess, no network).

Pure stdlib. No external deps.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# PARAMOUNT: pinned SKU. Never use "gemini-pro" alias — silent upgrades
# change verdicts. Plan senior-infra gotcha §5.
MODEL_PIN: str = "gemini-3.8-flash"

# Verdict vocabulary
VERDICT_APPROVE = "APPROVE"
VERDICT_CHANGES = "CHANGES_REQUESTED"
VERDICT_REJECT = "REJECT"
VALID_VERDICTS: frozenset[str] = frozenset({
    VERDICT_APPROVE, VERDICT_CHANGES, VERDICT_REJECT,
})


@dataclass
class PermanentJudgeVerdict:
    """Structured verdict from the permanent judge."""

    model_pin: str = MODEL_PIN
    calibrated_scores: dict = field(default_factory=dict)
    final_verdict: str = VERDICT_CHANGES
    confidence: int = 0  # 0-100
    reasons: list = field(default_factory=list)
    council_agreement_note: str = ""
    judge_error: Optional[str] = None


def _build_prompt(
    dimension_scores: dict,
    council_verdicts: list,
    diff_summary: str,
) -> str:
    """Render the permanent-judge prompt. Deterministic for reproducibility."""
    return (
        "You are the permanent judge for the HAL Council PR reviewer. Always "
        "produce a verdict — no tier-3 escalation logic.\n\n"
        f"Pinned model: {MODEL_PIN}\n\n"
        "Dimension scores (mechanical 0.45 + LLM 0.55, sum to 1.0):\n"
        f"{json.dumps(dimension_scores, indent=2, sort_keys=True)}\n\n"
        "Council verdicts (post-dedupe, family-corrected):\n"
        f"{json.dumps(council_verdicts, indent=2, default=str)}\n\n"
        "Diff summary:\n"
        f"{diff_summary}\n\n"
        "Output STRICT JSON (no prose) with exactly these keys:\n"
        "  - final_verdict: one of APPROVE | CHANGES_REQUESTED | REJECT\n"
        "  - calibrated_scores: {dimension: score} where scores are 0-1\n"
        "  - confidence: integer 0-100\n"
        "  - reasons: list of 1-5 short strings (each <=200 chars)\n"
        "  - council_agreement_note: short string summarising council alignment\n"
    )


def _default_gemini_call() -> Optional[Callable[[str, str], str]]:
    """Lazy import of the real Gemini client. Isolated so tests inject."""
    try:
        from lib.gemini_judge import _get_gemini_key  # noqa: F401
    except ImportError:
        return None

    def _call(model: str, prompt: str) -> str:
        from lib.gemini_judge import _get_gemini_key, _shield  # lazy
        import subprocess  # lazy
        try:
            request = json.dumps(_shield({"contents": [{"parts": [{"text": prompt}]}]}))
        except OSError:  # a failed scrub, or no shield to load: nothing is sent
            return ""
        key = _get_gemini_key()
        if not key:
            return ""
        cmd = [
            "curl", "-s",
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            "-H", f"x-goog-api-key: {key}",
            "-H", "Content-Type: application/json",
            "-d", request,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""
        if proc.returncode != 0:
            return ""
        try:
            body = json.loads(proc.stdout)
            return body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, json.JSONDecodeError):
            return ""

    return _call


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    return text


def _parse_verdict(raw: str) -> Optional[dict]:
    """Parse Gemini response into a dict. Returns None on failure."""
    text = _strip_json_fence(raw)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _coerce_verdict(data: dict) -> PermanentJudgeVerdict:
    """Shape a raw-parsed dict into a PermanentJudgeVerdict."""
    final = str(data.get("final_verdict", "")).strip().upper()
    if final not in VALID_VERDICTS:
        final = VERDICT_CHANGES
    confidence_raw = data.get("confidence", 0)
    try:
        confidence = max(0, min(100, int(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0
    return PermanentJudgeVerdict(
        model_pin=MODEL_PIN,
        calibrated_scores=data.get("calibrated_scores", {}) or {},
        final_verdict=final,
        confidence=confidence,
        reasons=list(data.get("reasons", []) or []),
        council_agreement_note=str(data.get("council_agreement_note", "") or ""),
    )


def judge_permanent(
    dimension_scores: dict,
    council_verdicts: list,
    diff_summary: str,
    *,
    gemini_call: Optional[Callable[[str, str], str]] = None,
) -> PermanentJudgeVerdict:
    """Always-fires permanent judge. Returns calibrated verdict from Gemini Pro.

    Pinned to `gemini-3.8-flash`. Never raises — errors become
    `judge_error` on the returned verdict. Callers downstream of this
    function MUST check `judge_error` before trusting `final_verdict`.

    Args:
        dimension_scores: 8-dim score dict (mechanical 0.45 + LLM 0.55)
        council_verdicts: 4 council member verdicts post family-dedupe
        diff_summary: short (~200-800 char) summary of PR changes
        gemini_call: injected callable `(model, prompt) -> str`. If None,
            uses the real Gemini client lazily (hermetic tests inject).

    Returns:
        PermanentJudgeVerdict. On failure, `judge_error` is set and
        `final_verdict` defaults to `CHANGES_REQUESTED` (conservative).
    """
    prompt = _build_prompt(dimension_scores, council_verdicts, diff_summary)
    call = gemini_call or _default_gemini_call()
    if call is None:
        return PermanentJudgeVerdict(judge_error="gemini_client_unavailable")
    try:
        raw = call(MODEL_PIN, prompt)
    except Exception as exc:  # noqa: BLE001 — never crash caller
        return PermanentJudgeVerdict(judge_error=f"gemini_call_failed:{type(exc).__name__}")
    if not raw:
        return PermanentJudgeVerdict(judge_error="empty_response")
    data = _parse_verdict(raw)
    if data is None:
        return PermanentJudgeVerdict(judge_error="invalid_json")
    return _coerce_verdict(data)


__all__ = [
    "MODEL_PIN",
    "VALID_VERDICTS",
    "VERDICT_APPROVE",
    "VERDICT_CHANGES",
    "VERDICT_REJECT",
    "PermanentJudgeVerdict",
    "judge_permanent",
]
