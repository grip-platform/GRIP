#!/usr/bin/env python3
"""
Local Llama MoE Generalist Reviewer
=====================================

Mixture-of-Experts review using Llama 3.2 3B via llama-cpp-python.
Domain-agnostic: scores code, config, docs, and knowledge work equally.
Each review dimension is an independent "expert" — a separate prompt
that scores one aspect. Scores combine with the same weights as
pre_review_pipeline.py for drop-in compatibility.

Diff type detection classifies PRs into: code, config, docs, mixed.
Expert prompts adapt their evaluation criteria per diff type so that
a config PR promoting operational context scores as well as a code PR
adding a feature — both are valid deliverables.

Experts:
    Quality   (30%) — deliverable correctness, structure, DRY/KISS
    Alignment (30%) — does diff match PR title/description
    Verification (25%) — evidence of correctness (tests, checklists, verification steps)
    Docs      (15%) — knowledge completeness, comments, documentation
    Security  (bonus) — secrets, injection risks, access control

Usage:
    python3 lib/llama_reviewer.py --pr 747
    python3 lib/llama_reviewer.py --diff-file /tmp/pr.diff
    python3 lib/llama_reviewer.py --all-open
    python3 lib/llama_reviewer.py --diff-file pr.diff --title "feat: add auth"

Requires:
    pip install llama-cpp-python
    bash scripts/bootstrap-llama.sh  (downloads model)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
MODEL_PATH = CLAUDE_DIR / "models" / "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
N_CTX = 4096
MAX_DIFF_CHARS = 8000  # ~2000 tokens, leaves room for prompt + response
MAX_TOKENS_PER_EXPERT = 256

# Weights from pre_review_pipeline.py — single source of truth
DIMENSION_WEIGHTS = {"quality": 0.30, "alignment": 0.30, "testing": 0.25, "docs": 0.15}

# Score floors for dimensions where 3B model hallucinates 0 scores.
# A 3B model scoring 0 on alignment/security is almost certainly noise, not signal.
DIMENSION_FLOORS = {"alignment": 30, "security": 30}

# Cliff penalties (mirrored from pre_review_pipeline.py)
TESTING_PENALTY_THRESHOLD = 60
TESTING_PENALTY = 10
DOCS_PENALTY_THRESHOLD = 40
DOCS_PENALTY = 5

# Shared response format instruction for all expert prompts (DRY)
_RESPONSE_FORMAT = """
Respond in EXACTLY this format (first line MUST be SCORE):
SCORE: <number 0-100>
- <feedback point 1>
- <feedback point 2>
- <feedback point 3>"""


# ---------------------------------------------------------------------------
# Data models (compatible with pre_review_pipeline.py)
# ---------------------------------------------------------------------------

@dataclass
class DimensionScores:
    quality: int = 0
    alignment: int = 0
    testing: int = 0
    docs: int = 0
    security: int = 0  # bonus dimension, not in weighted composite

    @property
    def composite(self) -> int:
        raw = round(
            DIMENSION_WEIGHTS["quality"] * self.quality
            + DIMENSION_WEIGHTS["alignment"] * self.alignment
            + DIMENSION_WEIGHTS["testing"] * self.testing
            + DIMENSION_WEIGHTS["docs"] * self.docs
        )
        if self.testing < TESTING_PENALTY_THRESHOLD:
            raw = max(0, raw - TESTING_PENALTY)
        if self.docs < DOCS_PENALTY_THRESHOLD:
            raw = max(0, raw - DOCS_PENALTY)
        # Security bonus/penalty: +3 if clean (>=80), -5 if critical (<40)
        if self.security >= 80:
            raw = min(100, raw + 3)
        elif self.security < 40:
            raw = max(0, raw - 5)
        return raw


@dataclass
class ExpertResult:
    dimension: str
    score: int
    feedback: list  # list of bullet point strings
    raw_response: str = ""


@dataclass
class ReviewResult:
    pr_number: str
    pr_title: str
    dimensions: DimensionScores = field(default_factory=DimensionScores)
    composite_score: int = 0
    expert_results: list = field(default_factory=list)  # list of ExpertResult
    verdict: str = ""  # "approve", "improve", "rework"
    summary: str = ""
    diff_type: str = "code"  # "code", "config", "docs", "mixed"

    def to_pipeline_dict(self) -> dict:
        """Format compatible with pre_review_pipeline.py PipelineResult output."""
        return {
            "composite_score": self.composite_score,
            "passed": self.composite_score >= 80,
            "dimension_scores": {
                "quality": self.dimensions.quality,
                "alignment": self.dimensions.alignment,
                "testing": self.dimensions.testing,
                "docs": self.dimensions.docs,
            },
            "security_score": self.dimensions.security,
            "verdict": self.verdict,
            "diff_type": self.diff_type,
            "suggestions": [
                fb for er in self.expert_results for fb in er.feedback
                if er.score < 80
            ],
            "reviewer": "llama-3.2-3b-moe-local",
            "expert_reasoning": {
                er.dimension: {
                    "score": er.score,
                    "feedback": er.feedback,
                }
                for er in self.expert_results
            },
        }


# ---------------------------------------------------------------------------
# Diff type detection (single source: diff_classifier module)
# ---------------------------------------------------------------------------

from lib.diff_classifier import classify_diff


def detect_diff_type(diff: str) -> str:
    """Classify a diff as 'code', 'config', 'docs', or 'mixed'.

    Delegates entirely to diff_classifier.classify_diff().
    This wrapper exists so callers don't need to change import paths.
    """
    return classify_diff(diff)


# ---------------------------------------------------------------------------
# Expert prompts (domain-agnostic)
# ---------------------------------------------------------------------------

_DIFF_TYPE_CONTEXT = {
    "code": "This is a CODE change. Evaluate code quality, patterns, and correctness.",
    "config": "This is a CONFIGURATION change (JSON, YAML, TOML, rules files). "
              "Evaluate structural correctness, consistency, and whether the config achieves its stated goal.",
    "docs": "This is a DOCUMENTATION change (Markdown, text). "
            "Evaluate content accuracy, completeness, and clarity. Documentation IS the deliverable.",
    "mixed": "This is a MIXED change (code + config + docs). Evaluate each file type appropriately.",
}


def _build_prompt(role: str, criteria: str, diff: str, title: str) -> str:
    """Build an expert prompt from role, criteria, diff, and title (DRY)."""
    return f"""{role}
Rate this PR diff on a scale of 0-100.

{criteria}

PR Title: {title}

```diff
{diff}
```
{_RESPONSE_FORMAT}"""


def _quality_prompt(diff: str, title: str, diff_type: str = "code") -> str:
    context = _DIFF_TYPE_CONTEXT.get(diff_type, _DIFF_TYPE_CONTEXT["mixed"])
    criteria = f"""{context}

For CODE: consider style, design patterns, complexity, bugs, DRY/KISS adherence.
For CONFIG: consider correctness, consistency, no stale values, achieves stated goal.
For DOCS: consider accuracy, completeness, clarity, useful to the reader.
100 = excellent quality deliverable. 0 = terrible quality."""
    return _build_prompt(
        "You are a deliverable quality reviewer for an AI operating system.",
        criteria, diff, title)


def _alignment_prompt(diff: str, title: str, diff_type: str = "code") -> str:
    criteria = """Look at the file paths in the diff and the PR title.
Do the changed files relate to the topic in the title?
Are there files that seem completely unrelated to the title?
Config, documentation, and knowledge management changes are VALID deliverables.
100 = all changed files relate to the title topic. 0 = files are completely unrelated to the title."""
    return _build_prompt(
        "You are a PR alignment reviewer.", criteria, diff, title)


def _verification_prompt(diff: str, title: str, diff_type: str = "code") -> str:
    type_criteria = {
        "code": "Are test files included? Do tests cover edge cases and verify actual behaviour?",
        "config": ("Is there evidence the config was verified? Look for: before/after comparisons, "
                   "checklist items, grep verification. Config verified by automated checks scores 80-90."),
        "docs": ("Is the documentation internally consistent? Are cross-references valid? "
                 "Well-structured docs with clear sections scores 80-90."),
    }
    specific = type_criteria.get(diff_type, "Is there evidence of verification?")
    criteria = f"""{specific}

IMPORTANT: Not all PRs need unit tests. Config consolidation verified by grep/line counts.
Docs verified by internal consistency. Score 70-80 for non-code with clear verification.
100 = comprehensive verification. 0 = no verification at all."""
    return _build_prompt(
        "You are a verification completeness reviewer.", criteria, diff, title)


def _docs_prompt(diff: str, title: str, diff_type: str = "code") -> str:
    if diff_type in ("docs", "config"):
        guidance = ("This IS a documentation/config change — the content is the deliverable. "
                    "Evaluate clarity, structure, completeness. Well-organised scores 80-90.")
    else:
        guidance = ("Evaluate: are public functions documented? Are comments helpful? "
                    "Are type hints present? Does the PR update relevant docs?")
    criteria = f"""{guidance}
100 = well documented and complete. 0 = no documentation at all."""
    return _build_prompt(
        "You are a knowledge completeness reviewer.", criteria, diff, title)


def _security_prompt(diff: str, title: str, diff_type: str = "code") -> str:
    criteria = """Look for these specific security problems in the added lines:
- Hardcoded API keys, passwords, or tokens in string literals (e.g. "sk-...", "password=...")
- Credentials or secrets stored in plain text
- Sensitive data exposed in config files
If NONE of these problems exist in the diff, score 90-100.
100 = no secrets or credentials found (SAFE). 0 = hardcoded secrets found (DANGEROUS)."""
    return _build_prompt(
        "You are a security reviewer.", criteria, diff, title)


EXPERT_PROMPTS = {
    "quality": _quality_prompt,
    "alignment": _alignment_prompt,
    "testing": _verification_prompt,
    "docs": _docs_prompt,
    "security": _security_prompt,
}


# ---------------------------------------------------------------------------
# Llama interface
# ---------------------------------------------------------------------------

_llm_instance = None


def _get_llm():
    """Lazy-load Llama model (singleton)."""
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance

    try:
        from llama_cpp import Llama
    except ImportError:
        print("ERROR: llama-cpp-python not installed. Run: pip install llama-cpp-python",
              file=sys.stderr)
        sys.exit(2)

    model_path = str(MODEL_PATH)
    if not Path(model_path).exists():
        print(f"ERROR: Model not found at {model_path}. Run: bash scripts/bootstrap-llama.sh",
              file=sys.stderr)
        sys.exit(2)

    _llm_instance = Llama(
        model_path=model_path,
        n_ctx=N_CTX,
        verbose=False,
        n_gpu_layers=-1,  # offload all layers to Metal/GPU
    )
    return _llm_instance


def _run_expert(dimension: str, diff: str, title: str,
                diff_type: str = "code") -> ExpertResult:
    """Run a single expert prompt and parse the result."""
    llm = _get_llm()
    prompt_fn = EXPERT_PROMPTS[dimension]
    prompt = prompt_fn(diff, title, diff_type)

    result = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS_PER_EXPERT,
        temperature=0.1,  # low temp for consistent scoring
    )

    raw = result["choices"][0]["message"]["content"]
    score = _parse_score(raw)
    feedback = _parse_feedback(raw)

    return ExpertResult(
        dimension=dimension,
        score=score,
        feedback=feedback,
        raw_response=raw,
    )


def _parse_score(text: str) -> int:
    """Extract score from expert response. Robust to formatting variations.

    Priority: SCORE: pattern > N/100 pattern > standalone number > fallback.
    Scores > 100 are capped with a warning (may indicate LLM over-confidence).
    Fallback skips 0-4 to avoid false matches like '0 issues found'.
    """
    # Try "SCORE: 85" pattern
    m = re.search(r"SCORE:\s*(\d+)", text, re.IGNORECASE)
    if m:
        raw_val = int(m.group(1))
        if raw_val > 100:
            print(f"  [warn] LLM returned score {raw_val}, capping to 100", file=sys.stderr)
        return max(0, min(100, raw_val))

    # Try "85/100" pattern
    m = re.search(r"(\d+)\s*/\s*100", text)
    if m:
        raw_val = int(m.group(1))
        if raw_val > 100:
            print(f"  [warn] LLM returned score {raw_val}, capping to 100", file=sys.stderr)
        return max(0, min(100, raw_val))

    # Try standalone number at start of line
    m = re.search(r"^(\d+)$", text, re.MULTILINE)
    if m:
        val = int(m.group(1))
        if 0 <= val <= 100:
            return val

    # Fallback: find any number that looks like a score (5-100)
    # Skip 0-4 as they're almost never intentional scores (e.g. "0 issues")
    numbers = re.findall(r"\b(\d+)\b", text)
    for n in numbers:
        val = int(n)
        if 5 <= val <= 100:
            return val

    return 50  # default mid-range if parsing fails


def _parse_feedback(text: str) -> list:
    """Extract bullet points from expert response."""
    bullets = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            bullets.append(line[2:].strip())
        elif re.match(r"^\d+\.\s", line):
            bullets.append(re.sub(r"^\d+\.\s*", "", line).strip())
    return bullets[:5]  # cap at 5 bullet points


# ---------------------------------------------------------------------------
# Main review function
# ---------------------------------------------------------------------------

def review_pr(diff: str, title: str, pr_number: str = "0",
              verbose: bool = False) -> ReviewResult:
    """Run MoE review on a PR diff.

    Args:
        diff: Unified diff text
        title: PR title
        pr_number: PR number (for display)
        verbose: Print progress

    Returns:
        ReviewResult with composite score and per-dimension feedback
    """
    # Detect diff type for domain-adaptive prompts
    diff_type = detect_diff_type(diff)
    if verbose:
        print(f"  [detect] Diff type: {diff_type}", flush=True)

    # Truncate diff to fit in context
    truncated = diff[:MAX_DIFF_CHARS]
    if len(diff) > MAX_DIFF_CHARS:
        truncated += f"\n... (truncated, {len(diff) - MAX_DIFF_CHARS} chars omitted)"

    results = []
    for dim in ["quality", "alignment", "testing", "docs", "security"]:
        if verbose:
            print(f"  [{dim}] Running expert...", flush=True)
        er = _run_expert(dim, truncated, title, diff_type)

        # Retry on suspicious scores — 3B model may hallucinate on first attempt
        if er.score < 20:
            if verbose:
                print(f"  [{dim}] Suspicious score {er.score}, retrying...", flush=True)
            retry_er = _run_expert(dim, truncated, title, diff_type)
            if retry_er.score > er.score:
                er = retry_er

        results.append(er)
        if verbose:
            print(f"  [{dim}] Score: {er.score}/100", flush=True)

    # Apply dimension floors — 3B model hallucinates 0 scores on alignment/security
    for er in results:
        floor = DIMENSION_FLOORS.get(er.dimension, 0)
        if er.score < floor:
            if verbose:
                print(f"  [{er.dimension}] Floor applied: {er.score} -> {floor}", flush=True)
            er.score = floor

    dims = DimensionScores(
        quality=results[0].score,
        alignment=results[1].score,
        testing=results[2].score,
        docs=results[3].score,
        security=results[4].score,
    )

    composite = dims.composite

    if composite >= 85:
        verdict = "approve"
    elif composite >= 70:
        verdict = "improve"
    else:
        verdict = "rework"

    weak_dims = sorted(
        [(d, getattr(dims, d)) for d in ["quality", "alignment", "testing", "docs"]],
        key=lambda x: x[1]
    )
    weakest = weak_dims[0]

    summary = (
        f"Score {composite}/100 — Q:{dims.quality} A:{dims.alignment} "
        f"T:{dims.testing} D:{dims.docs} S:{dims.security} — "
        f"weakest: {weakest[0]} ({weakest[1]})"
    )

    return ReviewResult(
        pr_number=pr_number,
        pr_title=title,
        dimensions=dims,
        composite_score=composite,
        expert_results=results,
        verdict=verdict,
        summary=summary,
        diff_type=diff_type,
    )


def review_pr_from_github(pr_number: int, verbose: bool = False) -> ReviewResult:
    """Fetch PR diff from GitHub and review it."""
    try:
        diff_result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number)],
            capture_output=True, text=True, timeout=30,
        )
        if diff_result.returncode != 0:
            raise RuntimeError(f"gh pr diff failed: {diff_result.stderr}")
        diff = diff_result.stdout

        title_result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title", "-q", ".title"],
            capture_output=True, text=True, timeout=10,
        )
        title = title_result.stdout.strip() or f"PR #{pr_number}"

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise RuntimeError(f"Failed to fetch PR #{pr_number}: {e}")

    return review_pr(diff, title, str(pr_number), verbose=verbose)


def review_all_open(verbose: bool = False) -> list:
    """Review all open PRs that need review."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json",
             "number,title,headRefName,reviewDecision"],
            capture_output=True, text=True, timeout=30,
        )
        prs = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        raise RuntimeError(f"Failed to list PRs: {e}")

    results = []
    for pr in prs:
        num = pr["number"]
        if verbose:
            print(f"\n=== PR #{num}: {pr['title']} ===", flush=True)
        try:
            rr = review_pr_from_github(num, verbose=verbose)
            results.append(rr)
        except RuntimeError as e:
            if verbose:
                print(f"  SKIP: {e}", flush=True)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Local Llama MoE PR Reviewer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr", type=int, help="Review a specific PR by number")
    group.add_argument("--diff-file", type=str, help="Review from a diff file")
    group.add_argument("--all-open", action="store_true", help="Review all open PRs")
    parser.add_argument("--title", default="PR Review", help="PR title (for --diff-file)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.pr:
        result = review_pr_from_github(args.pr, verbose=args.verbose)
        results = [result]
    elif args.diff_file:
        diff = Path(args.diff_file).read_text()
        result = review_pr(diff, args.title, "0", verbose=args.verbose)
        results = [result]
    elif args.all_open:
        results = review_all_open(verbose=args.verbose)

    if args.json:
        output = [r.to_pipeline_dict() for r in results]
        print(json.dumps(output if len(output) > 1 else output[0], indent=2))
    else:
        for r in results:
            print(f"\n{'='*60}")
            print(f"PR #{r.pr_number}: {r.pr_title}")
            print(f"{'='*60}")
            print(f"Diff type: {r.diff_type}")
            print(f"Composite: {r.composite_score}/100  Verdict: {r.verdict.upper()}")
            print(f"  Quality:   {r.dimensions.quality}/100")
            print(f"  Alignment: {r.dimensions.alignment}/100")
            print(f"  Testing:   {r.dimensions.testing}/100")
            print(f"  Docs:      {r.dimensions.docs}/100")
            print(f"  Security:  {r.dimensions.security}/100")
            print(f"\nSummary: {r.summary}")
            for er in r.expert_results:
                if er.feedback and er.score < 80:
                    print(f"\n  [{er.dimension}] Feedback:")
                    for fb in er.feedback:
                        print(f"    - {fb}")


if __name__ == "__main__":
    main()
