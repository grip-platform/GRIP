"""Backlog REVIEW and GRADUATE stages.

Review presents processed items with proposals to the user.
Graduate integrates accepted items into the GRIP ecosystem.
"""

import json
import os
from pathlib import Path
from typing import Optional

from lib.backlog_item import (
    BacklogItem, BACKLOG_DIR, save_item, load_item, move_item,
    list_items, STAGE_DIRS, now_iso
)


def get_reviewable_items() -> list[BacklogItem]:
    """Get all items in 'processed' stage ready for review."""
    return list_items("processed")


def accept_item(item: BacklogItem, accepted_indices: Optional[list[int]] = None,
                user_notes: Optional[str] = None) -> BacklogItem:
    """Accept an item's proposals (all or specific ones by index)."""
    item.review_status = "accepted"
    item.reviewed_at = now_iso()
    item.user_notes = user_notes

    if accepted_indices is not None:
        item.accepted_actions = [
            item.actions[i] for i in accepted_indices
            if i < len(item.actions)
        ]
    else:
        item.accepted_actions = list(item.actions)

    move_item(item, STAGE_DIRS["processed"], "reviewed")
    return item


def reject_item(item: BacklogItem, reason: Optional[str] = None) -> BacklogItem:
    """Reject an item."""
    item.review_status = "rejected"
    item.reviewed_at = now_iso()
    item.user_notes = reason
    move_item(item, STAGE_DIRS["processed"], "rejected")
    return item


def graduate_item(item: BacklogItem) -> BacklogItem:
    """Integrate an accepted item into the GRIP ecosystem.

    Routes by category:
    - task -> GitHub issue or focus card
    - reference -> KONO embedding + optional MEMORY.md entry
    - idea -> learning/pending/ for incubation
    - learning -> learning/pending/ skill candidate
    - communication -> draft message
    - project -> epic decomposition
    - archive -> KONO embedding only
    """
    category = item.category or "archive"

    if category == "task":
        item.graduated_to = "github_issue"
        _graduate_task(item)
    elif category == "reference":
        item.graduated_to = "kono_embedding"
        _graduate_reference(item)
    elif category == "idea":
        item.graduated_to = "learning_pending"
        _graduate_idea(item)
    elif category == "learning":
        item.graduated_to = "learning_pending"
        _graduate_learning(item)
    elif category == "communication":
        item.graduated_to = "draft_message"
    elif category == "project":
        item.graduated_to = "epic_decomposition"
    else:
        item.graduated_to = "archive"

    # Move to archive
    move_item(item, STAGE_DIRS["reviewed"], "archive")
    return item


def _graduate_task(item: BacklogItem):
    """Create a GitHub issue from a task item (logged, not executed)."""
    # We don't auto-create issues — just log the intent
    # The actual issue creation happens when the user acts on it
    pass


def _graduate_reference(item: BacklogItem):
    """Embed reference into KONO if embeddings are available."""
    try:
        from embeddings import get_engine
        from embeddings.storage import store_embedding
    except ImportError:
        return

    try:
        engine = get_engine()
        text = f"{item.content_title or ''}\n{item.summary or item.content_body[:500]}"
        embedding = engine.embed_text(text)
        if embedding is not None:
            store_embedding(
                engine,
                text=text,
                embed_type="backlog_reference",
                metadata={
                    "type": "backlog",
                    "category": "reference",
                    "tags": item.tags,
                    "source_id": item.id,
                },
            )
            item.embedding_id = item.id
    except Exception:
        pass


def _graduate_idea(item: BacklogItem):
    """Save idea to learning/pending/ for incubation."""
    pending_dir = Path(os.environ.get(
        "CLAUDE_DIR", os.path.expanduser("~/.claude")
    )) / "learning" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    idea_file = pending_dir / f"backlog-{item.id}.json"
    with open(idea_file, "w") as f:
        json.dump({
            "source": "backlog",
            "id": item.id,
            "title": item.content_title,
            "body": item.content_body[:500],
            "tags": item.tags,
            "created_at": item.created_at,
        }, f, indent=2)


def _graduate_learning(item: BacklogItem):
    """Save learning to learning/pending/ as skill candidate."""
    pending_dir = Path(os.environ.get(
        "CLAUDE_DIR", os.path.expanduser("~/.claude")
    )) / "learning" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    learning_file = pending_dir / f"backlog-learning-{item.id}.json"
    with open(learning_file, "w") as f:
        json.dump({
            "source": "backlog",
            "type": "skill_candidate",
            "id": item.id,
            "title": item.content_title,
            "body": item.content_body[:500],
            "tags": item.tags,
            "created_at": item.created_at,
        }, f, indent=2)


def format_review_display(item: BacklogItem) -> str:
    """Format an item for review display."""
    lines = [
        f"ID: {item.id}",
        f"Title: {item.content_title or 'Untitled'}",
        f"Category: {item.category or 'uncategorised'}",
        f"Confidence: {item.confidence or 0:.0%}",
        f"Summary: {item.summary or item.content_body[:100]}",
        f"Tags: {', '.join(item.tags) if item.tags else 'none'}",
    ]

    if item.actions:
        lines.append(f"\nProposals ({len(item.actions)}):")
        for i, action in enumerate(item.actions):
            a = action if isinstance(action, dict) else {}
            lines.append(
                f"  [{i+1}] {a.get('action', '?')} "
                f"(P:{a.get('priority', '?')}, "
                f"C:{a.get('confidence', 0):.0%})"
            )

    return "\n".join(lines)
