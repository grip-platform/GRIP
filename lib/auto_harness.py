"""Auto-harness synthesis for GRIP safety gates.

Adapted from AutoHarness (arXiv:2603.03329v1).
Synthesises is_legal_tool_call() functions via converge().
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_DIR = Path(__file__).parent.parent / "harnesses"


@dataclass
class HarnessNode:
    """A node in the harness search tree."""

    code: str
    heuristic: float = 0.0
    children: list[HarnessNode] = field(default_factory=list)
    parent: HarnessNode | None = None


@dataclass
class HarnessSpec:
    """Specification for harness synthesis."""

    name: str
    description: str
    examples: list[dict]  # {tool, args, context, legal: bool}
    function_sig: str = (
        "def is_legal_tool_call(tool: str, args: dict, context: dict) -> bool"
    )


def evaluate_harness(code: str, examples: list[dict]) -> float:
    """Evaluate harness code against examples. Returns legal action rate [0, 1]."""
    if not code.strip():
        return 0.0
    namespace: dict = {}
    try:
        exec(code, namespace)  # noqa: S102
    except Exception:
        return 0.0
    fn = namespace.get("is_legal_tool_call")
    if not callable(fn):
        return 0.0
    correct = 0
    for ex in examples:
        try:
            result = fn(ex["tool"], ex.get("args", {}), ex.get("context", {}))
            if bool(result) == bool(ex["legal"]):
                correct += 1
        except Exception:
            pass
    return correct / max(len(examples), 1)


def synthesise_harness(spec: HarnessSpec, max_iterations: int = 20) -> str:
    """Synthesise a harness via iterative refinement.

    This is the non-LLM skeleton. In production, the refine step
    calls an LLM to improve the code based on failed examples.
    Returns the best harness code found.
    """
    root = HarnessNode(
        code=f"{spec.function_sig}:\n    return True  # default: allow all"
    )
    root.heuristic = evaluate_harness(root.code, spec.examples)
    best = root

    for _i in range(max_iterations):
        if best.heuristic >= 1.0:
            break
        # In production: LLM refines code here based on failed examples
        # For now, return the skeleton for manual or LLM-driven refinement

    return best.code


def load_examples(path: Path) -> list[dict]:
    """Load harness training examples from JSONL."""
    examples: list[dict] = []
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                examples.append(json.loads(line))
    return examples


def save_harness(name: str, code: str) -> Path:
    """Save synthesised harness to disk."""
    HARNESS_DIR.mkdir(exist_ok=True)
    path = HARNESS_DIR / f"{name}.py"
    path.write_text(code)
    return path
