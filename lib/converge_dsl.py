"""
converge_dsl.py — Declarative YAML-based convergence DSL (#226).

Translates YAML convergence definitions into composable converge() pipelines.
Non-programmers can define convergence workflows as configuration.

Usage:
    from lib.converge_dsl import load_pipeline, run_pipeline

    # From YAML string
    pipeline = load_pipeline(yaml_text)

    # From file
    pipeline = load_pipeline_file("convergence/pr-quality.yaml")

    # Execute
    result = run_pipeline(pipeline, initial_state={"score": 0, "tests": 0})

YAML format:
    name: pr-quality
    criterion:
      all_of:
        - threshold: {key: score, min: 85}
        - threshold: {key: tests, min: 95}
    improve:
      pipeline:
        - focus: {key: score, step: 10}
        - focus: {key: tests, step: 5}
    max_depth: 5
    velocity:
      enabled: true
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.converge import converge, ConvergenceResult
from lib.converge_combinators import (
    all_of,
    any_of,
    threshold,
    weighted_threshold,
    pipeline,
    fallback,
    focus,
    composite_scorer,
    VelocityTracker,
)

# Try to import yaml; fall back to basic parsing if unavailable
try:
    import yaml as _yaml
    def _parse_yaml(text: str) -> dict:
        return _yaml.safe_load(text)
except ImportError:
    _yaml = None
    import json
    def _parse_yaml(text: str) -> dict:
        """Minimal YAML subset parser — handles simple key: value and lists."""
        # For environments without PyYAML, accept JSON as fallback
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ImportError(
                "PyYAML not installed. Install with: pip install pyyaml\n"
                "Alternatively, use JSON format for convergence definitions."
            )


# =========================================================================
# Strategy Registry — maps names to callables
# =========================================================================

_STRATEGY_REGISTRY: dict[str, Callable] = {}


def register_strategy(name: str, fn: Callable) -> None:
    """Register a named strategy for use in YAML definitions.

    Args:
        name: Strategy name referenced in YAML.
        fn: Callable that takes a value and returns improved value.
    """
    _STRATEGY_REGISTRY[name] = fn


def _get_strategy(name: str) -> Callable:
    """Look up a registered strategy by name."""
    if name in _STRATEGY_REGISTRY:
        return _STRATEGY_REGISTRY[name]
    raise ValueError(f"Unknown strategy: '{name}'. Register with register_strategy() first.")


# Built-in strategies
def _increment_strategy(step: float = 1.0) -> Callable:
    """Built-in: increment a value by step."""
    return lambda v: v + step


def _multiply_strategy(factor: float = 1.1) -> Callable:
    """Built-in: multiply a value by factor."""
    return lambda v: v * factor


# Register built-ins
register_strategy("increment", _increment_strategy())
register_strategy("double", _multiply_strategy(2.0))


# =========================================================================
# Pipeline Definition
# =========================================================================

@dataclass
class ConvergencePipeline:
    """Parsed convergence pipeline from YAML."""
    name: str
    criterion_fn: Callable
    improve_fn: Callable
    scorer_fn: Optional[Callable] = None
    max_depth: int = 5
    velocity_enabled: bool = False
    stall_strategy_name: Optional[str] = None
    raw_config: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"ConvergencePipeline(name={self.name!r}, max_depth={self.max_depth})"


# =========================================================================
# YAML -> Criterion
# =========================================================================

def _build_criterion(spec: dict) -> Callable:
    """Build a criterion function from YAML spec.

    Supported forms:
        threshold: {key: "score", min: 85}
        all_of: [criterion, criterion, ...]
        any_of: [criterion, criterion, ...]
        weighted: {weights: {a: 0.5, b: 0.5}, min: 0.8}
    """
    if "threshold" in spec:
        t = spec["threshold"]
        return threshold(t["key"], t["min"])

    if "all_of" in spec:
        criteria = [_build_criterion(c) for c in spec["all_of"]]
        return all_of(*criteria)

    if "any_of" in spec:
        criteria = [_build_criterion(c) for c in spec["any_of"]]
        return any_of(*criteria)

    if "weighted" in spec:
        w = spec["weighted"]
        return weighted_threshold(w["weights"], w["min"])

    raise ValueError(f"Unknown criterion spec: {spec}")


# =========================================================================
# YAML -> Improve
# =========================================================================

def _build_improve(spec: dict) -> Callable:
    """Build an improve function from YAML spec.

    Supported forms:
        focus: {key: "score", step: 10}
        focus: {key: "score", strategy: "my_strategy"}
        pipeline: [improve, improve, ...]
        fallback: [improve, improve, ...]
    """
    if "focus" in spec:
        f = spec["focus"]
        key = f["key"]
        if "step" in f:
            return focus(key, lambda v, s=f["step"]: v + s)
        if "strategy" in f:
            strategy = _get_strategy(f["strategy"])
            return focus(key, strategy)
        if "factor" in f:
            return focus(key, lambda v, fac=f["factor"]: v * fac)
        raise ValueError(f"focus spec needs 'step', 'strategy', or 'factor': {f}")

    if "pipeline" in spec:
        improvers = [_build_improve(i) for i in spec["pipeline"]]
        return pipeline(*improvers)

    if "fallback" in spec:
        improvers = [_build_improve(i) for i in spec["fallback"]]
        return fallback(*improvers)

    raise ValueError(f"Unknown improve spec: {spec}")


# =========================================================================
# Load and Run
# =========================================================================

def load_pipeline(yaml_text: str) -> ConvergencePipeline:
    """Parse a YAML convergence definition into a ConvergencePipeline.

    Args:
        yaml_text: YAML or JSON string defining the convergence pipeline.

    Returns:
        ConvergencePipeline ready to execute with run_pipeline().
    """
    config = _parse_yaml(yaml_text)

    name = config.get("name", "unnamed")
    max_depth = config.get("max_depth", 5)

    # Build criterion
    criterion_spec = config.get("criterion", {})
    criterion_fn = _build_criterion(criterion_spec)

    # Build improve
    improve_spec = config.get("improve", {})
    improve_fn = _build_improve(improve_spec)

    # Build scorer (optional)
    scorer_fn = None
    scorer_spec = config.get("scorer", None)
    if scorer_spec and isinstance(scorer_spec, dict):
        scorer_fn = composite_scorer(scorer_spec)

    # Velocity config
    velocity_spec = config.get("velocity", {})
    velocity_enabled = velocity_spec.get("enabled", False) if isinstance(velocity_spec, dict) else False
    stall_strategy = velocity_spec.get("stall_strategy") if isinstance(velocity_spec, dict) else None

    return ConvergencePipeline(
        name=name,
        criterion_fn=criterion_fn,
        improve_fn=improve_fn,
        scorer_fn=scorer_fn,
        max_depth=max_depth,
        velocity_enabled=velocity_enabled,
        stall_strategy_name=stall_strategy,
        raw_config=config,
    )


def load_pipeline_file(path: str) -> ConvergencePipeline:
    """Load a convergence pipeline from a YAML file."""
    return load_pipeline(Path(path).read_text())


def run_pipeline(
    pipe: ConvergencePipeline,
    initial_state: dict,
    label: Optional[str] = None,
) -> dict:
    """Execute a convergence pipeline.

    Args:
        pipe: Parsed ConvergencePipeline.
        initial_state: Starting state dict.
        label: Optional label for tracking.

    Returns:
        Dict with result, velocity data, and pipeline metadata.
    """
    improve = pipe.improve_fn

    # Wrap with velocity tracker if enabled
    tracker = None
    if pipe.velocity_enabled and pipe.scorer_fn:
        tracker = VelocityTracker(pipe.scorer_fn)
        stall_fn = None
        if pipe.stall_strategy_name:
            try:
                stall_fn = _get_strategy(pipe.stall_strategy_name)
            except ValueError:
                pass
        improve = tracker.as_improve_wrapper(improve, stall_strategy=stall_fn)

    result = converge(
        state=initial_state,
        criterion=pipe.criterion_fn,
        improve=improve,
        max_depth=pipe.max_depth,
        _scorer=pipe.scorer_fn,
        label=label or pipe.name,
    )

    output = {
        "name": pipe.name,
        "satisfied": result.satisfied,
        "iterations": result.iterations,
        "depth_reached": result.depth_reached,
        "best_state": result.best_state,
        "final_state": result.final_state,
        "elapsed_sec": result.elapsed_sec,
    }

    if tracker:
        output["velocity"] = {
            "velocity": tracker.velocity,
            "acceleration": tracker.acceleration,
            "is_stalling": tracker.is_stalling,
        }

    return output
