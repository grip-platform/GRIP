"""GRIP recall substrate — moat-backed, cross-harness, memory-independent.

Contract: lib/recall/schema.py (the cross-track pin). Sibling modules:
index (subject index, materialised view), mmr (audit commitment),
preassert (negative-existence verdict engine), forensic (RED chain),
cards / dejavu / wizard / provenance_card / crossharness_demo (surfaces).
"""

from lib.recall.schema import (  # noqa: F401
    Artefact,
    ForensicNode,
    Freshness,
    IndexEvent,
    PreassertResult,
    QueryResult,
    VERDICT_ALLOW,
    VERDICT_DENY,
    VERDICT_UNVERIFIABLE,
    canonical_json,
    content_hash,
    forensic_uuid,
)
