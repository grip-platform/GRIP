"""
GRIP Safety Gates Library

Deterministic safety gates that the agent cannot override.

Usage:
    from lib.gates import evaluate_gates, EvaluationContext

    ctx = EvaluationContext(
        tool_name="Bash",
        tool_input={"command": "rm -rf /"},
        session_id="test",
        confidence=0.5,
    )
    result = evaluate_gates(ctx)
    
    if result.decision == "deny":
        print(f"BLOCKED: {result.reason}")
"""

from .evaluator import (
    evaluate_gates,
    EvaluationContext,
    GateResult,
    Gate,
    GateCondition,
    load_gates,
)

from .rsi_proposals import (
    propose_gate,
    approve_proposal,
    reject_proposal,
    list_pending_proposals,
    GateProposal,
)

from .bash_orchestrator import run_bash_gates
from .context_orchestrator import run_context_gate

__all__ = [
    "evaluate_gates",
    "EvaluationContext",
    "GateResult",
    "Gate",
    "GateCondition",
    "load_gates",
    "propose_gate",
    "approve_proposal",
    "reject_proposal",
    "list_pending_proposals",
    "GateProposal",
    "run_bash_gates",  # #933
    "run_context_gate",  # #933
]
