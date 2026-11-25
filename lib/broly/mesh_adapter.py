"""
mesh_adapter.py — Thin adapter: Broly council outputs -> MeshResponse format.

Each council member IS a MeshNode. Their analysis IS a MeshResponse.
This adapter makes that fractal equivalence explicit without modifying
consensus.py or orchestrator.py.

Usage:
    from lib.broly.mesh_adapter import agent_analysis_to_mesh_response
    from lib.broly.mesh_adapter import consensus_to_mesh_response
    from lib.broly.mesh_adapter import ROLE_CAPABILITIES
"""

from __future__ import annotations

from typing import Any

from .consensus import ConsensusResult, ConsensusType

# Maps council roles to MeshNode capability tags.
# Derived from the canonical Broly council roles.
ROLE_CAPABILITIES: dict[str, list[str]] = {
    "pragmatist":  ["implementation", "feasibility", "trade-offs"],
    "theorist":    ["architecture", "correctness", "formal-reasoning"],
    "security":    ["threat-modelling", "vulnerability-analysis", "audit"],
    "ux":          ["usability", "user-journey", "accessibility"],
    "devil":       ["adversarial", "edge-cases", "stress-testing"],
    "integrator":  ["cross-system", "compatibility", "migration"],
}


def agent_analysis_to_mesh_response(
    agent_role: str,
    analysis_text: str,
    confidence: float,
    task_id: str,
    latency_ms: int = 0,
) -> dict[str, Any]:
    """Convert a single council member's output to MeshResponse format.

    Args:
        agent_role:    The council role (e.g. "pragmatist", "devil").
        analysis_text: The agent's analysis or recommendation text.
        confidence:    Agent's self-reported confidence in [0.0, 1.0].
        task_id:       Correlation ID — matches the originating MeshQuery.
        latency_ms:    Optional wall-clock execution time in milliseconds.

    Returns:
        A MeshResponse-compatible dict ready for mesh protocol consumers.
    """
    clamped = max(0.0, min(1.0, confidence))
    capabilities = ROLE_CAPABILITIES.get(agent_role, ["analysis"])

    return {
        "type": "mesh_response",
        "query_id": task_id,
        "responder": agent_role,
        "capabilities": capabilities,
        "results": [
            {
                "content": analysis_text,
                "similarity": clamped,
                "embed_type": "agent_analysis",
                "success_score": clamped,
            }
        ],
        "offered_genes": [],
        "latency_ms": latency_ms,
    }


def consensus_to_mesh_response(
    consensus_result: ConsensusResult,
    task_id: str,
) -> dict[str, Any]:
    """Convert a ConsensusResult (Council.vote() output) to a merged MeshResponse.

    The merged response represents the council's collective decision:
    - success_score reflects threshold_achieved
    - passed / consensus_type are surfaced in metadata
    - dissenting agents appear in a separate results entry if present

    Args:
        consensus_result: Output of Council.vote().
        task_id:          Correlation ID — matches the originating MeshQuery.

    Returns:
        A merged MeshResponse-compatible dict.
    """
    result = consensus_result
    achieved = result.threshold_achieved
    consensus_label = result.assertion.assertion_type.value  # "factual" or "strategic"

    results = [
        {
            "content": result.summary(),
            "similarity": achieved,
            "embed_type": "consensus_decision",
            "success_score": achieved if result.passed else 0.0,
            "metadata": {
                "passed": result.passed,
                "consensus_type": consensus_label,
                "votes_for": result.votes_for,
                "votes_against": result.votes_against,
                "votes_abstain": result.votes_abstain,
                "total_agents": result.total_agents,
                "threshold_required": result.threshold_required,
                "threshold_achieved": achieved,
                "unanimous": result.unanimous,
                "timestamp": result.timestamp,
            },
        }
    ]

    if result.dissent:
        dissent_summary = "; ".join(
            f"{v.agent}: {v.reasoning}" for v in result.dissent if v.reasoning
        ) or "; ".join(v.agent for v in result.dissent)

        results.append(
            {
                "content": f"Dissent: {dissent_summary}",
                "similarity": 0.0,
                "embed_type": "council_dissent",
                "success_score": 0.0,
            }
        )

    return {
        "type": "mesh_response",
        "query_id": task_id,
        "responder": "council",
        "capabilities": ["consensus", "multi-agent-synthesis"],
        "results": results,
        "offered_genes": [],
        "latency_ms": 0,
    }
