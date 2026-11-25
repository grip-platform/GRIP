"""LLM Replay via Cross-Provider HAL — P2 IDR Forest Extension.

P2 phase of GRIP #2799: extend deterministic replay (pure, byte-identical
reconstruction) to AI-bearing IDRs whose original decision came from an LLM.
This module re-executes the LLM call via HAL's multi-provider ladder and
compares the semantic alignment (not byte-identical; LLMs are stochastic).

Composition: extends lib.idr_forest.replay_chain with HAL cross-provider
redecision via lib.hal_llm_adapter.HALLLMAdapter.

Spec: docs/adr/0009-idr-forest-spec.md (P2).
Test: tests/test_idr_forest.py T-I2 extended to T-I2-cross.

Architecture:
- Deterministic replay (P1) proves the recorded chain is tamper-free.
- LLM replay (P2) proves the LLM's semantic output is reproducible across
  independent providers — a strictly weaker guarantee (stochasticity is
  expected, but systematic differences signal provider bias or capability gaps).

Pure + exogenous: the redecision is independent of the original provider; the
original decision record (in the forest) is the exogenous truth (Exogenous-
Anchor Law).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lib.idr_forest import Forest, IdrForestError, verify_chain_integrity
from lib.audit_chain import Verdict


@dataclass(frozen=True)
class LLMReplayResult:
    """Cross-provider LLM re-decision result (T-I2-cross).

    * ``node_id`` — the forest node replayed.
    * ``original_provider`` — the provider that produced the original decision
      (inferred from the IDR's metadata if available, else None).
    * ``redecision_output`` — the HAL response from the cross-provider redecision.
    * ``semantic_alignment`` — 0.0–1.0 score: does the redecision semantically
      agree with the original? (not byte-identical; LLM stochasticity is expected).
    * ``providers_chain`` — tuple of providers that HAL tried (failover ladder).
    * ``verdict`` — integrity of the forest chain (VERIFIED/DEGRADED/BROKEN).
    """

    node_id: str
    original_provider: Optional[str]
    redecision_output: str
    semantic_alignment: float
    providers_chain: tuple[str, ...]
    verdict: Verdict


def replay_with_llm_redecision(
    forest: Forest,
    node_id: str,
    prompt: Optional[str] = None,
) -> LLMReplayResult:
    """Re-execute an AI-bearing IDR via cross-provider HAL.

    Args:
        forest: The IDR forest containing the node.
        node_id: The node to replay.
        prompt: Optional explicit prompt to re-run. If None, extracted from the
            forest node's recorded decision.

    Returns:
        LLMReplayResult with the redecision output, semantic alignment score,
        and integrity verdict over the forest chain.

    Raises:
        IdrForestError: If node_id not in forest, or forest is not VERIFIED
            (BROKEN, or DEGRADED/keyless — replay requires positive signature proof).

    Pure + exogenous: the redecision is independent of the original provider.
    The forest chain (deterministic replay, P1) is the exogenous anchor
    (Exogenous-Anchor Law). A redecision disagreement signals provider bias or
    capability variance, not tampering.
    """
    if node_id not in forest.nodes:
        raise IdrForestError(f"replay_with_llm_redecision: unknown node {node_id!r}")

    verdict = verify_chain_integrity(forest)
    if verdict is not Verdict.VERIFIED:
        raise IdrForestError(
            "replay_with_llm_redecision: forest is not VERIFIED — refusing to replay a "
            "chain that is not positively signature-verified (a DEGRADED/keyless chain "
            "is recomputable by an attacker, so it is refused, not trusted)"
        )

    node = forest.nodes[node_id]
    idr = node.idr

    # Extract prompt from IDR if not provided (T-I2-cross assumption: prompt is
    # stored in the IDR's context field or metadata).
    if prompt is None:
        # The recorded decision is in idr.content["decision"]. For AI-bearing
        # decisions, the original prompt is in idr.context["prompt"].
        context = idr.context or {}
        prompt = context.get("prompt", "")
        if not prompt:
            raise IdrForestError(
                f"replay_with_llm_redecision: node {node_id!r} has no recorded prompt "
                "(LLM replay requires the original prompt)"
            )

    # Redecide via HAL (multi-provider, audit-capable).
    from lib.hal_llm_adapter import HALLLMAdapter

    adapter = HALLLMAdapter()
    # Call infer() to get audit metadata + multi-provider provenance.
    result = adapter.infer(
        prompt,
        audit=True,  # Enable IDR recording on HAL side
    )
    redecision_text = result.get("text", "")
    providers_chain = tuple(result.get("providers_chain", []))

    # Semantic alignment: for now, a naive implementation based on token overlap.
    # (T-I2-cross test can override this with a richer scorer if needed.)
    original_decision = idr.content.get("decision", "")
    alignment = _compute_semantic_alignment(original_decision, redecision_text)

    # Inferred original provider from IDR metadata (if available).
    original_provider = idr.context.get("provider") if idr.context else None

    return LLMReplayResult(
        node_id=node_id,
        original_provider=original_provider,
        redecision_output=redecision_text,
        semantic_alignment=alignment,
        providers_chain=providers_chain,
        verdict=verdict,
    )


def _compute_semantic_alignment(original: str, redecision: str) -> float:
    """Naive semantic alignment scorer (0.0–1.0).

    For T-I2-cross, this uses token-set overlap (Jaccard distance over the
    words). A richer implementation might use embedding distance (cosine
    similarity) or a cross-provider agreement classifier. Falsifier: if the
    token overlap consistently misses real semantic divergence (e.g., negation
    flips), the scorer needs refinement, but the scaffold remains the same.

    T-I2-cross test falsifies this if the naive scorer produces alignment >0.7
    on intentionally contradictory pairs.
    """
    if not original or not redecision:
        return 0.5  # Neutral when either is empty.

    original_tokens = set(original.lower().split())
    redecision_tokens = set(redecision.lower().split())

    if not original_tokens or not redecision_tokens:
        return 0.5

    # Jaccard distance: intersection / union.
    intersection = len(original_tokens & redecision_tokens)
    union = len(original_tokens | redecision_tokens)
    return intersection / union if union > 0 else 0.5
