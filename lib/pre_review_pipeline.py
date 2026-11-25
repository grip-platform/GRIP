#!/usr/bin/env python3
"""
Broly Recursive Pre-Review Pipeline (RBRP)
==========================================

Recursive self-improving PR pre-flight system.

Pipeline structure (single-pass):
    Level 0 (Thread) : Heuristic gate — instant, free
    Level 1 (Fork)   : 4D composite scoring via analyze-pr.py subprocess
    Level 2 (Council): Dimensional diagnosis — identifies weakest dimension
                        and generates targeted improvement suggestions

Only PRs that exit with composite_score >= 80 proceed to Ralph.
This prevents low-score PRs from consuming Ralph review budget.

Note: Recursion (re-scoring after council diagnosis) was removed because the
recursive calls made identical API requests without injecting council context.
This caused silent failures that zeroed valid first-pass scores (see PR #246).

Usage:
    python3 lib/pre_review_pipeline.py --diff-file pr.diff --pr-number 123 \\
        --pr-title "Title" --pr-author "username"

Exit codes:
    0  = passed (score >= threshold)
    1  = failed (score < fail_below threshold)
    2  = error
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# Resolve repo root: prefer script-relative path (works on CI runners where ~
# is ~, not the checkout dir), fall back to CLAUDE_DIR env var.
REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(REPO_ROOT)))
ANALYZE_SCRIPT = REPO_ROOT / ".github" / "scripts" / "analyze-pr.py"

# Thresholds
PASS_THRESHOLD = 80       # Score >= this: passes to Ralph
WARN_THRESHOLD = 70       # Score 70-79: warn but allow through
FAIL_THRESHOLD = 65       # Score < this: hard fail after recursion
# FAIL_THRESHOLD (65) is used by main() for exit code 1 (hard fail).
# WARN_THRESHOLD (70) is used by run() for borderline pass/fail.


# Dimension weights: quality and alignment are equally critical (30% each),
# testing is high-value but not blocking (25%), docs is supplementary (15%).
DIMENSION_WEIGHTS = {"quality": 0.30, "alignment": 0.30, "testing": 0.25, "docs": 0.15}
# Penalty thresholds: testing below 60 means no meaningful test coverage (-10pts),
# docs below 40 means effectively undocumented (-5pts). These are cliff penalties
# that create a hard floor rather than a linear gradient.
TESTING_PENALTY_THRESHOLD = 60
TESTING_PENALTY = 10
DOCS_PENALTY_THRESHOLD = 40
DOCS_PENALTY = 5


@dataclass
class DimensionScores:
    """4-dimensional PR quality scores with weighted composite calculation."""

    quality: int = 0
    alignment: int = 0
    testing: int = 0
    docs: int = 0

    @property
    def composite(self) -> int:
        """Weighted composite with cliff penalties for critically low dimensions."""
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
        return raw

    @property
    def weakest(self) -> str:
        """Return the dimension name with the lowest score."""
        scores = {
            "quality": self.quality,
            "alignment": self.alignment,
            "testing": self.testing,
            "docs": self.docs,
        }
        return min(scores, key=scores.get)


@dataclass
class PipelineResult:
    level_reached: int
    composite_score: int
    dimension_scores: DimensionScores
    passed: bool
    suggestions: list = field(default_factory=list)
    diagnosis: Optional[str] = None
    recursion_count: int = 0
    raw_review: dict = field(default_factory=dict)
    provider: str = ""  # cascade tier that produced the score
    reasoning: dict = field(default_factory=dict)  # dimension -> reasoning


class PreReviewPipeline:
    """Broly single-pass pre-review pipeline.

    L0 (heuristic) → L1 (API scoring) → L2 (council diagnosis).
    Each level is independently invocable for testing.
    """

    def __init__(self, diff_file: str, pr_number: str, pr_title: str,
                 pr_author: str, verbose: bool = False):
        self.diff_file = diff_file
        self.pr_number = pr_number
        self.pr_title = pr_title
        self.pr_author = pr_author
        self.verbose = verbose

    def run(self) -> PipelineResult:
        """Single-pass pipeline: L0 heuristic → L1 scoring → L2 council diagnosis."""
        self._log("[RBRP] Starting pre-review pipeline")

        # Level 0: Heuristic gate (free, instant)
        heuristic_pass, heuristic_reason = self._level0_heuristic()
        if not heuristic_pass:
            self._log(f"[RBRP:L0] Heuristic fail: {heuristic_reason}")
        else:
            self._log(f"[RBRP:L0] Heuristic pass: {heuristic_reason}")

        # Level 1: Full dimensional scoring (single API call)
        result = self._level1_score()
        self._log(
            f"[RBRP:L1] Composite={result.composite_score}/100 "
            f"(Q={result.dimension_scores.quality} "
            f"A={result.dimension_scores.alignment} "
            f"T={result.dimension_scores.testing} "
            f"D={result.dimension_scores.docs})"
        )

        if result.composite_score >= PASS_THRESHOLD:
            result.passed = True
            result.level_reached = 1
            return result

        # Level 2: Council diagnosis (no API call — deterministic advisory)
        diagnosis, suggestions = self._level2_council_diagnose(result)
        result.diagnosis = diagnosis
        result.suggestions = suggestions
        result.level_reached = 2
        result.passed = result.composite_score >= WARN_THRESHOLD
        self._log(f"[RBRP:L2] Council diagnosis: {diagnosis}")

        return result

    # ── Level 0: Heuristic Gate ───────────────────────────────────────────

    def _level0_heuristic(self) -> tuple[bool, str]:
        """Fast heuristic checks that don't require API calls.

        Returns (passed, reason). Domain-aware: config and docs PRs
        pass without test files since their verification is structural.
        """
        diff_path = Path(self.diff_file)
        if not diff_path.exists():
            return False, "diff file not found"

        diff = diff_path.read_text()
        if not diff.strip():
            return False, "empty diff"

        # Classify diff type for domain-aware heuristics
        from lib.diff_classifier import DOC_EXTENSIONS, CONFIG_EXTENSIONS
        files = [
            line[6:] for line in diff.split("\n")
            if line.startswith("+++ b/")
        ]
        doc_exts = DOC_EXTENSIONS
        config_exts = CONFIG_EXTENSIONS
        has_tests = "test_" in diff or "/tests/" in diff or "def test_" in diff

        if not files:
            return True, "no file headers detected (pass through)"

        total = len(files)
        doc_count = sum(1 for f in files if Path(f).suffix in doc_exts)
        config_count = sum(1 for f in files if Path(f).suffix in config_exts)

        # Docs-only and config-only PRs pass without test files
        if doc_count / total >= 0.7:
            return True, "docs-dominant PR (verification via structure)"
        if config_count / total >= 0.7:
            return True, "config-dominant PR (verification via consistency)"
        if has_tests:
            return True, "test files present"

        return False, "no test files modified (pre-screen warning)"

    # ── Level 1: Dimensional Scoring ──────────────────────────────────────

    def _level1_score(self) -> PipelineResult:
        """Run analyze-pr.py and extract 4D dimensional scores."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_file = f.name

        try:
            cmd = [
                sys.executable, str(ANALYZE_SCRIPT),
                "--diff-file", self.diff_file,
                "--pr-number", self.pr_number,
                "--pr-author", self.pr_author,
                "--pr-title", self.pr_title,
                "--output", output_file,
                "--ralph-mode",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ},
            )

            if result.returncode != 0:
                error_detail = result.stderr.strip() or result.stdout.strip()
                self._log(f"[RBRP:L1] analyze-pr.py failed: {error_detail[:200]}")
                # Fail-safe: do NOT silently pass bad PRs through
                return PipelineResult(
                    level_reached=1,
                    composite_score=0,
                    dimension_scores=DimensionScores(0, 0, 0, 0),
                    passed=False,
                    diagnosis=f"analyze-pr.py failed (exit {result.returncode}) — scoring unavailable",
                )

            review = json.loads(Path(output_file).read_text())
            dims_raw = review.get("dimension_scores", {})
            dims = DimensionScores(
                quality=dims_raw.get("quality", review.get("quality_score",
                                     review.get("alignment_score", 0))),
                alignment=review.get("alignment_score", 0),
                testing=dims_raw.get("testing", review.get("testing_score",
                                     review.get("alignment_score", 0))),
                docs=dims_raw.get("docs", review.get("doc_score",
                                  review.get("alignment_score", 0))),
            )
            composite = review.get("composite_score", dims.composite)

            return PipelineResult(
                level_reached=1,
                composite_score=composite,
                dimension_scores=dims,
                passed=composite >= PASS_THRESHOLD,
                raw_review=review,
                reasoning=review.get("dimension_reasoning", {}),
            )

        except subprocess.TimeoutExpired:
            return PipelineResult(
                level_reached=1,
                composite_score=0,
                dimension_scores=DimensionScores(0, 0, 0, 0),
                passed=False,
                diagnosis="Scoring timeout — manual review required",
            )
        except (json.JSONDecodeError, FileNotFoundError) as e:
            return PipelineResult(
                level_reached=1,
                composite_score=0,
                dimension_scores=DimensionScores(0, 0, 0, 0),
                passed=False,
                diagnosis=f"Scoring error: {e} — manual review required",
            )
        finally:
            Path(output_file).unlink(missing_ok=True)

    # ── Level 2: Council Diagnosis ────────────────────────────────────────

    def _level2_council_diagnose(self, result: PipelineResult) -> tuple[str, list[str]]:
        """Council-style multi-perspective diagnosis of score gaps.

        Each 'council member' focuses on one dimension weakness.
        Returns (diagnosis_summary, [actionable_suggestions]).
        """
        dims = result.dimension_scores
        weakest = dims.weakest
        composite = result.composite_score

        # Council members: each provides dimension-specific guidance
        council_opinions = []

        if dims.testing < 75:
            council_opinions.append(
                "Testing advisor: Add integration tests that verify actual output values, "
                "not just mock call counts. Tests should fail when logic is wrong."
            )

        if dims.quality < 75:
            council_opinions.append(
                "Quality advisor: Review for edge cases, error handling, and consistency "
                "with existing patterns in lib/. Check for DRY violations."
            )

        if dims.alignment < 75:
            council_opinions.append(
                "Alignment advisor: Ensure SOLID/GRASP compliance. Single Responsibility "
                "violations are the most common cause of alignment score drops."
            )

        if dims.docs < 65:
            council_opinions.append(
                "Docs advisor: Add docstrings to public functions. Update CLAUDE.md or "
                "relevant docs/ if behaviour changes."
            )

        # Devil's advocate: challenge the overall approach
        if composite < WARN_THRESHOLD:
            council_opinions.append(
                f"Devil's advocate: Score {composite}/100 is below warning threshold. "
                f"Weakest dimension is {weakest} ({getattr(dims, weakest)}/100). "
                "Consider whether the implementation approach itself needs rethinking."
            )

        suggestions = []
        for opinion in council_opinions:
            # Extract actionable suggestion (everything after the colon)
            if ":" in opinion:
                suggestions.append(opinion.split(":", 1)[1].strip())

        diagnosis = (
            f"Score {composite}/100 — weakest dimension: {weakest} "
            f"({getattr(dims, weakest)}/100). "
            f"{len(council_opinions)} council advisors flagged issues."
        )

        return diagnosis, suggestions

    def _log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)


def compute_composite_from_review(review: dict) -> int:
    """Single source of truth for composite score calculation.

    Used by analyze-pr.py and post-review.py — never duplicate this logic.
    Accepts a review dict from the Claude API scorer. Falls back to
    alignment_score when individual dimension scores are absent.

    Includes +3 bonus for user-facing PRs with all dimensions >= 85.
    """
    if "composite_score" in review:
        return review["composite_score"]
    fallback = review.get("alignment_score", 0)
    q = review.get("quality_score", fallback)
    a = review.get("alignment_score", 0)
    t = review.get("testing_score", fallback)
    d = review.get("doc_score", fallback)
    dims = DimensionScores(quality=q, alignment=a, testing=t, docs=d)
    score = dims.composite
    # User-facing bonus: +3 when all dimensions strong (rewards well-rounded PRs)
    if review.get("is_user_facing") and all(s >= 85 for s in [q, a, t, d]):
        score = min(100, score + 3)
    return score


def main():
    parser = argparse.ArgumentParser(description="Broly Recursive Pre-Review Pipeline")
    parser.add_argument("--diff-file", required=True)
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--pr-title", required=True)
    parser.add_argument("--pr-author", required=True)
    parser.add_argument("--output", help="Write result JSON to this file")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--fail-below", type=int, default=FAIL_THRESHOLD,
                        help=f"Exit code 1 if score < this (default {FAIL_THRESHOLD})")
    parser.add_argument("--local", action="store_true", default=True,
                        help="Use cascade MoE reviewer (default)")
    parser.add_argument("--api", action="store_true",
                        help="Use Anthropic API reviewer (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Skip review cache lookup (force fresh review)")
    args = parser.parse_args()

    if not args.api:
        # Cascade MoE review with optional cache
        sys.path.insert(0, str(REPO_ROOT / "lib"))
        diff_text = Path(args.diff_file).read_text()

        # Check review cache first (unless --no-cache)
        cached_result = None
        review_cache = None
        if not args.no_cache:
            try:
                from moe.review_cache import ReviewCache
                review_cache = ReviewCache()
                cached = review_cache.lookup(diff_text)
                if cached:
                    if args.verbose:
                        print("[pipeline] Cache hit — reusing previous score",
                              flush=True)
                    cached_result = PipelineResult(
                        level_reached=1,
                        composite_score=cached.get("composite_score", 0),
                        dimension_scores=DimensionScores(
                            **{k: cached.get("dimension_scores", {}).get(k, 0)
                               for k in ["quality", "alignment", "testing", "docs"]}
                        ),
                        passed=cached.get("passed", False),
                        suggestions=cached.get("suggestions", []),
                    )
            except ImportError:
                pass

        if cached_result:
            result = cached_result
        else:
            # Cascade: Llama -> Haiku -> pipeline fallback
            try:
                from moe.cascade_reviewer import cascade_review
                cr = cascade_review(diff_text, args.pr_title, args.pr_number,
                                    verbose=args.verbose)
                if cr.provider != "skipped":
                    dims = cr.dimension_scores
                    result = PipelineResult(
                        level_reached=1,
                        composite_score=cr.composite_score,
                        dimension_scores=DimensionScores(
                            quality=dims.get("quality", 0),
                            alignment=dims.get("alignment", 0),
                            testing=dims.get("testing", 0),
                            docs=dims.get("docs", 0),
                        ),
                        passed=cr.passed,
                        suggestions=cr.suggestions,
                        provider=cr.provider,
                        reasoning=cr.reasoning or {},
                    )
                    # Store in cache for future lookups
                    if review_cache:
                        review_cache.store(diff_text, {
                            "composite_score": cr.composite_score,
                            "dimension_scores": cr.dimension_scores,
                            "passed": cr.passed,
                            "suggestions": cr.suggestions,
                            "provider": cr.provider,
                        })
                else:
                    # All scorers unavailable — fall through to pipeline
                    if args.verbose:
                        print("[pipeline] Cascade skipped, falling back to API pipeline",
                              flush=True)
                    pipeline = PreReviewPipeline(
                        diff_file=args.diff_file, pr_number=args.pr_number,
                        pr_title=args.pr_title, pr_author=args.pr_author,
                        verbose=args.verbose,
                    )
                    result = pipeline.run()
            except ImportError:
                if args.verbose:
                    print("[pipeline] cascade_reviewer not available, using pipeline",
                          flush=True)
                pipeline = PreReviewPipeline(
                    diff_file=args.diff_file, pr_number=args.pr_number,
                    pr_title=args.pr_title, pr_author=args.pr_author,
                    verbose=args.verbose,
                )
                result = pipeline.run()
    else:
        pipeline = PreReviewPipeline(
            diff_file=args.diff_file,
            pr_number=args.pr_number,
            pr_title=args.pr_title,
            pr_author=args.pr_author,
            verbose=args.verbose,
        )

        result = pipeline.run()

    # Spread analysis for attractor detection
    dim_values = [
        result.dimension_scores.quality,
        result.dimension_scores.alignment,
        result.dimension_scores.testing,
        result.dimension_scores.docs,
    ]
    mean_val = sum(dim_values) / len(dim_values) if dim_values else 0
    variance = sum((v - mean_val) ** 2 for v in dim_values) / len(dim_values) if dim_values else 0
    stdev = variance ** 0.5
    attractor_band = all(85 <= v <= 92 for v in dim_values)

    output = {
        "composite_score": result.composite_score,
        "passed": result.passed,
        "level_reached": result.level_reached,
        "recursion_count": result.recursion_count,
        "dimension_scores": asdict(result.dimension_scores),
        "diagnosis": result.diagnosis,
        "suggestions": result.suggestions,
        "provider": result.provider,
        "reasoning": result.reasoning,
        "spread_analysis": {
            "stdev": round(stdev, 1),
            "attractor_band": attractor_band,
            "close_pairs": [
                f"{d1}:{d2}"
                for i, d1 in enumerate(["quality", "alignment", "testing", "docs"])
                for j, d2 in enumerate(["quality", "alignment", "testing", "docs"])
                if i < j and abs(dim_values[i] - dim_values[j]) <= 2
            ],
        },
    }

    print(json.dumps(output, indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(output, indent=2))

    if result.composite_score < args.fail_below:
        sys.exit(1)


def _safe_main():
    """Wrapper that ensures pipeline_result.json is written even on crash."""
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        failure = {
            "composite_score": 0,
            "passed": False,
            "level_reached": 0,
            "recursion_count": 0,
            "dimension_scores": {"quality": 0, "alignment": 0, "testing": 0, "docs": 0},
            "diagnosis": f"Pipeline error: {e}",
            "suggestions": ["Fix the pipeline error and re-push."],
        }
        print(json.dumps(failure, indent=2), file=sys.stderr)
        # Write to --output if it was parsed before the crash
        for i, arg in enumerate(sys.argv):
            if arg == "--output" and i + 1 < len(sys.argv):
                Path(sys.argv[i + 1]).write_text(json.dumps(failure, indent=2))
                break
        sys.exit(2)


if __name__ == "__main__":
    _safe_main()
