#!/usr/bin/env python3
"""
Embedding Classification - Intent/feedback/skill/command classification.
Part of GRIP v6.0 SRP refactoring.

Responsibilities:
    - Concept classification
    - Intent detection
    - Checkpoint detection
    - Agent/skill/command matching
    - Full classification pipeline
"""

import json
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import EmbeddingEngine


def _get_dynamic_threshold(
    engine: 'EmbeddingEngine',
    concept_type: str,
    default: float = 0.45
) -> float:
    """Get threshold from database or return default."""
    try:
        conn = engine.connect()
        result = list(conn.execute(
            'SELECT current_threshold FROM threshold_config WHERE concept_type = ?',
            (concept_type,)
        ))
        return result[0][0] if result else default
    except Exception:
        return default


def classify_against_concepts(
    engine: 'EmbeddingEngine',
    text: str,
    concept_type: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    Classify text against stored concepts of a given type.
    
    Args:
        engine: EmbeddingEngine instance
        text: Text to classify
        concept_type: Type of concepts to match against
        threshold: Minimum similarity threshold (default: dynamic from DB)
        
    Returns:
        List of matching concepts sorted by similarity
    """
    if threshold is None:
        threshold = _get_dynamic_threshold(engine, concept_type)

    query_vector = engine.embed_text(text)
    conn = engine.connect()

    concepts = list(conn.execute(
        """SELECT id, vector, content, metadata FROM embeddings
           WHERE type = ?""",
        (concept_type,)
    ))

    results = []
    for emb_id, stored_vec, content, metadata_json in concepts:
        similarity = engine._cosine_similarity(query_vector, stored_vec)
        if similarity >= threshold:
            metadata = json.loads(metadata_json) if metadata_json else {}
            results.append({
                "id": emb_id,
                "name": metadata.get("name", "unknown"),
                "category": metadata.get("category", concept_type),
                "content": content,
                "similarity": round(similarity, 4)
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results


def detect_checkpoint(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """Detect if text matches a checkpoint concept."""
    results = classify_against_concepts(engine, text, "checkpoint", threshold)
    if results:
        return results[0]
    return None


def classify_intent(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Classify text intent."""
    return classify_against_concepts(engine, text, "concept_intent", threshold)


def classify_feedback(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Classify text as feedback type."""
    return classify_against_concepts(engine, text, "concept_feedback", threshold)


def classify_solution(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Classify text as solution type."""
    return classify_against_concepts(engine, text, "concept_solution", threshold)


def classify_workflow(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Classify text as workflow type."""
    return classify_against_concepts(engine, text, "concept_workflow", threshold)


def match_agent(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Match text to agent concepts."""
    return classify_against_concepts(engine, text, "concept_agent", threshold)


def match_skill(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Match text to skill concepts."""
    return classify_against_concepts(engine, text, "concept_skill", threshold)


def match_command(
    engine: 'EmbeddingEngine',
    text: str,
    threshold: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Match text to command concepts."""
    return classify_against_concepts(engine, text, "concept_command", threshold)


def should_trigger_checkpoint(
    engine: 'EmbeddingEngine',
    text: str
) -> Tuple[bool, Optional[str]]:
    """Check if text should trigger a checkpoint."""
    checkpoint = detect_checkpoint(engine, text)
    if checkpoint:
        return True, checkpoint["name"]
    return False, None


def get_full_classification(
    engine: 'EmbeddingEngine',
    text: str
) -> Dict[str, Any]:
    """
    Get full classification of text including all concept types.
    
    Returns dict with:
        - intent, feedback, checkpoint, workflow matches
        - suggested agent, skill, command
        - novelty score and priority
        - coherence metadata
    """
    from .storage import calculate_novelty, get_priority
    
    intent = classify_intent(engine, text)
    feedback = classify_feedback(engine, text)
    checkpoint = detect_checkpoint(engine, text)
    workflow = classify_workflow(engine, text)
    agent = match_agent(engine, text)
    skill = match_skill(engine, text)
    command = match_command(engine, text)
    novelty, coherence_meta = calculate_novelty(engine, text)
    priority = get_priority(novelty)

    return {
        "intent": intent[:3] if intent else [],
        "feedback": feedback[:2] if feedback else [],
        "checkpoint": checkpoint,
        "workflow": workflow[:1] if workflow else [],
        "suggested_agent": agent[0] if agent else None,
        "suggested_skill": skill[0] if skill else None,
        "suggested_command": command[0] if command else None,
        "novelty_score": round(novelty, 4),
        "priority": priority,
        "should_process_queue": checkpoint is not None,
        "coherence": coherence_meta
    }
