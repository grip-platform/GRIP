"""
External Reviewer — Independent LLM Review Layer for GRIP Power.

Sends GRIP output to external LLMs (Gemini Pro, Groq Llama) for independent
review with ZERO system instructions (vanilla, unbiased, neutral positioning).

Usage:
    from lib.external_reviewer import review_output, ReviewType
    result = await review_output(text, context, ReviewType.CODE)

Origin: Gen 475. GRIP Power plan — V>> directive for independent external review.
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
_LIB = str(Path(__file__).resolve().parent)
if _LIB in sys.path:  # own lib (GRIP#9857); always first, not merely present --
    sys.path.remove(_LIB)  # a stale shared-tree lib may already lead it (Codex, GRIP#10125)
sys.path.insert(0, _LIB)


def pr_review_diff_budget() -> int:
    """Return ``pr_diff.PR_REVIEW_DIFF_BUDGET`` in every invocation shape.

    This module puts ``lib/`` on ``sys.path`` (above), so the sibling module
    is ``pr_diff``. The ``lib.``-prefixed spelling only resolves when the repo
    root is also on the path (pytest from the root, ``python -m lib.x``); it
    is NOT when a script under ``lib/`` is run by path, which is the shape of
    every CLI and launchd invocation, and the hypothesis engine then logged
    "External validation unavailable: No module named 'lib.pr_diff'" on every
    run (measured 2026-09-22). Try the sibling first, keep the prefixed form as
    the fallback for package-style callers.
    """
    try:
        from pr_diff import PR_REVIEW_DIFF_BUDGET
    except ImportError:
        from lib.pr_diff import PR_REVIEW_DIFF_BUDGET
    return PR_REVIEW_DIFF_BUDGET


def _shielded(text: str) -> str:
    """``text`` through lib/review_redaction.py, in every invocation shape (as above)."""
    try:
        from review_redaction import redact_outbound
    except ImportError:
        from lib.review_redaction import redact_outbound
    return redact_outbound(text)


class ReviewType(Enum):
    CODE = "code"
    KNOWLEDGE = "knowledge"
    CLAIM = "claim"
    DECISION = "decision"
    STRUCTURAL_MAPPING = "structural_mapping"
    FORMAL_PROOF = "formal_proof"


@dataclass
class ReviewResult:
    """Result from external LLM review.

    ``score`` is ``None`` whenever no reviewer supplied a rating -- either
    nobody answered at all, or whoever did returned prose with no parseable
    number. That is deliberately not a float. An unknown collapsed into a
    number is indistinguishable from a measurement, and the 0.5 placeholder
    this used to return was the worst possible choice of number: it lands
    exactly on the SMT council's 50/100 veto line, so a reviewer that never
    answered was counted as a real vote (#4502).
    """
    provider: str
    model: str
    score: Optional[float]  # 0.0 to 1.0, or None when nothing scored it
    hallucination_risk: str  # "none", "low", "medium", "high", "unknown"
    issues: list = field(default_factory=list)
    reasoning: str = ""
    raw_response: str = ""


# Gemini 2.5 Flash spends hidden thinking out of maxOutputTokens. At 2048 its
# visible reply could stop after about 80 tokens: on 2026-11-16 every review of
# H950-H952 ended mid-sentence, before any rating, so no verdict was validated
# (GRIP#9230). The budget now leaves room for the answer, and the rating comes
# first, so a reply that is still cut off carries it.
_REVIEW_MAX_TOKENS = 8192
_SCORE_FIRST = ("Begin your reply with one line in exactly this form: Score: N/100, "
                "where N is from 0 to 100. Then give your reasons.\n\n")

# Review prompts — ZERO system instructions, purely neutral
REVIEW_PROMPTS = {
    ReviewType.CODE: (
        "Review this code for correctness, efficiency, and potential bugs. "
        "Check for: duplicated logic, functions over 30 lines, O(n^2)+ algorithms "
        "without justification, unused parameters, and hardcoded dependencies.\n\n"
        "Rate overall quality 0-100. List specific issues found.\n\n"
        "Code:\n{output}\n\nContext: {context}"
    ),
    ReviewType.KNOWLEDGE: (
        "Review this text for reasoning quality and factual accuracy. "
        "Check for: unsupported claims, logical fallacies, missing evidence, "
        "overgeneralisation, and confirmation bias.\n\n"
        "Rate reasoning quality 0-100. List specific issues found.\n\n"
        "Text:\n{output}\n\nContext: {context}"
    ),
    ReviewType.CLAIM: (
        "Evaluate this claim for falsifiability and evidence support. "
        "For each claim: (1) Is it testable? (2) What would disprove it? "
        "(3) Is evidence provided? (4) Are there alternative explanations?\n\n"
        "Rate claim quality 0-100. List specific issues found.\n\n"
        "Claim:\n{output}\n\nContext: {context}"
    ),
    ReviewType.DECISION: (
        "Review this decision analysis for completeness and bias. "
        "Check for: anchoring bias, sunk cost fallacy, confirmation bias, "
        "missing alternatives, and reversibility assessment.\n\n"
        "Rate decision quality 0-100. List specific issues found.\n\n"
        "Decision:\n{output}\n\nContext: {context}"
    ),
    ReviewType.STRUCTURAL_MAPPING: (
        "Evaluate this structural mapping using Gentner's Structure-Mapping "
        "Theory (1983). Check: (1) Are relations causal or surface-level? "
        "(2) Is there over-mapping (>80% forced)? (3) Is there under-mapping "
        "(<20%)? (4) Are unmapped elements genuinely orthogonal? "
        "(5) Does systematicity hold?\n\n"
        "Rate structural validity 0-100. List specific violations.\n\n"
        "Mapping:\n{output}\n\nContext: {context}"
    ),
    ReviewType.FORMAL_PROOF: (
        "Evaluate this mathematical claim or proof sketch for rigour. "
        "Check: (1) Are assumptions stated explicitly? (2) Does each step "
        "follow logically? (3) Are there hidden assumptions? (4) Is the "
        "claim falsifiable? (5) Would a counterexample invalidate the chain?\n\n"
        "Rate formal rigour 0-100. List specific gaps.\n\n"
        "Proof/Claim:\n{output}\n\nContext: {context}"
    ),
}


def _parse_score(text: str) -> Optional[float]:
    """Extract numeric score from review text, or None when there isn't one.

    Returns ``None`` rather than a midpoint placeholder. A reviewer that
    answered without a rating has expressed no opinion, and a stand-in number
    reads downstream as a real, deliberate score (#4502).
    """
    import re
    # ``[:\s*]`` lets a Markdown-bold label through: "**Rating:** 72".
    patterns = [
        r"(\d{1,3})\s*/\s*100",
        r"score[:\s*]+(\d{1,3})",
        r"rating[:\s*]+(\d{1,3})",
        r"quality[:\s*]+(\d{1,3})",
        r"(\d{1,3})\s*(?:out of|/)\s*100",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = int(match.group(1))
            if 0 <= val <= 100:
                return val / 100.0
    return None  # nothing parseable -- NOT the same as a score of 0.5


def _classify_risk(score: Optional[float], issues: list) -> str:
    """Classify hallucination risk from score and issue count.

    A missing score is not a low score. ``None`` means nobody rated this, so
    the risk is genuinely unknown -- saying "medium" would be inventing a
    finding out of an absence (#4502).
    """
    if score is None:
        return "unknown"
    if score >= 0.85 and len(issues) <= 1:
        return "none"
    if score >= 0.70:
        return "low"
    if score >= 0.50:
        return "medium"
    return "high"


def _extract_issues(text: str) -> list:
    """Extract issue bullet points from review text."""
    lines = text.split("\n")
    issues = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("-", "*", "•")) and len(stripped) > 5:
            issues.append(stripped.lstrip("-*• "))
    return issues[:10]  # cap at 10


async def review_with_gemini(prompt: str) -> Optional[ReviewResult]:
    """Send review to Gemini Pro (free tier, powerful reasoning)."""
    try:
        from providers.gemini import GeminiProvider
        provider = GeminiProvider()
        result = provider.generate(prompt, temperature=0.1, max_tokens=_REVIEW_MAX_TOKENS)
        response = result.text if hasattr(result, 'text') else str(result)
        issues = _extract_issues(response)
        score = _parse_score(response)
        return ReviewResult(
            provider="gemini",
            model=provider.model,
            score=score,
            hallucination_risk=_classify_risk(score, issues),
            issues=issues,
            reasoning=response[:500],
            raw_response=response,
        )
    except Exception as e:
        print(f"[external_reviewer] Gemini failed: {e}", file=sys.stderr)
        return None


async def review_with_groq(prompt: str) -> Optional[ReviewResult]:
    """Send review to Groq Llama (fast, free tier fallback)."""
    try:
        from providers.groq import GroqProvider
        provider = GroqProvider()
        result = provider.generate(prompt, temperature=0.1, max_tokens=_REVIEW_MAX_TOKENS)
        response = result.text if hasattr(result, 'text') else str(result)
        issues = _extract_issues(response)
        score = _parse_score(response)
        return ReviewResult(
            provider="groq",
            model=provider.model,
            score=score,
            hallucination_risk=_classify_risk(score, issues),
            issues=issues,
            reasoning=response[:500],
            raw_response=response,
        )
    except Exception as e:
        print(f"[external_reviewer] Groq failed: {e}", file=sys.stderr)
        return None


async def review_output(
    output_text: str,
    context: str = "",
    review_type: ReviewType = ReviewType.CODE,
) -> ReviewResult:
    """
    Send GRIP output to external LLM for independent review.

    External models receive ZERO system instructions — purely neutral prompts.
    Primary: Gemini Pro. Fallback: Groq Llama 3.3 70B.

    Args:
        output_text: The output to review (code, text, claim, decision)
        context: Brief description of the task that produced this output
        review_type: Type of review to perform

    Returns:
        ReviewResult with score, hallucination risk, and specific issues
    """
    # Names, message IDs, emails, ID and phone numbers never leave unredacted
    # (lib/review_redaction.py). Each text is scrubbed whole and only then cut:
    # a cut first could split a name in two, and the shield recognises neither half.
    prompt = _SCORE_FIRST + REVIEW_PROMPTS[review_type].format(
        output=_shielded(output_text)[:pr_review_diff_budget()],  # sane budget; never silent truncation
        context=_shielded(context)[:500],
    )

    # Try Gemini first (primary), fall back to Groq
    result = await review_with_gemini(prompt)
    if result is None:
        result = await review_with_groq(prompt)
    if result is None:
        return ReviewResult(
            provider="none",
            model="unavailable",
            # No score at all. Not 0.5 -- that placeholder was read downstream
            # as a genuine 50/100, which is exactly the SMT council's veto
            # line, so an outage silently cast a real vote (#4502).
            score=None,
            hallucination_risk="unknown",
            issues=["Both external providers unavailable"],
            reasoning="Could not reach Gemini or Groq for independent review",
        )
    return result


def review_output_sync(
    output_text: str,
    context: str = "",
    review_type: ReviewType = ReviewType.CODE,
) -> ReviewResult:
    """Synchronous wrapper for review_output."""
    return asyncio.run(review_output(output_text, context, review_type))


if __name__ == "__main__":
    # CLI usage: python external_reviewer.py <review_type> <file_or_text>
    if len(sys.argv) < 3:
        print("Usage: python external_reviewer.py <code|knowledge|claim|decision> <text_or_file>")
        sys.exit(1)

    rtype = ReviewType(sys.argv[1])
    text_or_path = sys.argv[2]

    if os.path.isfile(text_or_path):
        with open(text_or_path) as f:
            text = f.read()
    else:
        text = text_or_path

    result = review_output_sync(text, review_type=rtype)
    print(json.dumps({
        "provider": result.provider,
        "model": result.model,
        "score": result.score,
        "hallucination_risk": result.hallucination_risk,
        "issues": result.issues,
        "reasoning": result.reasoning,
    }, indent=2))
