"""
External Scoring Council for converge() output quality validation.

Eliminates self-scoring bias by using multi-provider LLM council to
independently validate converge() outputs. Claude Opus is EXCLUDED
from the council (same model family as the system under test).

Provider tiers:
    Fast:     Gemini 3.5 Flash-Lite + GPT-OSS 120B (2 families, fast)
    Standard: Gemini 3.1 Pro + GPT-OSS 120B + Qwen 3.8 27B (3 families)
    Proof:    Standard + GPT-OSS 20B and Gemini 3.8 Flash as tiebreakers (5 seats)

    Fast/Proof seats repointed from gemini-2.5-flash/gemini-2.5-pro on
    2026-11-24 (GRIP#9389): Google auto-routes those ids to gemini-3.5-flash-lite
    / gemini-3.8-flash from 2026-12-01.

The Haiku seat bills a per-token Anthropic key, so it joins standard and proof
only with GRIP_CLAUDE_ALLOW_PAID=1 (then 4 families, and 4 and 6 seats).

Lead scorer: Gemini 3.1 Pro (strongest non-Claude reasoning model).

Usage:
    from lib.moe.external_scorer import external_score

    verdict = external_score(
        output="function implementation...",
        task="write auth middleware",
        task_type="code",
        dimension_weights={"correctness": 0.4, "design": 0.3, "simplicity": 0.3},
    )
    print(f"Score: {verdict.composite_score}, Providers: {verdict.num_providers}")
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.hal_model_resolver import resolve as _resolve_model  # noqa: E402

logger = logging.getLogger(__name__)

#: The Haiku seat bills a per-token Anthropic key, so it sits only when this is
#: exactly "1" (rules/claude-cap-moves-to-the-next-subscription.md), compared as
#: bin/grip-auth-failover's _paid_allowed compares it, so " 1" or "1\n" is not
#: consent. The quote-support panel gates its own Haiku seat the same way
#: (lib/prove_it_council.py, GRIP#8508).
_PAID_OPT_IN = "GRIP_CLAUDE_ALLOW_PAID"


def _paid_anthropic_allowed() -> bool:
    return os.environ.get(_PAID_OPT_IN) == "1"

# Council weights (sum = 1.0), one row per seat name. The council weighs a name
# with no row at 1.0 (lib/moe/council.py), as much as all these rows together,
# so every seat needs one.
COUNCIL_WEIGHTS = {
    "gemini-3.1-pro": 0.30,
    "gpt-oss-120b": 0.20,
    "qwen3.8-27b": 0.20,
    "anthropic-haiku": 0.15,
    "gpt-oss-20b": 0.05,
    "gemini-3.8-flash": 0.05,
    "gemini-3.5-flash-lite": 0.05,
}

#: The name each Gemini or Groq model's scores carry into the council. A model
#: with no row here scores under its raw id, which has no weight.
_SEAT_NAMES = {
    "gemini-3.1-pro-preview": "gemini-3.1-pro",
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.8-flash": "gemini-3.8-flash",
    "openai/gpt-oss-120b": "gpt-oss-120b",
    "qwen/qwen3.8-27b": "qwen3.8-27b",
    "openai/gpt-oss-20b": "gpt-oss-20b",
}

#: Gemini 2.5 and 3.x and Groq's GPT-OSS reason before they answer, and the
#: reasoning counts against the output budget. Measured 2026-10-17: at the old
#: 512 tokens every Gemini seat stopped at MAX_TOKENS with no scores, the lead
#: scorer included, and GPT-OSS 120B hit the cap on 2 of 12 full-length inputs.
#: On the scorer's full 4,000-character input Gemini 2.5 Pro thought for up to
#: about 2,760 tokens, and GPT-OSS 120B used up to 603. Gemini's 60-second
#: request timeout limits what a larger Gemini budget could add.
_GEMINI_MAX_TOKENS = 4096
_GROQ_MAX_TOKENS = 2048

# Providers EXCLUDED from external scoring (self-scoring bias)
EXCLUDED_PROVIDERS = frozenset({"claude-opus", "claude-sonnet", "claude-haiku-self"})

# Keychain service for Anthropic external scorer
_ANTHROPIC_KEYCHAIN_SERVICE = "grip-anthropic-external"

SCORING_PROMPT_TEMPLATE = """You are scoring the quality of an AI assistant's output.

Task: {task}
Task type: {task_type}

Output to score:
---
{output}
---

Score each dimension 0-100:
{dimensions}

Return ONLY a JSON object with dimension scores. Be critical.
Do NOT give high scores by default. A mediocre output should score 50-65.
An excellent output scores 80-90. Only truly exceptional work scores 90+.
"""


@dataclass
class ExternalScore:
    """Score from a single external provider."""
    provider: str
    dimension_scores: dict[str, int] = field(default_factory=dict)
    composite_score: int = 0
    error: str = ""


def _parse_json_scores(text: str) -> dict[str, int]:
    """Extract JSON scores from provider response text."""
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end])
            return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _format_dimensions(weights: dict[str, float]) -> str:
    """Format dimension list for the scoring prompt."""
    lines = []
    for dim, weight in weights.items():
        lines.append(f"- {dim} (weight: {weight:.0%})")
    return "\n".join(lines)


def _compute_composite(dim_scores: dict[str, int], weights: dict[str, float]) -> int:
    """Compute weighted composite from dimension scores."""
    if not dim_scores:
        return 0
    total = 0.0
    weight_sum = 0.0
    for dim, weight in weights.items():
        if dim in dim_scores:
            total += dim_scores[dim] * weight
            weight_sum += weight
    return int(total / weight_sum) if weight_sum > 0 else 0


def _fetch_anthropic_key() -> Optional[str]:
    """Fetch Anthropic API key from macOS Keychain for external scoring."""
    import subprocess
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _ANTHROPIC_KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _shield(text: str) -> str:
    """``text`` scrubbed whole by lib/review_redaction.py before it leaves, in every
    invocation shape; with no shield to load it raises OSError and sends nothing
    (GRIP#8106: this module had no scrub and relied on each caller to have one)."""
    try:
        from review_redaction import shield_text
    except ImportError:
        try:
            from lib.review_redaction import shield_text
        except ImportError as exc:  # no shield, no send
            raise OSError("the redaction shield is unavailable, so nothing was sent") from exc
    return shield_text(text)


def _anthropic_score_request(api_key: str, prompt: str) -> str:
    """POST one scoring prompt to the Anthropic API, return concatenated text.

    Extracted from `_score_with_anthropic_external` (KISS decomposition —
    the HTTP request + response-text assembly is a self-contained unit).
    The Haiku-tier model id resolves from HAL's canonical catalogue
    (syscall-doctrine); fail-safe verbatim literal when absent, never raises.
    The prompt is scrubbed again here, at the exit, so a caller that skipped
    the shield still sends nothing unscrubbed; a failed scrub raises OSError.
    """
    import urllib.request

    prompt = _shield(prompt)
    payload = json.dumps({
        "model": _resolve_model("anthropic/claude-haiku-", "claude-haiku-4-5"),
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
        "system": "You are an objective code quality scorer. Return only JSON.",
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": "GRIP-ExternalScorer/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return "".join(
        block.get("text", "")
        for block in body.get("content", [])
        if block.get("type") == "text"
    )


def _finalize_score(provider_name: str, raw_text: str) -> ExternalScore:
    """Parse scorer JSON into an ExternalScore (success or parse-fail).

    Extracted from the three provider-scorer functions (Gemini, Groq,
    Anthropic) which had byte-identical parse-and-return tails (DRY/YSH,
    proven 3x). Behaviour preserved exactly.
    """
    scores = _parse_json_scores(raw_text)
    if scores:
        return ExternalScore(provider=provider_name, dimension_scores=scores)
    return ExternalScore(provider=provider_name, error="Failed to parse scores")


def _score_with_gemini(output: str, task: str, task_type: str,
                       dimensions: str, model: str) -> ExternalScore:
    """Score using the Gemini API (3.1 Pro, 2.5 Pro or 2.5 Flash)."""
    provider_name = _SEAT_NAMES.get(model, model)
    try:
        from lib.providers.gemini import GeminiProvider
        provider = GeminiProvider()
        prompt = SCORING_PROMPT_TEMPLATE.format(
            task=task, task_type=task_type, output=output[:4000], dimensions=dimensions
        )
        result = provider.generate(prompt, model=model, temperature=0.1,
                                   max_tokens=_GEMINI_MAX_TOKENS)
        return _finalize_score(provider_name, result.text)
    except Exception as e:
        return ExternalScore(provider=provider_name, error=str(e))


def _score_with_groq(output: str, task: str, task_type: str,
                     dimensions: str, model: str) -> ExternalScore:
    """Score using the Groq API (GPT-OSS and Qwen)."""
    provider_name = _SEAT_NAMES.get(model, model)
    try:
        from lib.providers.groq import GroqProvider
        provider = GroqProvider()
        prompt = SCORING_PROMPT_TEMPLATE.format(
            task=task, task_type=task_type, output=output[:4000], dimensions=dimensions
        )
        result = provider.generate(prompt, model=model, temperature=0.1,
                                   max_tokens=_GROQ_MAX_TOKENS,
                                   system="You are an objective code quality scorer. Return only JSON.")
        return _finalize_score(provider_name, result.text)
    except Exception as e:
        return ExternalScore(provider=provider_name, error=str(e))


def _score_with_anthropic_external(output: str, task: str, task_type: str,
                                   dimensions: str) -> ExternalScore:
    """Score using Anthropic API via Keychain (EXTERNAL call, not self-scoring).

    Uses Claude Haiku for cost efficiency. This is a DIFFERENT API call from
    the GRIP system itself -- scored via a separate credential, so it is NOT
    the agent scoring its own output. The credential bills per token, so
    without GRIP_CLAUDE_ALLOW_PAID=1 the leg answers with an error before it
    reads the keychain, and nothing is billed.
    """
    provider_name = "anthropic-haiku"
    if not _paid_anthropic_allowed():
        return ExternalScore(provider=provider_name,
                             error=f"the paid Anthropic key needs {_PAID_OPT_IN}=1")
    api_key = _fetch_anthropic_key()

    if not api_key:
        return ExternalScore(provider=provider_name,
                             error="No key in Keychain service: " + _ANTHROPIC_KEYCHAIN_SERVICE)

    try:
        prompt = SCORING_PROMPT_TEMPLATE.format(
            task=task, task_type=task_type, output=output[:4000], dimensions=dimensions
        )
        text = _anthropic_score_request(api_key, prompt)
        return _finalize_score(provider_name, text)
    except Exception as e:
        return ExternalScore(provider=provider_name, error=str(e))


def external_score(
    output: str,
    task: str,
    task_type: str = "code",
    dimension_weights: Optional[dict[str, float]] = None,
    mode: str = "standard",
) -> "CouncilVerdict":
    """Score converge() output via multi-provider council.

    Args:
        output: The converge() output to score.
        task: Description of the task.
        task_type: Type of task (code, explanation, design, etc.).
        dimension_weights: Scoring dimensions and weights.
        mode: "fast", "standard", "proof" -- controls how many providers.

    Returns:
        CouncilVerdict with merged scores from all providers.
    """
    from lib.moe.council import MoECouncil, CouncilVerdict
    from lib.moe.provider_registry import ScoreResult

    # Scrub whole, THEN cut: every leg cuts output[:4000], and a cut can split a
    # name the shield then misses. The Gemini and Groq legs scrub again at their
    # provider exit; the Anthropic leg scrubs again in _anthropic_score_request.
    # A failed scrub raises OSError here and no leg runs (GRIP#8106).
    output, task = _shield(output), _shield(task)

    if dimension_weights is None:
        from lib.converge_metrics import WEIGHT_TABLES
        dimension_weights = WEIGHT_TABLES.get(task_type, WEIGHT_TABLES["general"])

    dimensions_text = _format_dimensions(dimension_weights)

    # Collect scores from providers based on mode
    raw_scores: list[ExternalScore] = []

    if mode in ("standard", "proof"):
        # Gemini 3.1 Pro -- LEAD scorer
        raw_scores.append(_score_with_gemini(
            output, task, task_type, dimensions_text, "gemini-3.1-pro-preview"))

    if mode in ("fast", "standard", "proof"):
        # Gemini 2.5 Flash (fast mode only)
        if mode == "fast":
            raw_scores.append(_score_with_gemini(
                output, task, task_type, dimensions_text, "gemini-3.5-flash-lite"))

        # GPT-OSS 120B (replaced Llama 3.3 70B, which Groq retired 2026-06-17)
        raw_scores.append(_score_with_groq(
            output, task, task_type, dimensions_text, "openai/gpt-oss-120b"))

    if mode in ("standard", "proof"):
        # Qwen 3.8 27B. This seat was Qwen3 32B until Groq decommissioned it on
        # 2026-07-17, then GPT-OSS 120B, so standard mode counted that model twice.
        raw_scores.append(_score_with_groq(
            output, task, task_type, dimensions_text, "qwen/qwen3.8-27b"))

        # Anthropic Haiku (external API call via Keychain, NOT self-scoring). It
        # bills a per-token key, so it sits only with GRIP_CLAUDE_ALLOW_PAID=1.
        if _paid_anthropic_allowed():
            raw_scores.append(_score_with_anthropic_external(
                output, task, task_type, dimensions_text))

    if mode == "proof":
        # Tiebreakers for maximum rigour. They replace DeepSeek R1 Distill Llama
        # 70B and Llama 4 Scout, which Groq retired on 2026-08-30.
        raw_scores.append(_score_with_groq(
            output, task, task_type, dimensions_text, "openai/gpt-oss-20b"))
        raw_scores.append(_score_with_gemini(
            output, task, task_type, dimensions_text, "gemini-3.8-flash"))

    # Convert to ScoreResult for council
    score_results = []
    for rs in raw_scores:
        if rs.error:
            logger.debug("Provider %s failed: %s", rs.provider, rs.error)
            continue
        composite = _compute_composite(rs.dimension_scores, dimension_weights)
        score_results.append(ScoreResult(
            provider=rs.provider,
            composite_score=composite,
            dimension_scores=rs.dimension_scores,
            passed=composite >= 70,
        ))

    if not score_results:
        return CouncilVerdict(composite_score=0, num_providers=0)

    # Deliberate via council
    council = MoECouncil()
    verdict = council.deliberate(score_results, weights=COUNCIL_WEIGHTS)

    return verdict


def validate_no_self_scoring(providers: list[str]) -> bool:
    """Verify that no Claude Opus/Sonnet model is in the scoring council.

    Claude Haiku via EXTERNAL API key is allowed (different context).
    Claude Opus/Sonnet (the system under test) is NOT allowed.
    """
    for p in providers:
        p_lower = p.lower()
        if "claude-opus" in p_lower or "claude-sonnet" in p_lower:
            return False
    return True
