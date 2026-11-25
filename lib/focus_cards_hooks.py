#!/usr/bin/env python3
"""
Focus Cards — Hook Integration Layer.

.. deprecated::
    LEGACY / UNWIRED (Phase 1+2 class-based handler). The supported
    SessionStart/PostToolUse/SessionEnd entry point is
    ``hooks/focus-cards-hook.py`` (function-based
    ``on_start``/``on_post``/``on_end``) — NOT this module. Neither is wired
    by default: ``config/hooks.registry.json`` marks
    ``policy.focus-cards-hook-py`` intentionally inactive. This file is
    retained only for its test suite (``tests/test_focus_cards_hooks.py``).
    It was NOT touched by the GRIP #2885 no-completion-path fix, so its
    inference path is the original append-only-PENDING shape. **Do NOT
    wire this handler in settings.json without first porting the #2885
    fix (born-DONE commit items + FIFO cap + Jaccard completion-match)** —
    doing so silently reintroduces the 0/N-forever runaway.
    ``tests/test_focus_cards_p2.py`` is a regression anchor that fails if
    settings.json ever points the focus hooks at this module.
    (#2885 P2: recon falsified the "dead duplicate" framing — this is a
    test-covered unwired twin, a distinct concern from dead code.)

Wires Focus Cards into the GRIP session lifecycle:
  - Session start: Load or create a card for the current session
  - PostToolUse: Infer checklist items from tool output (commits, edits)
  - Session end: Pause card, persist final state

This module provides the glue between focus_cards.py (data model),
focus_inference.py (heuristic engine), and the GRIP hook system.

Architecture:
    SessionStart (bash) → on_session_start() → create/resume card
    PostToolUse (python) → on_post_tool_use() → infer checklist items
    SessionEnd (bash)   → on_session_end() → pause card, save state

Usage:
    from focus_cards_hooks import FocusCardsHookHandler

    handler = FocusCardsHookHandler()
    handler.on_session_start(session_id, branch, project_path, first_message)
    handler.on_post_tool_use(tool_name, tool_input, tool_response)
    handler.on_session_end(session_id)
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

if str(Path(__file__).resolve().parent) not in sys.path:  # own lib (GRIP#9857)
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from focus_cards import FocusCardsManager, CardStatus  # noqa: E402
from focus_inference import FocusInference, InferredItem  # noqa: E402

logger = logging.getLogger("focus-cards-hooks")

# Commit hash pattern in Bash tool output
COMMIT_PATTERN = re.compile(
    r"\[[\w/.-]+\s+([0-9a-f]{7,12})\]\s+(.+)",
)

# Plan file pattern in Read/Write tool paths
PLAN_FILE_PATTERN = re.compile(
    r"\.claude/plans/.*\.md$|plans?/.*\.md$",
)


class FocusCardsHookHandler:
    """
    Stateful handler wired into GRIP hooks for Focus Cards lifecycle.

    One instance per session. Created at session-start, reused in PostToolUse.
    """

    def __init__(self, state_path: Optional[Path] = None):
        self.manager = FocusCardsManager(state_path=state_path)
        self.inference = FocusInference()
        self._active_card_id: Optional[str] = None

    @property
    def active_card(self):
        """Currently active card for this session."""
        if self._active_card_id:
            return self.manager.get_card(self._active_card_id)
        return None

    def on_session_start(
        self,
        session_id: str,
        branch: str = "",
        project_path: str = "",
        first_message: str = "",
    ) -> dict:
        """
        Called at session start. Creates or resumes a Focus Card.

        Returns dict with card info for hook output injection.
        """
        # Check for existing active/pinned card for this session
        existing = self.manager.get_card_by_session(session_id)
        if existing:
            # Resume existing card
            if existing.status == CardStatus.PAUSED:
                existing.status = CardStatus.ACTIVE
                existing.touch()
                self.manager.save()
            self._active_card_id = existing.id
            done, total = existing.progress()
            return {
                "card_id": existing.id,
                "title": existing.title,
                "resumed": True,
                "progress": f"{done}/{total}",
            }

        # Infer title from available context
        context = {
            "branch": branch,
            "first_message": first_message,
            "project_name": Path(project_path).name if project_path else "",
        }
        title = self.inference.infer_title(context)

        # Create new card
        card = self.manager.create_card(
            session_id=session_id,
            title=title,
            branch=branch,
            project_path=project_path,
        )
        self._active_card_id = card.id

        # If there's a branch, try to infer initial checklist from branch name
        if branch and branch not in ("main", "master", "develop"):
            items = self.inference.from_commit_message(f"feat: {title}")
            for item in items:
                card.add_checklist_item(item.text, source=item.source)
            if items:
                self.manager.save()

        return {
            "card_id": card.id,
            "title": card.title,
            "resumed": False,
            "progress": "0/0",
        }

    def on_post_tool_use(
        self,
        tool_name: str,
        tool_input: dict,
        tool_response: str,
    ) -> Optional[str]:
        """
        Called after every tool use. Infers checklist items from tool output.

        Returns optional systemMessage to inject, or None.
        """
        card = self.active_card
        if not card:
            return None

        inferred_items: list[InferredItem] = []

        # Detect git commits from Bash output
        if tool_name == "Bash" and tool_response:
            for match in COMMIT_PATTERN.finditer(tool_response):
                commit_msg = match.group(2)
                items = self.inference.from_commit_message(commit_msg)
                inferred_items.extend(items)

        # Detect plan file reads/writes
        if tool_name in ("Read", "Write", "Edit"):
            file_path = tool_input.get("file_path", "")
            if PLAN_FILE_PATTERN.search(file_path) and tool_name == "Read":
                items = self.inference.from_plan_file(Path(file_path))
                inferred_items.extend(items)

        # Deduplicate and filter
        if inferred_items:
            merged = self.inference.merge_items(inferred_items)
            added = []
            existing_texts = {
                item.text.lower().strip() for item in card.checklist
            }
            for item in merged:
                if item.text.lower().strip() not in existing_texts:
                    card.add_checklist_item(item.text, source=item.source)
                    existing_texts.add(item.text.lower().strip())
                    added.append(item.text)
            if added:
                self.manager.save()
                return f"[Focus Card] Added {len(added)} item(s): {', '.join(added[:3])}"

        return None

    def on_session_end(self, session_id: str) -> dict:
        """
        Called at session end. Pauses card and persists state.

        Returns summary for session-end hook output.
        """
        card = self.active_card
        if not card:
            # Try finding by session_id in case _active_card_id wasn't set
            card = self.manager.get_card_by_session(session_id)

        if not card:
            return {"status": "no_card"}

        done, total = card.progress()

        # Auto-complete if all items done (regardless of current status)
        if card.is_complete() and card.status in (CardStatus.ACTIVE, CardStatus.PAUSED):
            card.complete()
        elif card.status == CardStatus.ACTIVE:
            self.manager.pause_card(card.id)
        # Pinned cards stay pinned through session boundaries

        self.manager.save()

        return {
            "card_id": card.id,
            "title": card.title,
            "status": card.status.value,
            "progress": f"{done}/{total}",
        }


# ============================================================================
# Standalone hook entry points (called from bash hooks or settings.json)
# ============================================================================

def focus_cards_post_tool_hook():
    """
    PostToolUse hook entry point.

    Reads JSON from stdin (tool_name, tool_input, tool_response),
    runs inference, returns JSON response with optional systemMessage.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({}))
            return

        data = json.loads(raw)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        tool_response = str(data.get("tool_response", ""))

        # Load session context from environment
        session_id = os.environ.get("GRIP_SESSION_ID", "")
        if not session_id:
            print(json.dumps({}))
            return

        handler = FocusCardsHookHandler()
        handler._active_card_id = _find_active_card_id(handler.manager, session_id)

        msg = handler.on_post_tool_use(tool_name, tool_input, tool_response)
        if msg:
            print(json.dumps({"systemMessage": msg}))
        else:
            print(json.dumps({}))

    except Exception:
        # Hooks must not crash the session
        print(json.dumps({}))


def _find_active_card_id(manager: FocusCardsManager, session_id: str) -> Optional[str]:
    """Find active card ID for a session."""
    card = manager.get_card_by_session(session_id)
    return card.id if card else None


def _cli_session_start():
    """CLI entry point for session-start hook integration."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--branch", default="")
    parser.add_argument("--project", default="")
    parser.add_argument("--message", default="")
    args = parser.parse_args(sys.argv[2:])  # Skip "session-start"

    handler = FocusCardsHookHandler()
    result = handler.on_session_start(
        session_id=args.session_id,
        branch=args.branch,
        project_path=args.project,
        first_message=args.message,
    )
    print(json.dumps(result))


def _cli_session_end():
    """CLI entry point for session-end hook integration."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args(sys.argv[2:])  # Skip "session-end"

    handler = FocusCardsHookHandler()
    result = handler.on_session_end(session_id=args.session_id)
    print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "session-start":
        _cli_session_start()
    elif len(sys.argv) > 1 and sys.argv[1] == "session-end":
        _cli_session_end()
    else:
        # Default: act as PostToolUse hook (reads JSON from stdin)
        focus_cards_post_tool_hook()
