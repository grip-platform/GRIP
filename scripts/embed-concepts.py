#!/usr/bin/env python3
"""
Pre-embed Concept Library
One-time script to embed all semantic anchors from concept-library.json

This creates the static concept embeddings used for:
- Intent classification
- Solution classification
- Feedback detection
- Checkpoint detection
- Agent/skill/command matching

Run: python3 ~/.claude/scripts/embed-concepts.py

When no embedding server answers, it prints one line and exits with
NO_SERVER_EXIT, which scripts/bootstrap-semantic-rl.sh reads as "skipped",
not "failed": semantic memory is optional, and a new install should not show
a stranger a traceback for it.
"""

import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from embeddings import get_engine  # noqa: E402  W5(a): dispatch via get_engine()
from embeddings.ollama_backend import EmbeddingServerUnreachable  # noqa: E402

CLAUDE_DIR = get_claude_dir()
CONCEPT_LIBRARY_PATH = CLAUDE_DIR / "config" / "concept-library.json"
NO_SERVER_EXIT = 3


CATEGORIES = [
    ("intent_concepts", "concept_intent"),
    ("solution_concepts", "concept_solution"),
    ("feedback_concepts", "concept_feedback"),
    ("workflow_concepts", "concept_workflow"),
    ("checkpoint_anchors", "checkpoint"),
    ("agents", "concept_agent"),
    ("skills", "concept_skill"),
    ("commands", "concept_command"),
]


def _embed_category(engine, library, category_key, embed_type):
    """Embed one category. Returns count or None if category missing."""
    if category_key not in library:
        return None
    concepts = library[category_key].get("concepts", {})
    for name, text in concepts.items():
        engine.store_embedding(
            text=text,
            embed_type=embed_type,
            metadata={"category": category_key, "name": name},
            project_path="__system__",
            success_score=1.0,
            novelty_score=0.0,
            priority="system",
        )
    return len(concepts)


def _print_stats(engine):
    stats = engine.get_stats()
    print("\n=== Database Stats ===")
    print(f"  Total embeddings: {stats['total_embeddings']}")
    print(f"  By type: {json.dumps(stats['by_type'], indent=4)}")


def _embed_all(library):
    engine = get_engine()
    engine.init_schema()
    total = 0
    for category_key, embed_type in CATEGORIES:
        count = _embed_category(engine, library, category_key, embed_type)
        if count is None:
            print(f"  Skipping {category_key} (not found)")
        else:
            print(f"  Embedded {count} {category_key}")
            total += count
    print(f"\n  Total concepts embedded: {total}")
    _print_stats(engine)
    engine.close()
    print("\n=== Concept Library Pre-embedding Complete ===")
    return 0


def main():
    print("=== Pre-embedding Concept Library ===\n")
    if not CONCEPT_LIBRARY_PATH.exists():
        print(f"ERROR: Concept library not found at {CONCEPT_LIBRARY_PATH}")
        return 1
    library = json.load(open(CONCEPT_LIBRARY_PATH))
    try:
        return _embed_all(library)
    except EmbeddingServerUnreachable as err:
        print(f"Skipped: {err}. Semantic memory stays off until an embedding server runs; "
              "then run scripts/bootstrap-semantic-rl.sh again.")
        return NO_SERVER_EXIT


if __name__ == "__main__":
    sys.exit(main())
