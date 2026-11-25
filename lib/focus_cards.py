#!/usr/bin/env python3
"""
Focus Cards — Session-to-Card mapping for Claude Code.

Maps active Claude Code sessions to Kanban-style cards with auto-inferred
checklists. Cards persist across sessions via atomic JSON writes.

Architecture:
    FocusCard ←→ Claude Code session (1:1)
    ChecklistItem ←→ Inferred TODO from session activity
    FocusCardsManager ←→ CRUD + persistence layer

State file: .focus-cards-state.json (project-level)

Phase 1: Core data model + hooks (this file)
Phase 2: MCP server + VS Code UI
Phase 3: KONO memory integration

Usage:
    from focus_cards import FocusCardsManager

    mgr = FocusCardsManager()
    card = mgr.create_card(session_id="abc-123", title="Fix auth bug")
    mgr.add_checklist_item(card.id, "Review token expiration logic")
    mgr.pin_card(card.id)
    mgr.save()
"""

import json
import os
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

# Use centralized state manager (GRASP: Pure Fabrication)
from lib.state_manager import get_state_manager
from lib.atomic_write import atomic_write_json

# A Focus Card models a focus *window*, never an unbounded commit log.
# Without this cap a long-lived branch (main) accretes one item per commit
# forever — the 439-item runaway that motivated GRIP #2885. Eviction is
# FIFO, oldest-resolved-first, so in-flight pending work survives longest.
MAX_CHECKLIST_ITEMS = 50


class CardStatus(str, Enum):
    """Card lifecycle states."""
    ACTIVE = "active"          # Session is running
    PAUSED = "paused"          # Session ended, card still relevant
    COMPLETED = "completed"    # All checklist items done
    ARCHIVED = "archived"      # Manually archived by user
    PINNED = "pinned"          # Pinned for cross-session persistence


class ChecklistStatus(str, Enum):
    """Checklist item states."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


@dataclass
class ChecklistItem:
    """A single TODO item inferred from session activity."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    text: str = ""
    status: ChecklistStatus = ChecklistStatus.PENDING
    source: str = ""           # How it was inferred (commit, tool_call, conversation)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None

    def complete(self) -> None:
        self.status = ChecklistStatus.DONE
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def skip(self) -> None:
        self.status = ChecklistStatus.SKIPPED
        self.completed_at = datetime.now(timezone.utc).isoformat()


@dataclass
class FocusCard:
    """A Focus Card representing one Claude Code session's work."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_id: str = ""
    title: str = ""
    description: str = ""
    status: CardStatus = CardStatus.ACTIVE
    branch: str = ""
    project_path: str = ""
    checklist: list[ChecklistItem] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    pinned: bool = False
    metadata: dict = field(default_factory=dict)

    def touch(self) -> None:
        """Update the modified timestamp."""
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_checklist_item(
        self,
        text: str,
        source: str = "manual",
        status: ChecklistStatus = ChecklistStatus.PENDING,
    ) -> ChecklistItem:
        """Add a checklist item.

        ``status`` lets a caller record an item as already resolved. A
        commit is *completed work*, not a pending intent — the
        PostToolUse hook passes ``status=DONE`` so progress reflects
        reality instead of growing a permanently-0% denominator
        (GRIP #2885). The checklist is FIFO-capped at
        ``MAX_CHECKLIST_ITEMS``.
        """
        item = ChecklistItem(text=text, source=source, status=status)
        if status in (ChecklistStatus.DONE, ChecklistStatus.SKIPPED):
            item.completed_at = datetime.now(timezone.utc).isoformat()
        self.checklist.append(item)
        self._enforce_cap()
        self.touch()
        return item

    def _enforce_cap(self) -> None:
        """Bound the checklist to MAX_CHECKLIST_ITEMS (FIFO, resolved-first).

        Single O(n) pass: evict the oldest resolved (DONE/SKIPPED) items
        first so in-flight pending work is preserved as long as possible,
        backfilling with the oldest pending items only if still over cap.
        Chronological order of survivors is unchanged.
        """
        overflow = len(self.checklist) - MAX_CHECKLIST_ITEMS
        if overflow <= 0:
            return
        resolved = (ChecklistStatus.DONE, ChecklistStatus.SKIPPED)
        order = sorted(
            range(len(self.checklist)),
            key=lambda i: (self.checklist[i].status not in resolved, i),
        )
        drop = set(order[:overflow])
        self.checklist = [
            it for i, it in enumerate(self.checklist) if i not in drop
        ]

    def progress(self) -> tuple[int, int]:
        """Return (completed, total) checklist progress."""
        total = len(self.checklist)
        done = sum(1 for i in self.checklist if i.status in (ChecklistStatus.DONE, ChecklistStatus.SKIPPED))
        return done, total

    def is_complete(self) -> bool:
        """All checklist items resolved."""
        if not self.checklist:
            return False
        done, total = self.progress()
        return done == total

    def pin(self) -> None:
        """Pin card for cross-session persistence."""
        self.pinned = True
        self.status = CardStatus.PINNED
        self.touch()

    def archive(self) -> None:
        """Archive the card."""
        self.status = CardStatus.ARCHIVED
        self.touch()

    def complete(self) -> None:
        """Mark card as completed."""
        self.status = CardStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.touch()

    def to_dict(self) -> dict:
        """Serialize to dict with enum string values."""
        d = asdict(self)
        d["status"] = self.status.value
        for item in d["checklist"]:
            item["status"] = item["status"].value if isinstance(item["status"], str) else item["status"]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "FocusCard":
        """Deserialize from dict."""
        checklist_data = data.pop("checklist", [])
        status_str = data.pop("status", "active")
        card = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        card.status = CardStatus(status_str)
        for item_data in checklist_data:
            item_status = item_data.pop("status", "pending")
            item = ChecklistItem(**{k: v for k, v in item_data.items() if k in ChecklistItem.__dataclass_fields__})
            item.status = ChecklistStatus(item_status)
            card.checklist.append(item)
        return card


class KONOBackend:
    """
    KONO memory integration for Focus Cards.

    Stores cards as episodic memories, enables semantic recall on session
    start, and promotes pinned cards to permanent KONO storage.

    Gracefully no-ops when KONO/embeddings pipeline is unavailable.
    """

    MEMORY_TYPE = "focus_card"

    def __init__(self) -> None:
        self._substrate = None

    def _get_substrate(self):
        """Lazy-load substrate; return None if unavailable."""
        if self._substrate is not None:
            return self._substrate
        try:
            import sys as _sys
            own_lib = str(Path(__file__).resolve().parent)  # never the shared tree's (GRIP#9857)
            if own_lib not in _sys.path:
                _sys.path.insert(0, own_lib)
            from kono_substrate import MemorySubstrate
            self._substrate = MemorySubstrate()
            return self._substrate
        except Exception:
            return None

    def _card_to_text(self, card: "FocusCard") -> str:
        """Serialize card to a searchable text block for embedding."""
        done, total = card.progress()
        items = "\n".join(f"  - [{i.status.value}] {i.text}" for i in card.checklist)
        return (
            f"Focus Card: {card.title}\n"
            f"Branch: {card.branch or 'none'} | Project: {card.project_path or 'none'}\n"
            f"Tags: {', '.join(card.tags) if card.tags else 'none'}\n"
            f"Progress: {done}/{total}\n"
            f"Description: {card.description}\n"
            f"Checklist:\n{items}"
        ).strip()

    def store_card(self, card: "FocusCard") -> Optional[int]:
        """Store a card as an episodic KONO memory. Returns embedding ID or None."""
        substrate = self._get_substrate()
        if substrate is None:
            return None
        try:
            content = self._card_to_text(card)
            metadata_prefix = f"[focus_card:{card.id}] "
            return substrate.store_session_memory(
                content=metadata_prefix + content,
                memory_type=self.MEMORY_TYPE,
                project_path=card.project_path or None,
            )
        except Exception:
            return None

    def pin_card_permanent(self, card: "FocusCard") -> Optional[int]:
        """
        Store a pinned card as a permanent KONO memory.

        Stores the card, then immediately consolidates to push it toward
        permanence. The card gets 3 attestations (permanence threshold).
        """
        substrate = self._get_substrate()
        if substrate is None:
            return None
        try:
            content = self._card_to_text(card)
            metadata_prefix = f"[focus_card:{card.id}:pinned] "
            emb_id = substrate.store_session_memory(
                content=metadata_prefix + content,
                memory_type=self.MEMORY_TYPE,
                project_path=card.project_path or None,
            )
            if emb_id is not None:
                # Attest twice more to hit the 3-attestation permanence threshold
                for _ in range(2):
                    substrate.attest(emb_id)
                substrate.consolidate()
            return emb_id
        except Exception:
            return None

    def recall_cards(
        self,
        query: str,
        project_path: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """
        Semantic recall of past Focus Cards from KONO.

        Called on session start to surface relevant work from prior sessions.
        Returns list of memory dicts with 'content', 'is_permanent', 'fitness'.
        """
        substrate = self._get_substrate()
        if substrate is None:
            return []
        try:
            results = substrate.recall(
                query=query,
                limit=limit * 2,
                min_fitness=0.0,
                project_path=project_path,
                dimension=7,  # Focus cards are dimension 7 (episodic)
            )
            # Filter to focus_card memories only (belt-and-suspenders)
            return [
                r for r in results
                if "[focus_card:" in r.get("content", "")
            ][:limit]
        except Exception:
            return []

    def recall_recent_cards(
        self,
        project_path: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """
        Recency-based recall of focus cards from KONO (no vector search).

        For session resume, recency matters more than semantic similarity.
        Queries the DB directly for type='focus_card' ordered by created_at.
        Also attests recalled entries to increment access_count.
        """
        substrate = self._get_substrate()
        if substrate is None:
            return []
        try:
            conn = substrate.connect()
            sql = """
                SELECT id, content, fitness_score, is_permanent, created_at,
                       access_count
                FROM embeddings
                WHERE type = 'focus_card'
            """
            params: list = []
            if project_path:
                sql += " AND project_path = ?"
                params.append(project_path)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = list(conn.execute(sql, params))
            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "fitness": row[2],
                    "is_permanent": bool(row[3]),
                    "created_at": row[4],
                    "access_count": row[5],
                })
                # Attest to bump access_count via trigger
                substrate.attest_recalled(row[0])
            return results
        except Exception:
            return []

    def resume_context(self, project_path: Optional[str] = None) -> str:
        """
        Build a context injection string from recent focus cards in KONO.

        Uses recency-based recall (not vector search) to surface the most
        recent session work. Returns empty string if nothing found.
        """
        memories = self.recall_recent_cards(
            project_path=project_path,
            limit=5,
        )
        if not memories:
            return ""
        lines = ["[FOCUS CARDS CONTEXT]"]
        for mem in memories:
            content = mem.get("content", "")
            # Strip the metadata prefix if present
            if "[focus_card:" in content:
                content = content.split("]", 1)[-1].strip()
            is_perm = mem.get("is_permanent", False)
            tag = " (pinned)" if is_perm else ""
            lines.append(f"  {content[:200]}{tag}")
        return "\n".join(lines)


class FocusCardsManager:
    """
    Manages Focus Cards with persistent state.

    State file location: {project_dir}/.focus-cards-state.json
    Falls back to ~/.claude/.focus-cards-state.json if no project context.

    ACCEPTED DEBT (#2885 P2, SOLID-ISP — Rule 19 documented deferral):
    this facade exposes 16 public methods (threshold 7). Every one has a
    code- or test-caller (verified vs origin/main); none is dead. A true
    ISP reduction requires a facade/collaborator split that rewrites the
    ``mgr.X()`` call surface across 300+ tests + the hook + CLI — a
    dedicated refactor with its own validation runway, NOT an
    evening-close rider on the just-shipped #2885 fix (Rule 19's own
    falsification clause: a rushed cross-surface refactor here is
    "more harm than good"). Tracked as a focused follow-up issue; do not
    silently expand this surface further. The cheap, safe wins (the
    _checklist_preview extraction and the _board_* de-nesting) are done.
    """

    def __init__(self, state_path: Optional[Path] = None):
        if state_path:
            self.state_path = Path(state_path)
        else:
            self.state_path = get_state_manager().path("focus-cards-state")
        self.cards: dict[str, FocusCard] = {}
        self.kono = KONOBackend()
        self._load()

    def _load(self) -> None:
        """Load state from disk."""
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path, "r") as f:
                data = json.load(f)
            for card_data in data.get("cards", []):
                card = FocusCard.from_dict(card_data)
                self.cards[card.id] = card
        except (json.JSONDecodeError, KeyError, TypeError):
            # Corrupted state file — start fresh
            self.cards = {}

    def save(self) -> None:
        """Persist state to disk atomically."""
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cards": [card.to_dict() for card in self.cards.values()],
        }
        atomic_write_json(self.state_path, data)

    def create_card(
        self,
        session_id: str,
        title: str,
        branch: str = "",
        project_path: str = "",
        description: str = "",
        tags: Optional[list[str]] = None,
    ) -> FocusCard:
        """Create a new Focus Card for a session."""
        card = FocusCard(
            session_id=session_id,
            title=title,
            branch=branch,
            project_path=project_path,
            description=description,
            tags=tags or [],
        )
        self.cards[card.id] = card
        self.save()
        self.kono.store_card(card)
        return card

    def get_card(self, card_id: str) -> Optional[FocusCard]:
        """Get card by ID."""
        return self.cards.get(card_id)

    def get_card_by_session(self, session_id: str) -> Optional[FocusCard]:
        """Find the resumable card for a session (active, pinned, or paused)."""
        for card in self.cards.values():
            if card.session_id == session_id and card.status in (
                CardStatus.ACTIVE, CardStatus.PINNED, CardStatus.PAUSED,
            ):
                return card
        return None

    def add_checklist_item(self, card_id: str, text: str, source: str = "manual") -> Optional[ChecklistItem]:
        """Add a checklist item to a card."""
        card = self.get_card(card_id)
        if not card:
            return None
        item = card.add_checklist_item(text, source)
        self.save()
        return item

    def complete_checklist_item(self, card_id: str, item_id: str) -> bool:
        """Mark a checklist item as done."""
        card = self.get_card(card_id)
        if not card:
            return False
        for item in card.checklist:
            if item.id == item_id:
                item.complete()
                if card.is_complete():
                    card.complete()
                self.save()
                return True
        return False

    def pin_card(self, card_id: str) -> bool:
        """Pin a card for cross-session persistence. Stores permanently in KONO."""
        card = self.get_card(card_id)
        if not card:
            return False
        card.pin()
        self.save()
        self.kono.pin_card_permanent(card)
        return True

    def recall_from_kono(
        self,
        query: str,
        project_path: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Semantic recall of past cards from KONO memory substrate."""
        return self.kono.recall_cards(query=query, project_path=project_path, limit=limit)

    def resume_context(self, project_path: Optional[str] = None) -> str:
        """Context injection string from pinned KONO cards for session resume."""
        return self.kono.resume_context(project_path=project_path)

    def archive_card(self, card_id: str) -> bool:
        """Archive a card."""
        card = self.get_card(card_id)
        if not card:
            return False
        card.archive()
        self.save()
        return True

    def pause_card(self, card_id: str) -> bool:
        """Pause an active card (session ended)."""
        card = self.get_card(card_id)
        if not card:
            return False
        if card.status == CardStatus.ACTIVE:
            card.status = CardStatus.PAUSED
            card.touch()
            self.save()
        return True

    def list_cards(
        self,
        status: Optional[CardStatus] = None,
        include_archived: bool = False,
    ) -> list[FocusCard]:
        """List cards with optional status filter."""
        cards = list(self.cards.values())
        if status:
            cards = [c for c in cards if c.status == status]
        elif not include_archived:
            cards = [c for c in cards if c.status != CardStatus.ARCHIVED]
        return sorted(cards, key=lambda c: c.updated_at, reverse=True)

    def active_cards(self) -> list[FocusCard]:
        """Cards for currently running sessions."""
        return self.list_cards(status=CardStatus.ACTIVE)

    def pinned_cards(self) -> list[FocusCard]:
        """Pinned cards that persist across sessions."""
        return self.list_cards(status=CardStatus.PINNED)

    def _get_live_sessions(self) -> dict:
        """Get live session data for liveness enrichment."""
        try:
            from session_lister import scan_all_sessions
            return {s["session_id"]: s for s in scan_all_sessions()}
        except Exception:
            return {}

    def export_board(self) -> dict:
        """Export all cards as a Kanban board structure with rich metadata."""
        live = self._get_live_sessions()
        return {
            "columns": [
                {
                    "id": "active",
                    "heading": "In Progress",
                    "color": "#22c55e",
                    "cards": [self._card_detail(c, live) for c in self.list_cards(status=CardStatus.ACTIVE)],
                },
                {
                    "id": "pinned",
                    "heading": "Pinned",
                    "color": "#eab308",
                    "cards": [self._card_detail(c, live) for c in self.list_cards(status=CardStatus.PINNED)],
                },
                {
                    "id": "paused",
                    "heading": "Paused",
                    "color": "#94a3b8",
                    "cards": [self._card_detail(c, live) for c in self.list_cards(status=CardStatus.PAUSED)],
                },
                {
                    "id": "completed",
                    "heading": "Done",
                    "color": "#3b82f6",
                    "cards": [self._card_detail(c, live) for c in self.list_cards(status=CardStatus.COMPLETED)],
                },
            ],
        }

    @staticmethod
    def _checklist_preview(card: FocusCard) -> str:
        """First-5 checklist lines for the board view; else the card
        description. Extracted from _card_detail (#2885 P2, KISS)."""
        lines = []
        for item in card.checklist[:5]:
            mark = "x" if item.status in (
                ChecklistStatus.DONE, ChecklistStatus.SKIPPED) else " "
            lines.append(f"[{mark}] {item.text}")
        if len(card.checklist) > 5:
            lines.append(f"    ...and {len(card.checklist) - 5} more")
        if lines:
            return "\n".join(lines)
        return card.description[:120] if card.description else "No items yet"

    def _card_detail(self, card: FocusCard, live_sessions: Optional[dict] = None) -> dict:
        """Rich card representation with checklist, progress, and metadata."""
        done, total = card.progress()
        pct = round((done / total) * 100) if total > 0 else 0
        description = self._checklist_preview(card)

        detail = {
            "id": card.id,
            "title": card.title,
            "description": description,
            "tags": card.tags,
            "priority": "high" if card.pinned else ("medium" if card.status == CardStatus.ACTIVE else "low"),
            "progress": f"{done}/{total}" if total > 0 else None,
            "progressPct": pct,
        }

        # Add branch/project context if available
        if card.branch:
            detail["branch"] = card.branch
        if card.project_path:
            # Show just the last path component for readability
            detail["project"] = Path(card.project_path).name or card.project_path

        # Timing metadata
        detail["created"] = card.created_at[:10] if card.created_at else None
        if card.completed_at:
            detail["completed"] = card.completed_at[:10]

        # Session liveness enrichment
        if live_sessions and card.session_id in live_sessions:
            sess = live_sessions[card.session_id]
            detail["session_alive"] = True
            detail["uptime"] = sess.get("uptime_display", "")
            detail["pid"] = sess.get("pid")
        elif card.status == CardStatus.ACTIVE:
            detail["session_alive"] = False

        return detail

    def _card_summary(self, card: FocusCard) -> dict:
        """Compact card representation (legacy, used by stats)."""
        done, total = card.progress()
        return {
            "id": card.id,
            "title": card.title,
            "description": f"{done}/{total} items" if total > 0 else card.description[:80],
            "tags": card.tags,
            "priority": "high" if card.pinned else "medium",
        }

    def stats(self) -> dict:
        """Summary statistics."""
        all_cards = list(self.cards.values())
        return {
            "total": len(all_cards),
            "active": sum(1 for c in all_cards if c.status == CardStatus.ACTIVE),
            "pinned": sum(1 for c in all_cards if c.status == CardStatus.PINNED),
            "paused": sum(1 for c in all_cards if c.status == CardStatus.PAUSED),
            "completed": sum(1 for c in all_cards if c.status == CardStatus.COMPLETED),
            "archived": sum(1 for c in all_cards if c.status == CardStatus.ARCHIVED),
        }


# ---------------------------------------------------------------------------
# Corpus repair (GRIP #2885) — one-shot, idempotent, dry-run by default
# ---------------------------------------------------------------------------

_REPAIR_APPLY = {
    "archive_runaway": lambda c: c.archive(),
    "archive_empty": lambda c: c.archive(),
    "truncate_capped": lambda c: c._enforce_cap(),
    "complete_alldone": lambda c: c.complete(),
}


def _classify_repair(card: "FocusCard") -> list:
    """Repair actions a card needs (0-2), pre-mutation. Pure.

    archive_* are terminal. truncate_capped + complete_alldone may
    co-occur: ``_enforce_cap`` evicts resolved-first, so the
    "all items resolved" property is invariant under truncation —
    classification is stable before mutation, giving single-pass settle.
    """
    if card.status == CardStatus.ARCHIVED:
        return []
    n = len(card.checklist)
    default_branch = (card.branch or "") in {
        "main", "master", "develop", "HEAD", "",
    }
    if default_branch and n > MAX_CHECKLIST_ITEMS:
        return ["archive_runaway"]
    if n == 0 and card.title.startswith("Session:"):
        return ["archive_empty"]
    actions = []
    if n > MAX_CHECKLIST_ITEMS:
        actions.append("truncate_capped")
    done, total = card.progress()
    if total and done == total and card.status in (
        CardStatus.ACTIVE, CardStatus.PAUSED,
    ):
        actions.append("complete_alldone")
    return actions


def _repair_one(card: "FocusCard", report: dict, apply: bool) -> None:
    """Record (and optionally apply) one card's repair actions."""
    for action in _classify_repair(card):
        report[action].append(card.id)
        if apply:
            _REPAIR_APPLY[action](card)


def repair_corpus(mgr: "FocusCardsManager", apply: bool = False) -> dict:
    """Repair a Focus Cards corpus left broken by the append-only era.

    Dry-run by default; mutates + saves only when ``apply=True``. Returns
    ``{action: [card_id, ...]}``. Idempotent by construction: every
    action's precondition is false after the first apply, so a second
    apply reports nothing and changes nothing.

    Actions:
      archive_runaway   default-branch card with > cap items (the 439
                        landfill — a long-lived branch is not a focus)
      archive_empty     0-item Session: placeholder card (dead noise)
      truncate_capped   feature card with > cap items -> FIFO-trimmed
      complete_alldone  non-empty all-resolved ACTIVE/PAUSED -> completed
    """
    report = {k: [] for k in _REPAIR_APPLY}
    for card in list(mgr.cards.values()):
        _repair_one(card, report, apply)
    if apply:
        mgr.save()
    return report


# ---------------------------------------------------------------------------
# CLI formatters
# ---------------------------------------------------------------------------

def _progress_bar(done: int, total: int, width: int = 20) -> str:
    """Render an ASCII progress bar."""
    if total == 0:
        return " " * width
    filled = round((done / total) * width)
    return "#" * filled + "." * (width - filled)


def _format_card_line(card: "FocusCard", show_branch: bool = True) -> str:
    """Format a single card as a rich terminal line."""
    done, total = card.progress()
    pct = round((done / total) * 100) if total > 0 else 0

    status_icons = {
        CardStatus.ACTIVE: ">",
        CardStatus.PINNED: "*",
        CardStatus.PAUSED: "~",
        CardStatus.COMPLETED: "v",
        CardStatus.ARCHIVED: "-",
    }
    icon = status_icons.get(card.status, " ")
    bar = _progress_bar(done, total, 12) if total > 0 else ""

    line = f"  {icon} {card.id}  {card.title}"
    if total > 0:
        line += f"  [{bar}] {pct}%"
    if show_branch and card.branch:
        line += f"  ({card.branch})"
    if card.tags:
        line += f"  #{' #'.join(card.tags)}"
    return line


def _board_card_block(card: "FocusCard") -> list:
    """One card's board lines: header + first 3 checklist items.

    Extracted from _format_board_ascii so no single function carries a
    triple-nested loop (#2885 P2, BIG-O). Output byte-identical."""
    out = [_format_card_line(card)]
    for item in card.checklist[:3]:
        mark = "x" if item.status in (
            ChecklistStatus.DONE, ChecklistStatus.SKIPPED) else " "
        out.append(f"      [{mark}] {item.text}")
    if len(card.checklist) > 3:
        out.append(f"      ... +{len(card.checklist) - 3} more")
    return out


def _board_column(mgr: "FocusCardsManager", heading: str, status, icon: str,
                   width: int) -> list:
    """One Kanban column's lines (empty list if the column has no cards)."""
    cards = mgr.list_cards(status=status)
    if not cards:
        return []
    out = ["", f"  {icon} {heading} ({len(cards)})", "  " + "-" * (width - 4)]
    for card in cards:
        out.extend(_board_card_block(card))
    return out


def _format_board_ascii(mgr: "FocusCardsManager") -> str:
    """Render the full board as an ASCII Kanban (coordinator: one loop)."""
    W = 60
    columns = [
        ("In Progress", CardStatus.ACTIVE, ">"),
        ("Pinned", CardStatus.PINNED, "*"),
        ("Paused", CardStatus.PAUSED, "~"),
        ("Done", CardStatus.COMPLETED, "v"),
    ]
    stats = mgr.stats()
    lines = [
        "=" * W,
        "  FOCUS CARDS",
        f"  {stats['active']} active | {stats['pinned']} pinned | "
        f"{stats['completed']} done | {stats['total']} total",
        "=" * W,
    ]
    for heading, status, icon in columns:
        lines.extend(_board_column(mgr, heading, status, icon, W))
    lines.append("")
    lines.append("=" * W)
    return "\n".join(lines)


def _format_card_detail(card: "FocusCard") -> str:
    """Format a single card with full detail."""
    done, total = card.progress()
    pct = round((done / total) * 100) if total > 0 else 0
    W = 50

    lines = []
    lines.append("-" * W)
    lines.append(f"  {card.title}")
    lines.append(f"  ID: {card.id} | Status: {card.status.value}")
    if card.branch:
        lines.append(f"  Branch: {card.branch}")
    if card.project_path:
        lines.append(f"  Project: {card.project_path}")
    if card.description:
        lines.append(f"  {card.description}")
    lines.append("-" * W)

    if card.checklist:
        lines.append(f"  Checklist [{_progress_bar(done, total)}] {pct}%")
        for item in card.checklist:
            mark = "x" if item.status in (ChecklistStatus.DONE, ChecklistStatus.SKIPPED) else " "
            source_tag = f" ({item.source})" if item.source and item.source != "manual" else ""
            lines.append(f"    [{mark}] {item.text}{source_tag}")
    else:
        lines.append("  No checklist items")

    lines.append("")
    lines.append(f"  Created: {card.created_at[:19] if card.created_at else 'unknown'}")
    if card.completed_at:
        lines.append(f"  Completed: {card.completed_at[:19]}")
    if card.tags:
        lines.append(f"  Tags: {', '.join(card.tags)}")
    lines.append("-" * W)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Focus Cards -- session-to-card tracking for Claude Code",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python3 lib/focus_cards.py board           # ASCII Kanban board\n"
               "  python3 lib/focus_cards.py list             # List all active cards\n"
               "  python3 lib/focus_cards.py show CARD_ID     # Card detail view\n"
               "  python3 lib/focus_cards.py create --title 'Fix auth bug'\n"
               "  python3 lib/focus_cards.py add CARD_ID 'Review token logic'\n",
    )
    sub = parser.add_subparsers(dest="command")

    list_p = sub.add_parser("list", help="List active cards")
    list_p.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    sub.add_parser("board", help="ASCII Kanban board view")
    sub.add_parser("board-json", help="Export board as JSON (for MCP apps)")
    sub.add_parser("stats", help="Show statistics")

    show_p = sub.add_parser("show", help="Show card detail")
    show_p.add_argument("card_id")

    recall_p = sub.add_parser("recall", help="Semantic recall of past cards from KONO")
    recall_p.add_argument("query", nargs="?", default="active work in progress", help="Search query")
    recall_p.add_argument("--project", default="", help="Project path filter")
    recall_p.add_argument("--limit", type=int, default=5)

    resume_p = sub.add_parser("resume", help="Show KONO context for session resume")
    resume_p.add_argument("--project", default="", help="Project path filter")

    create_p = sub.add_parser("create", help="Create a card")
    create_p.add_argument("--session-id", default="", help="Session ID (auto-detected if empty)")
    create_p.add_argument("--title", required=True)
    create_p.add_argument("--branch", default="")
    create_p.add_argument("--project", default="")
    create_p.add_argument("--tags", default="", help="Comma-separated tags")
    create_p.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    create_p.add_argument("--checklist", default="", help="Comma-separated checklist items")

    add_p = sub.add_parser("add", help="Add a checklist item to a card")
    add_p.add_argument("card_id")
    add_p.add_argument("text")
    add_p.add_argument("--source", default="manual")

    done_p = sub.add_parser("done", help="Mark a checklist item as done")
    done_p.add_argument("card_id")
    done_p.add_argument("item_id")

    update_item_p = sub.add_parser("update-item", help="Set checklist item status")
    update_item_p.add_argument("card_id")
    update_item_p.add_argument("item_id")
    update_item_p.add_argument("status", choices=["pending", "in_progress", "done", "skipped"])

    pin_p = sub.add_parser("pin", help="Pin a card")
    pin_p.add_argument("card_id")

    archive_p = sub.add_parser("archive", help="Archive a card")
    archive_p.add_argument("card_id")

    complete_p = sub.add_parser("complete", help="Mark a card as completed")
    complete_p.add_argument("card_id")

    repair_p = sub.add_parser(
        "repair",
        help="One-shot idempotent corpus repair (GRIP #2885); dry-run by default",
    )
    repair_p.add_argument(
        "--apply", action="store_true",
        help="Mutate + save state (default: dry-run report only)",
    )

    args = parser.parse_args()
    mgr = FocusCardsManager()

    if args.command == "list":
        cards = mgr.list_cards()
        if getattr(args, "as_json", False):
            print(json.dumps([card.to_dict() for card in cards], indent=2))
        elif not cards:
            print("No active cards.")
        else:
            for card in cards:
                print(_format_card_line(card))

    elif args.command == "board":
        print(_format_board_ascii(mgr))

    elif args.command == "board-json":
        print(json.dumps(mgr.export_board(), indent=2))

    elif args.command == "stats":
        s = mgr.stats()
        print(f"Total: {s['total']} | Active: {s['active']} | Pinned: {s['pinned']} | "
              f"Paused: {s['paused']} | Done: {s['completed']} | Archived: {s['archived']}")

    elif args.command == "show":
        card = mgr.get_card(args.card_id)
        if card:
            print(_format_card_detail(card))
        else:
            print(f"Card {args.card_id} not found")
            sys.exit(1)

    elif args.command == "create":
        session_id = args.session_id or os.environ.get("CLAUDE_SESSION_ID", str(uuid.uuid4())[:12])
        branch = args.branch
        if not branch:
            # Auto-detect current git branch
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True, text=True, timeout=5,
                )
                branch = result.stdout.strip() if result.returncode == 0 else ""
            except Exception:
                branch = ""

        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        card = mgr.create_card(
            session_id=session_id,
            title=args.title,
            branch=branch,
            project_path=args.project or os.getcwd(),
            tags=tags,
        )
        # Add checklist items if provided
        checklist_items = [i.strip() for i in args.checklist.split(",") if i.strip()] if args.checklist else []
        for item_text in checklist_items:
            card.add_checklist_item(item_text, source="mcp")
        if checklist_items:
            mgr.save()

        if getattr(args, "as_json", False):
            print(json.dumps(card.to_dict(), indent=2))
        else:
            print(f"Created card {card.id}: {card.title}")
            if branch:
                print(f"  Branch: {branch}")

    elif args.command == "add":
        item = mgr.add_checklist_item(args.card_id, args.text, source=args.source)
        if item:
            print(f"Added [{item.id}] {item.text}")
        else:
            print(f"Card {args.card_id} not found")
            sys.exit(1)

    elif args.command == "done":
        if mgr.complete_checklist_item(args.card_id, args.item_id):
            card = mgr.get_card(args.card_id)
            done, total = card.progress() if card else (0, 0)
            print(f"Done! [{_progress_bar(done, total)}] {done}/{total}")
        else:
            print(f"Card or item not found")
            sys.exit(1)

    elif args.command == "update-item":
        card = mgr.get_card(args.card_id)
        if not card:
            print(f"Card {args.card_id} not found")
            sys.exit(1)
        found = False
        for item in card.checklist:
            if item.id == args.item_id:
                new_status = ChecklistStatus(args.status)
                item.status = new_status
                if new_status in (ChecklistStatus.DONE, ChecklistStatus.SKIPPED):
                    item.completed_at = datetime.now(timezone.utc).isoformat()
                else:
                    item.completed_at = None
                card.touch()
                if card.is_complete():
                    card.complete()
                mgr.save()
                done, total = card.progress()
                print(json.dumps({
                    "card_id": card.id,
                    "item_id": item.id,
                    "status": item.status.value,
                    "progress": f"{done}/{total}",
                }))
                found = True
                break
        if not found:
            print(f"Item {args.item_id} not found in card {args.card_id}")
            sys.exit(1)

    elif args.command == "pin":
        if mgr.pin_card(args.card_id):
            print(f"Pinned card {args.card_id}")
        else:
            print(f"Card {args.card_id} not found")
            sys.exit(1)

    elif args.command == "archive":
        if mgr.archive_card(args.card_id):
            print(f"Archived card {args.card_id}")
        else:
            print(f"Card {args.card_id} not found")
            sys.exit(1)

    elif args.command == "complete":
        card = mgr.get_card(args.card_id)
        if card:
            card.complete()
            mgr.save()
            print(f"Completed card {args.card_id}: {card.title}")
        else:
            print(f"Card {args.card_id} not found")
            sys.exit(1)

    elif args.command == "recall":
        project = args.project or None
        results = mgr.recall_from_kono(args.query, project_path=project, limit=args.limit)
        if not results:
            print("No past cards found in KONO memory.")
        else:
            for r in results:
                content = r.get("content", "")
                fitness = r.get("fitness", 0)
                is_perm = r.get("is_permanent", False)
                perm_tag = " [permanent]" if is_perm else ""
                print(f"[fitness:{fitness:.2f}{perm_tag}] {content[:200]}")

    elif args.command == "resume":
        project = args.project or None
        ctx = mgr.resume_context(project_path=project)
        if ctx:
            print(ctx)
        else:
            print("No KONO context found for resume.")

    elif args.command == "repair":
        report = repair_corpus(mgr, apply=args.apply)
        total = sum(len(v) for v in report.values())
        mode = "APPLIED" if args.apply else "DRY-RUN"
        print(f"[focus repair {mode}] {total} card(s) affected")
        for action, ids in report.items():
            if ids:
                shown = ", ".join(i[:8] for i in ids[:6])
                more = f" +{len(ids) - 6} more" if len(ids) > 6 else ""
                print(f"  {action}: {len(ids)} -> {shown}{more}")
        if not args.apply and total:
            print("Re-run with --apply to mutate.")

    else:
        parser.print_help()
