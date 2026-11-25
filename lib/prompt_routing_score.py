"""Jev scores how much execution a prompt needs, in shadow beside the router's heuristic.

The Jev integration plan, Phase 2b.3 (V>>'s D-2): lib/prompt_routing.py recommends
one of four ordered execution patterns (Direct, Focused, Sprint, FullMesh) from a
heuristic. A decision model (TypeSafe's Jev, through HAL decide) rates the same
prompt as a Score over those four levels. Below ASK_BELOW confidence the reading
is "ask", which the plan maps to the router's existing A/B/C question rather than
a guess. In shadow: the router's output is unchanged, and each reading goes to
logs/prompt-routing-shadow.jsonl beside the heuristic's pick and any pattern the
operator stated, so agreement and overrides can be measured before anything
changes.

Off by default since 17 October 2026; requires `GRIP_PROMPT_ROUTING_SCORE=1`.
See docs/jev-shadows.md for the measurements and retained reporting.

Privacy: this is the operator's own prompt, so more is kept at home than the
shared floor requires. The decision-model privacy floor (lib/decision_privacy.py)
runs first: nothing from the guardian repository, nothing the redaction shield
would change. Then, because the shield only knows listed names, a prompt is also
kept at home when it is long or carries a pasted-content marker (pasted material
is more often someone else's), or when it mentions family, legal or WhatsApp
matters. The log never holds the prompt.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB.parent) not in sys.path:
    sys.path.insert(0, str(_LIB.parent))

#: The router's patterns, lowest effort first (lib/prompt_routing.PATTERNS).
LEVELS = ("Direct", "Focused", "Sprint", "FullMesh")
_LEVEL_TEXT = (
    "Direct: one quick action or answer, done in place",
    "Focused: one bounded task of a few steps",
    "Sprint: several related tasks, planned and done in sequence",
    "FullMesh: a large effort that fans out to agents working in parallel",
)
_QUESTION = "How much execution effort does this request to a coding agent need?"
#: Below this confidence the reading is "ask": the router's A/B/C question, not a guess.
ASK_BELOW = 0.5
#: Longer prompts stay at home: they are mostly pasted material.
STATE_LIMIT = 4000
_PASTED = ("<pasted_content", "[Pasted text")
_PERSONAL = re.compile(
    r"\b(divorce|custody|mediat\w*|parenting|maintenance|attorney|advocate|lawyer|court|"
    r"child\w*|son|daughter|wife|husband|family|school|teacher|testament|guardian|"
    r"separation|whatsapp)\b", re.IGNORECASE)
#: Explicit opt-in; the suite's fence still overrides it.
_OPT_IN = "GRIP_PROMPT_ROUTING_SCORE"
_OFF = (("GRIP_CLASSIFY_NO_DECIDE", "1"),)

Decide = Callable[[str, dict], Optional[dict]]


@dataclass(frozen=True)
class Reading:
    """Jev's rating of one prompt, or why there is none. Uncalibrated and advisory."""
    pattern: str = ""
    score: Optional[float] = None
    confidence: Optional[float] = None
    probabilities: Dict[str, float] = field(default_factory=dict)
    model: str = ""
    cost_usd: str = ""
    skipped: str = ""

    @property
    def ask(self) -> bool:
        return self.confidence is not None and self.confidence < ASK_BELOW


def enabled() -> bool:
    return (os.environ.get(_OPT_IN) == "1"
            and not any(os.environ.get(name, "") == value for name, value in _OFF))


def _kept_home(prompt: str) -> str:
    """Why this prompt stays here, or "" when it may go out."""
    if len(prompt) > STATE_LIMIT:
        return "too_long"
    if any(marker in prompt for marker in _PASTED):
        return "pasted_content"
    return "personal_words" if _PERSONAL.search(prompt) else ""


def _number(value) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok else None


def _read(answer) -> Optional[tuple]:
    """``(pattern, score, confidence, probabilities)`` from a Score answer, or None."""
    if not isinstance(answer, dict) or not isinstance(answer.get("probabilities"), dict):
        return None
    probs = {k: _number(answer["probabilities"].get(str(i))) for i, k in enumerate(LEVELS)}
    score, confidence = _number(answer.get("score")), _number(answer.get("confidence"))
    numbers = list(probs.values()) + [confidence]
    if score is None or not 0 <= score <= len(LEVELS) - 1 or any(n is None or not 0 <= n <= 1 for n in numbers):
        return None
    pattern = max(LEVELS, key=lambda k: (probs[k], -LEVELS.index(k)))
    return pattern, score, confidence, probs


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from lib.hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=10.0)


def _floor(text: str, cwd: Optional[str]) -> tuple:
    try:
        from lib.decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def score_prompt(prompt: str, *, cwd: Optional[str] = None, decide: Decide = _default_decide) -> Reading:
    """Jev's Score over the four patterns for ``prompt``; ``skipped`` says why not."""
    if not enabled():
        return Reading(skipped="off")
    why = _kept_home(prompt)
    if why:
        return Reading(skipped=why)
    allowed, floor_why = _floor(prompt, cwd)
    if not allowed:
        return Reading(skipped=f"privacy_floor:{floor_why}")
    question = {"type": "score", "instructions": _QUESTION, "criteria": list(_LEVEL_TEXT)}
    try:
        result = decide(prompt, {"effort": question})
    except Exception:  # noqa: BLE001 -- advisory; never raises
        result = None
    read = _read((result.get("answers") or {}).get("effort")) \
        if isinstance(result, dict) and result.get("ok") else None
    if read is None:
        return Reading(skipped="no_answer")
    pattern, score, confidence, probs = read
    return Reading(pattern, score, confidence, probs, str(result.get("model") or ""),
                   str(result.get("cost_usd") or ""))


def shadow_row(reading: Reading, assessment: dict, *, ts: float) -> dict:
    """The log row: the heuristic's pick, the operator's stated pattern and Jev's reading,
    never the prompt."""
    return {"ts": ts, "machine_reading": True, "calibrated": False,
            "heuristic": assessment.get("recommended_pattern"),
            "implied": assessment.get("user_implied_pattern"),
            "jev": reading.pattern, "score": reading.score, "confidence": reading.confidence,
            "ask": reading.ask, "model": reading.model, "cost_usd": reading.cost_usd,
            "skipped": reading.skipped}


def shadow(prompt: str, *, assessment: dict, cwd: Optional[str] = None,
           log_path: Optional[Path] = None, decide: Decide = _default_decide) -> None:
    """Score the prompt and log it beside the heuristic. Never raises."""
    try:
        reading = score_prompt(prompt, cwd=cwd, decide=decide)
        if reading.skipped == "off":
            return
        from lib.claude_dir import get_claude_dir
        path = log_path or (get_claude_dir() / "logs" / "prompt-routing-shadow.jsonl")
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(shadow_row(reading, assessment, ts=time.time())) + "\n")
    except Exception:  # noqa: BLE001 -- shadow work never disturbs a prompt
        return
