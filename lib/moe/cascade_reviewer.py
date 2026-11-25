"""
MoE Cascade Reviewer — Layered 3-Tier Architecture
=====================================================

Layered MoE scoring for PR reviews. Unlike a fallback cascade, all
available tiers contribute to the final score:

    Tier 1: Broly Static Mesh (AST, 8 principles) -- ALWAYS runs, free
    Tier 2: Local Llama 3.2 3B MoE OR Haiku API -- enhancement layer
    Tier 3: Gemini LLM-as-Judge -- calibration on disagreement/borderline

The result merges all tier scores with provenance tracking.

Usage:
    from lib.moe.cascade_reviewer import cascade_review
    result = cascade_review(diff, title, pr_number, verbose=True)
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))

# Size-based fast-track: PRs with fewer total changed lines than this
# skip Tier 2 (LLM) entirely and use mesh-only review (zero cost).
# PR #1097 (18 lines, 1 bash file) triggered full Haiku API — wasteful.
SMALL_PR_LINE_THRESHOLD = 50


def _count_changed_lines(diff: str) -> int:
    """Count total added + removed lines in a unified diff."""
    added = 0
    removed = 0
    for line in diff.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added + removed


# Ensure .github/scripts is importable for broly_mesh_reviewer: this checkout's own,
# never the shared tree's (GRIP#9857).
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / ".github" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Resolve the Haiku-tier id from HAL's canonical catalogue (syscall-doctrine);
# fail-safe verbatim literal when the catalogue is absent, never raises.
_LIB_DIR = str(Path(__file__).resolve().parent.parent)  # own lib (GRIP#9857)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from hal_model_resolver import resolve as _resolve_model  # noqa: E402


@dataclass
class CascadeResult:
    """Result from cascade review with provenance tracking."""
    provider: str  # "llama-local", "haiku-api", "static-ast", "broly-static-mesh", "skipped"
    composite_score: int = 0
    dimension_scores: dict = None
    passed: bool = False
    suggestions: list = None
    skip_reason: str = ""
    reasoning: dict = None  # dimension -> {"score": int, "feedback": [...], ...}

    def __post_init__(self):
        if self.dimension_scores is None:
            self.dimension_scores = {}
        if self.suggestions is None:
            self.suggestions = []
        if self.reasoning is None:
            self.reasoning = {}


def check_llama_available() -> bool:
    """Check if local Llama model is available for scoring."""
    model_path = CLAUDE_DIR / "models" / "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    if not model_path.exists():
        return False
    try:
        import llama_cpp  # noqa: F401
        return True
    except ImportError:
        return False


def check_haiku_available() -> bool:
    """Check if Anthropic API key is configured for Haiku fallback."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _review_with_llama(diff: str, title: str, pr_number: str,
                       verbose: bool = False) -> CascadeResult:
    """Tier 1: Review using local Llama 3.2 3B MoE."""
    if str(Path(__file__).resolve().parent.parent) not in sys.path:  # own lib (GRIP#9857)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from llama_reviewer import review_pr

    result = review_pr(diff, title, pr_number, verbose=verbose)
    return CascadeResult(
        provider="llama-local",
        composite_score=result.composite_score,
        dimension_scores={
            "quality": result.dimensions.quality,
            "alignment": result.dimensions.alignment,
            "testing": result.dimensions.testing,
            "docs": result.dimensions.docs,
            "security": result.dimensions.security,
        },
        passed=result.composite_score >= 80,
        suggestions=[
            fb for er in result.expert_results
            for fb in er.feedback if er.score < 80
        ],
        reasoning={
            er.dimension: {
                "score": er.score,
                "feedback": er.feedback,
            }
            for er in result.expert_results
        },
    )


def _parse_haiku_review(output_file: str) -> CascadeResult:
    """Build a CascadeResult from analyze-pr.py's JSON output.

    Extracted from `_review_with_haiku` (KISS decomposition — JSON parse +
    result assembly is a self-contained unit). Behaviour preserved exactly:
    prefers dimension_reasoning over expert_reasoning over a reasoning shim.
    """
    review = json.loads(Path(output_file).read_text())
    composite = review.get("composite_score", 0)
    haiku_reasoning = review.get(
        "dimension_reasoning",
        review.get(
            "expert_reasoning",
            {"summary": {"feedback": review.get("reasoning", [])}},
        ),
    )
    return CascadeResult(
        provider="haiku-api",
        composite_score=composite,
        dimension_scores=review.get("dimension_scores", {}),
        passed=composite >= 80,
        suggestions=review.get("suggestions", []),
        reasoning=haiku_reasoning,
    )


def _review_with_haiku(diff: str, title: str, pr_number: str,
                       verbose: bool = False) -> CascadeResult:
    """Tier 2: Review using Haiku API via analyze-pr.py."""
    import tempfile

    analyze_script = CLAUDE_DIR / ".github" / "scripts" / "analyze-pr.py"
    if not analyze_script.exists():
        raise RuntimeError("analyze-pr.py not found")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as df:
        df.write(diff)
        diff_file = df.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as of:
        output_file = of.name

    try:
        cmd = [
            sys.executable, str(analyze_script),
            "--diff-file", diff_file,
            "--pr-number", pr_number,
            "--pr-author", "cascade-reviewer",
            "--pr-title", title,
            "--output", output_file,
            "--ralph-mode",
        ]
        env = {**os.environ, "ANTHROPIC_MODEL": _resolve_model(
            "anthropic/claude-haiku-", "claude-haiku-4-5")}
        proc = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=120, env=env)

        if proc.returncode != 0:
            raise RuntimeError(f"analyze-pr.py failed: {proc.stderr[:200]}")

        return _parse_haiku_review(output_file)
    finally:
        Path(diff_file).unlink(missing_ok=True)
        Path(output_file).unlink(missing_ok=True)


def _review_with_mesh(diff: str, title: str, pr_number: str,
                      verbose: bool = False) -> CascadeResult:
    """Tier 1: Broly static mesh (AST, 8 principles). Always available."""
    import re as _re
    from broly_mesh_reviewer import broly_mesh_review
    from lib.diff_classifier import classify_diff

    all_files = _re.findall(r"^\+\+\+ b/(.+)$", diff, _re.MULTILINE)
    categories = {"other": all_files}
    diff_type = classify_diff(diff)
    pr_info_dict = {"number": pr_number, "title": title, "author": "cascade"}

    mesh_result = broly_mesh_review(diff, categories, diff_type, pr_info_dict)
    dims = mesh_result.get("dimension_scores", {})
    mesh_reasoning = mesh_result.get("dimension_reasoning", {})

    # Compute composite from dimensions
    composite = mesh_result.get("composite_score", 0)
    if not composite:
        from lib.pre_review_pipeline import DimensionScores
        ds = DimensionScores(
            quality=dims.get("quality", 0),
            alignment=dims.get("alignment", 0),
            testing=dims.get("testing", 0),
            docs=dims.get("docs", 0),
        )
        composite = ds.composite

    return CascadeResult(
        provider="broly-static-mesh",
        composite_score=composite,
        dimension_scores=dims,
        passed=composite >= 80,
        suggestions=mesh_result.get("suggestions", []),
        reasoning=mesh_reasoning if mesh_reasoning else {
            dim: {"feedback": mesh_result.get("reasoning", [])}
            for dim in dims
        },
    )


def _try_tier2(diff: str, title: str, pr_number: str,
               verbose: bool) -> Optional[CascadeResult]:
    """Tier 2: Try local Llama, then Haiku API as fallback."""
    # subprocess.TimeoutExpired inherits from SubprocessError, not from any of the four
    # classes the tuples below used to name, so a Haiku review that ran past its 120 s
    # deadline escaped as an uncaught exception instead of falling through to the next tier
    # (GRIP#5217 item 1 — a slow reviewer read as a dead cascade).
    if check_llama_available():
        if verbose:
            print("[cascade] Tier 2: Local Llama MoE", flush=True)
        try:
            return _review_with_llama(diff, title, pr_number, verbose)
        except (ImportError, RuntimeError, ValueError, OSError,
                subprocess.TimeoutExpired) as e:
            if verbose:
                print(f"[cascade] Tier 2 Llama failed: {e}", flush=True)

    if check_haiku_available():
        if verbose:
            print("[cascade] Tier 2: Haiku API fallback", flush=True)
        try:
            return _review_with_haiku(diff, title, pr_number, verbose)
        except (ImportError, RuntimeError, ValueError, OSError,
                subprocess.TimeoutExpired) as e:
            if verbose:
                print(f"[cascade] Tier 2 Haiku failed: {e}", flush=True)

    return None


def _try_gemini_calibration(
    t1: CascadeResult, t2: CascadeResult,
    diff: str, title: str, verbose: bool,
) -> Optional[tuple[dict, int, str]]:
    """Tier 3: Gemini judge if tiers disagree or scores are borderline.

    Returns (calibrated_dims, composite, provider_name) or None.
    """
    try:
        if str(Path(__file__).resolve().parent.parent) not in sys.path:  # own lib (GRIP#9857)
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from gemini_judge import (
            check_gemini_available, should_trigger_judge,
            gemini_calibrate, merge_with_judge,
        )
        if not check_gemini_available():
            return None

        should_judge, reason = should_trigger_judge(
            t1.composite_score, t2.composite_score)
        if not should_judge:
            if verbose:
                print("[cascade] Tier 3: Gemini not triggered (scores agree)", flush=True)
            return None

        if verbose:
            print(f"[cascade] Tier 3: Gemini judge ({reason})", flush=True)
        verdict = gemini_calibrate(
            t1.dimension_scores, t2.dimension_scores, diff[:2000],
            pr_title=title, trigger_reason=reason, verbose=verbose,
        )
        if not verdict:
            return None

        dims = merge_with_judge(t1.dimension_scores, t2.dimension_scores, verdict)
        composite = int(sum(
            dims[d] * w
            for d, w in [("quality", 0.30), ("alignment", 0.30),
                         ("testing", 0.25), ("docs", 0.15)]
        ))
        return dims, composite, "gemini-judge"
    except Exception as e:
        if verbose:
            print(f"[cascade] Tier 3 failed: {e}", flush=True)
        return None


def _merge_dim_reasoning(merged: dict, dim: str, info, provider: str) -> None:
    """Merge one Tier-2 reasoning dimension into the Tier-1 base in place.

    Extracted from `_merge_tier_results` (KISS/BIG-O — flattens the inner
    feedback loop out of the per-dimension loop). Behaviour preserved:
    new dim adopted as-is; existing dim with feedback gets Tier-2 feedback
    appended, provider-tagged.
    """
    if dim not in merged:
        merged[dim] = info
        return
    if not (isinstance(info, dict) and "feedback" in info):
        return
    base = merged[dim]
    if isinstance(base, dict) and "feedback" in base:
        base["feedback"] = base["feedback"] + [
            f"[{provider}] {fb}" for fb in info.get("feedback", [])
        ]


def _merge_tier_results(
    t1: CascadeResult, t2: CascadeResult,
    diff: str, title: str, verbose: bool,
) -> CascadeResult:
    """Merge Tier 1 + Tier 2 results, optionally with Tier 3 calibration."""
    # Default merge: 60% Tier 1 (deterministic), 40% Tier 2 (LLM)
    merged_dims = {
        dim: int(t1.dimension_scores.get(dim, 0) * 0.6
                 + t2.dimension_scores.get(dim, 0) * 0.4)
        for dim in ("quality", "alignment", "testing", "docs")
    }
    merged_composite = int(t1.composite_score * 0.6 + t2.composite_score * 0.4)
    providers = [t1.provider, t2.provider]

    # Try Gemini calibration
    calibration = _try_gemini_calibration(t1, t2, diff, title, verbose)
    if calibration:
        merged_dims, merged_composite, provider = calibration
        providers.append(provider)

    # Merge suggestions (deduped)
    seen = set()
    merged_suggestions = []
    for s in t1.suggestions + t2.suggestions:
        if s not in seen:
            seen.add(s)
            merged_suggestions.append(s)

    # Merge reasoning (Tier 1 base, augment with Tier 2 feedback)
    merged_reasoning = dict(t1.reasoning)
    for dim, info in t2.reasoning.items():
        _merge_dim_reasoning(merged_reasoning, dim, info, t2.provider)

    return CascadeResult(
        provider="+".join(providers),
        composite_score=merged_composite,
        dimension_scores=merged_dims,
        passed=merged_composite >= 80,
        suggestions=merged_suggestions[:5],
        reasoning=merged_reasoning,
    )


def cascade_review(diff: str, title: str, pr_number: str = "0",
                   verbose: bool = False) -> CascadeResult:
    """Run layered MoE review: mesh (always) + Llama/Haiku + Gemini judge.

    Architecture:
        Tier 1: Broly static mesh — 8 principles via AST, ALWAYS runs
        Tier 2: Local Llama MoE OR Haiku API — LLM enhancement
        Tier 3: Gemini LLM-as-Judge — calibration on disagreement/borderline

    Returns CascadeResult with merged scores and provenance.
    """
    # Tier 1: Static mesh (always runs, zero cost)
    tier1 = None
    if verbose:
        print("[cascade] Tier 1: Broly static mesh (8 principles)", flush=True)
    try:
        tier1 = _review_with_mesh(diff, title, pr_number, verbose)
    except Exception as e:
        if verbose:
            print(f"[cascade] Tier 1 failed: {e}", flush=True)

    # Size-based fast-track: small PRs use mesh-only (no LLM cost)
    changed_lines = _count_changed_lines(diff)
    if tier1 is not None and changed_lines <= SMALL_PR_LINE_THRESHOLD:
        if verbose:
            print(f"[cascade] Small PR ({changed_lines} lines <= "
                  f"{SMALL_PR_LINE_THRESHOLD}) — mesh-only, skipping LLM tiers",
                  flush=True)
        return tier1

    # Tier 2: LLM enhancement
    tier2 = _try_tier2(diff, title, pr_number, verbose)

    # Route based on available tiers
    if tier1 is None and tier2 is None:
        return CascadeResult(provider="skipped",
                             skip_reason="all tiers failed")
    if tier2 is None:
        return tier1
    if tier1 is None:
        return tier2

    # Both available: merge with optional Tier 3 calibration
    return _merge_tier_results(tier1, tier2, diff, title, verbose)
