"""
Gemini LLM-as-Judge -- Tier 3 calibration for MoE cascade.

Called only when:
  - Tier 1 (static AST) and Tier 2 (local Llama/Haiku) disagree by > 15 points
  - Scores are borderline (65-80 composite)
  - Explicit calibration requested

Uses Google Gemini API with key from macOS Keychain (grip-gemini).
Minimal token usage: sends dimension scores + diff summary, not full diff.
The prompt passes the redaction shield (lib/review_redaction.py) first; when
the shield cannot run, nothing is sent and the call returns None.

Usage:
    from lib.gemini_judge import gemini_calibrate, check_gemini_available
    if check_gemini_available():
        result = gemini_calibrate(tier1_scores, tier2_scores, diff_summary)
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Optional

# Keychain service name for the Gemini credential
_KEYCHAIN_SERVICE = "grip-gemini"


@dataclass
class JudgeVerdict:
    """Result from Gemini LLM-as-Judge calibration."""
    provider: str = "gemini-judge"
    calibrated_scores: dict = field(default_factory=dict)
    reasoning: str = ""
    confidence: int = 0
    disagreements: list = field(default_factory=list)
    triggered_by: str = ""  # "disagreement", "borderline", "explicit"


def _get_gemini_key() -> Optional[str]:
    """Fetch Gemini API key from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _shield(value):
    """``value`` scrubbed by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing."""
    try:
        from review_redaction import shield_payload
    except ImportError:
        try:
            from lib.review_redaction import shield_payload
        except ImportError as exc:  # no shield, no send: callers treat OSError as a failed send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_payload(value)


def check_gemini_available() -> bool:
    """Check if Gemini API is available for calibration."""
    return _get_gemini_key() is not None


def should_trigger_judge(
    tier1_composite: int,
    tier2_composite: int,
    threshold_disagree: int = 15,
    threshold_borderline: tuple = (65, 80),
) -> tuple[bool, str]:
    """Determine if Gemini judge should be triggered.

    Returns (should_trigger, reason).
    """
    if abs(tier1_composite - tier2_composite) > threshold_disagree:
        return True, "disagreement"

    lo, hi = threshold_borderline
    for score, label in [(tier1_composite, "tier1"), (tier2_composite, "tier2")]:
        if lo <= score <= hi:
            return True, f"borderline-{label}"

    return False, ""


def _build_judge_prompt(
    tier1_scores: dict,
    tier2_scores: dict,
    diff_summary: str,
    pr_title: str = "",
) -> str:
    """Build the structured prompt for Gemini LLM-as-Judge."""
    return f"""You are an expert code reviewer acting as an LLM-as-Judge.

Two independent review tiers have scored this PR. Your task is to:
1. Identify which tier's scores are more accurate
2. Produce calibrated final scores
3. Flag specific disagreements

PR Title: {pr_title}

Tier 1 (Static AST Analysis) scores:
{json.dumps(tier1_scores, indent=2)}

Tier 2 (Local LLM Review) scores:
{json.dumps(tier2_scores, indent=2)}

Diff summary (first 2000 chars):
{diff_summary[:2000]}

Respond in JSON only:
{{
  "calibrated_scores": {{
    "quality": <0-100>,
    "alignment": <0-100>,
    "testing": <0-100>,
    "docs": <0-100>
  }},
  "reasoning": "<1-2 sentences>",
  "confidence": <50-100>,
  "disagreements": ["<specific disagreement 1>", ...]
}}"""


def gemini_calibrate(
    tier1_scores: dict,
    tier2_scores: dict,
    diff_summary: str,
    pr_title: str = "",
    trigger_reason: str = "",
    verbose: bool = False,
) -> Optional[JudgeVerdict]:
    """Call Gemini API to calibrate scores from Tier 1 and Tier 2.

    Returns JudgeVerdict or None if API call fails.
    """
    key = _get_gemini_key()
    if not key:
        if verbose:
            print("[gemini-judge] No API key available", flush=True)
        return None

    prompt = _build_judge_prompt(tier1_scores, tier2_scores,
                                 diff_summary, pr_title)

    try:
        import urllib.request

        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            "models/gemini-2.0-flash:generateContent"
            f"?key={key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 512,
                "responseMimeType": "application/json",
            },
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(_shield(payload)).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if verbose:
            print("[gemini-judge] Calling Gemini API...", flush=True)

        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)

        verdict = JudgeVerdict(
            calibrated_scores=parsed.get("calibrated_scores", {}),
            reasoning=parsed.get("reasoning", ""),
            confidence=parsed.get("confidence", 50),
            disagreements=parsed.get("disagreements", []),
            triggered_by=trigger_reason,
        )

        if verbose:
            print(f"[gemini-judge] Confidence: {verdict.confidence}%", flush=True)
            print(f"[gemini-judge] Reasoning: {verdict.reasoning}", flush=True)

        return verdict

    except Exception as e:
        if verbose:
            print(f"[gemini-judge] API call failed: {e}", flush=True)
        return None


def merge_with_judge(
    tier1_scores: dict,
    tier2_scores: dict,
    judge: JudgeVerdict,
) -> dict:
    """Merge Tier 1, Tier 2, and Judge scores into final calibrated scores.

    Judge confidence determines weight:
    - confidence >= 80: Judge 50%, T1/T2 split remaining
    - confidence 60-79: Judge 30%
    - confidence < 60: Judge 10% (mostly advisory)
    """
    if judge.confidence >= 80:
        w_judge, w_t1, w_t2 = 0.50, 0.25, 0.25
    elif judge.confidence >= 60:
        w_judge, w_t1, w_t2 = 0.30, 0.35, 0.35
    else:
        w_judge, w_t1, w_t2 = 0.10, 0.45, 0.45

    merged = {}
    for dim in ("quality", "alignment", "testing", "docs"):
        t1 = tier1_scores.get(dim, 0)
        t2 = tier2_scores.get(dim, 0)
        jd = judge.calibrated_scores.get(dim, (t1 + t2) // 2)
        merged[dim] = int(t1 * w_t1 + t2 * w_t2 + jd * w_judge)

    return merged
