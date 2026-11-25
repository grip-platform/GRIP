#!/usr/bin/env python3
"""
principled_scorer.py -- LLM-based design principle evaluation for GRIP.

Uses LlamaEngine to score code diffs against design principles.
Blends with static analysis from principles_criterion.py for hybrid scoring.

Integration:
    PrincipledScorer -> Cube.record(domain, confidence=score)

FEEDBACK LOOP:
    Code -> LlamaEngine scores -> Cube state -> Gates/instructions -> Claude -> Code

GOODHART PROTECTION:
    1. Llama evaluates (independent evaluator), Claude produces (separate actor)
    2. Hybrid blend (40% static + 60% LLM) -- static anchors prevent pure LLM gaming
    3. LLM failure returns 0.5 (neutral) -- prevents gaming by inducing failures
    4. Scores clamped to [0,1] -- prevents outlier inflation
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# -- Data classes -----------------------------------------------------------

@dataclass
class PrincipleScore:
    """Score for a single design principle evaluation.

    score: 0.0-1.0 where 1.0 = perfectly principled.
    reasoning: LLM's explanation (audit trail).
    violations: Specific violations found.
    """
    principle: str
    score: float
    reasoning: str = ""
    violations: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        """Whether the score meets the passing threshold (0.7)."""
        return self.score >= 0.7


@dataclass
class DiffEvaluation:
    """Complete evaluation of a code diff across all principles."""
    diff_hash: str
    scores: dict[str, PrincipleScore] = field(default_factory=dict)
    composite_score: float = 0.0
    session_id: str = ""

    # Weights match PrinciplesReport.composite_score exactly
    WEIGHTS = {
        "DRY": 0.20, "KISS": 0.15, "SOLID": 0.20,
        "GRASP": 0.15, "YAGNI": 0.15, "YSH": 0.15,
    }

    def compute_composite(self) -> float:
        """Weighted average matching PrinciplesReport weights."""
        total = 0.0
        for name, ps in self.scores.items():
            total += self.WEIGHTS.get(name, 0.15) * ps.score
        self.composite_score = total
        return total


# -- Scorer -----------------------------------------------------------------

class PrincipledScorer:
    """Evaluates code diffs against GRIP design principles using local Llama.

    Each evaluation produces:
    1. Per-principle scores (DRY, KISS, SOLID, GRASP, YAGNI, YSH)
    2. Reasoning traces (audit trail)
    3. Cube.record() calls with confidence = score

    Blends:
    - Static analysis (from principles_criterion.py) -- fast, syntactic
    - LLM analysis (from LlamaEngine) -- slower, semantic
    - Default: 0.4 * static + 0.6 * LLM
    """

    PRINCIPLES = ("DRY", "KISS", "SOLID", "GRASP", "YAGNI", "YSH")

    PROMPT_TEMPLATE = """Evaluate this code diff against the {principle} design principle.

{principle_description}

Code diff:
```
{diff}
```

Respond in JSON:
{{"score": <0.0-1.0>, "reasoning": "brief explanation", "violations": ["list of specific violations"]}}

Score 1.0 = perfectly follows {principle}. Score 0.0 = completely violates it.
Be conservative -- most real code scores 0.5-0.8."""

    PRINCIPLE_DESCRIPTIONS = {
        "DRY": "Don't Repeat Yourself. No duplicated logic, data, or structure. Each piece of knowledge has a single, unambiguous representation.",
        "KISS": "Keep It Simple. Minimal complexity, no over-engineering. The simplest solution that works.",
        "SOLID": "Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, Dependency Inversion.",
        "GRASP": "General Responsibility Assignment. Clear ownership of behaviour. Low coupling, high cohesion.",
        "YAGNI": "You Aren't Gonna Need It. No speculative features, unused code, or premature abstractions.",
        "YSH": "You Should Have. When a pattern appears 3+ times, it should already be abstracted. Abstraction debt.",
    }

    def __init__(self, llama_engine=None, static_weight: float = 0.4):
        """Initialise scorer with optional engine and blend weight.

        Args:
            llama_engine: LlamaEngine instance (created lazily if None).
            static_weight: Weight for static analysis (0.0-1.0). LLM weight = 1 - static.
        """
        self._engine = llama_engine
        self._static_weight = static_weight
        self._llm_weight = 1.0 - static_weight

    def _get_engine(self):
        """Lazy engine initialisation."""
        if self._engine is None:
            from lib.llama_engine import LlamaEngine
            self._engine = LlamaEngine()
        return self._engine

    def score_diff(self, diff: str, session_id: str = "") -> DiffEvaluation:
        """Score a git diff against all 6 principles using LLM only.

        Complexity: O(p * n) where p=6 (constant), n=diff length (capped at 2000).
        Effectively O(n).

        Args:
            diff: Git diff text.
            session_id: Session identifier for Cube tracking.

        Returns:
            DiffEvaluation with per-principle scores.
        """
        diff_hash = hashlib.sha256(diff.encode()).hexdigest()[:16]
        evaluation = DiffEvaluation(diff_hash=diff_hash, session_id=session_id)

        truncated = diff[:2000]
        engine = self._get_engine()

        for principle in self.PRINCIPLES:
            prompt = self.PROMPT_TEMPLATE.format(
                principle=principle,
                principle_description=self.PRINCIPLE_DESCRIPTIONS[principle],
                diff=truncated,
            )

            try:
                raw = engine.generate(prompt, json_mode=True, max_tokens=256)
                parsed = json.loads(raw)
                score = max(0.0, min(1.0, float(parsed.get("score", 0.5))))
                reasoning = str(parsed.get("reasoning", ""))
                violations = list(parsed.get("violations", []))
            except Exception as e:
                logger.warning("LLM evaluation failed for %s: %s", principle, e)
                score = 0.5  # Neutral on failure (Goodhart protection)
                reasoning = f"LLM evaluation failed: {e}"
                violations = []

            evaluation.scores[principle] = PrincipleScore(
                principle=principle,
                score=score,
                reasoning=reasoning,
                violations=violations,
            )

        evaluation.compute_composite()
        return evaluation

    def score_diff_hybrid(
        self, diff: str, path: str = ".", session_id: str = ""
    ) -> DiffEvaluation:
        """Hybrid scoring: static analysis + LLM evaluation.

        Blends static scores from principles_criterion with LLM semantic scores.
        This is the primary scoring method.

        Args:
            diff: Git diff text.
            path: Codebase path for static analysis.
            session_id: Session identifier.

        Returns:
            DiffEvaluation with blended scores.
        """
        # LLM evaluation
        evaluation = self.score_diff(diff, session_id)

        # Static analysis (fast, syntactic)
        try:
            from lib.principles_criterion import principles_scan
            static_report = principles_scan(path)
            static_scores = {
                "DRY": static_report.dry_score,
                "KISS": static_report.kiss_score,
                "SOLID": static_report.solid_score,
                "GRASP": static_report.grasp_score,
                "YAGNI": static_report.yagni_score,
                "YSH": static_report.ysh_score,
            }
        except Exception as e:
            logger.warning("Static analysis failed, using LLM-only scores: %s", e)
            return evaluation

        # Blend: static_weight * static + llm_weight * llm
        for principle in self.PRINCIPLES:
            if principle in evaluation.scores and principle in static_scores:
                llm_score = evaluation.scores[principle].score
                static_score = static_scores[principle]
                blended = self._static_weight * static_score + self._llm_weight * llm_score
                evaluation.scores[principle].score = max(0.0, min(1.0, blended))

        evaluation.compute_composite()
        return evaluation

    def record_to_cube(self, evaluation: DiffEvaluation, cube=None) -> object:
        """Record evaluation scores into the Cube.

        Integration point: PrincipledScorer -> Cube.record()
        Mirrors cube_session_wire.py wire_principles() pattern.

        Args:
            evaluation: DiffEvaluation to record.
            cube: Optional Cube instance (loaded from cache if None).

        Returns:
            The Cube instance (for chaining or inspection).
        """
        from lib.cube import ConvergenceDepth

        if cube is None:
            from lib.cube_session_wire import _load_cube, _save_cube
            cube = _load_cube()
            should_save = True
        else:
            should_save = False

        for principle, ps in evaluation.scores.items():
            # Map score to convergence depth
            if ps.score >= 0.7:
                cd = ConvergenceDepth.CODIFIED
            elif ps.score >= 0.5:
                cd = ConvergenceDepth.UNDERSTOOD
            else:
                cd = ConvergenceDepth.DETECTED

            cube.record(
                domain=principle,
                session_id=evaluation.session_id or "llama-scorer",
                confidence=ps.score,
                convergence_depth=cd,
            )

        if should_save:
            _save_cube(cube)

        return cube
