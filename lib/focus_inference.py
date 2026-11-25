#!/usr/bin/env python3
"""
Focus Cards — Heuristic Inference Engine.

Infers checklist items from session activity: commit messages, tool calls,
conversation patterns, and plan files. Uses simple heuristics, not LLM calls,
for speed and determinism.

Phase 1: Rule-based extraction
Phase 3: KONO-enhanced semantic recall

Usage:
    from focus_inference import FocusInference

    inference = FocusInference()
    items = inference.from_commit_message("fix: resolve auth token expiration")
    items = inference.from_plan_file(Path("plans/auth-refactor.md"))
    title = inference.infer_title(session_context)
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class InferredItem:
    """A checklist item extracted by heuristic analysis."""
    text: str
    source: str      # commit, plan, conversation, tool_call
    confidence: float  # 0.0 to 1.0


class FocusInference:
    """Heuristic engine for inferring card titles and checklist items."""

    # Patterns that indicate actionable items in commit messages
    COMMIT_ACTION_PATTERNS = [
        (r"^(?:feat|fix|refactor|perf|test|docs|chore):\s*(.+)", "commit_type"),
        (r"(?:TODO|FIXME|HACK|XXX):\s*(.+)", "todo_marker"),
        (r"(?:WIP|work.in.progress):\s*(.+)", "wip_marker"),
    ]

    # Patterns for plan file checklist extraction
    PLAN_CHECKLIST_PATTERNS = [
        (r"^[-*]\s+\[[ x]\]\s+(.+)", "markdown_checkbox"),
        (r"^\d+\.\s+(.+)", "numbered_item"),
        (r"^#{2,3}\s+(?:Step|Phase|Sprint)\s+\d+[:\s]+(.+)", "phase_header"),
    ]

    # Patterns that indicate session purpose from conversation
    SESSION_PURPOSE_PATTERNS = [
        (r"(?:implement|build|create|add|write)\s+(.+)", "implementation"),
        (r"(?:fix|resolve|debug|investigate)\s+(.+)", "bugfix"),
        (r"(?:refactor|restructure|reorganize|clean.up)\s+(.+)", "refactor"),
        (r"(?:test|verify|validate|check)\s+(.+)", "testing"),
        (r"(?:review|audit|analyze|assess)\s+(.+)", "review"),
    ]

    # Completion matching (GRIP #2885): a commit "closes" a prior
    # planned/manual checklist item when their normalised content-word
    # sets overlap strongly. Deterministic Jaccard — no LLM (this is
    # value-producing matching, not a safety gate; see
    # rules/llm-augmentation-pattern.md category errors).
    COMPLETION_MATCH_THRESHOLD = 0.5
    _STOPWORDS = frozenset({
        "the", "a", "an", "and", "or", "to", "of", "for", "in", "on",
        "with", "is", "it", "this", "that", "fix", "add", "update",
        "feat", "chore", "refactor", "test", "docs", "perf",
    })

    def from_commit_message(self, message: str) -> list[InferredItem]:
        """Extract checklist items from a commit message."""
        items = []
        lines = message.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue
            for pattern, source in self.COMMIT_ACTION_PATTERNS:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    items.append(InferredItem(
                        text=match.group(1).strip(),
                        source=f"commit:{source}",
                        confidence=0.8,
                    ))
                    break

        return items

    def from_plan_file(self, plan_path: Path) -> list[InferredItem]:
        """Extract checklist items from a plan markdown file."""
        items = []
        if not plan_path.exists():
            return items

        content = plan_path.read_text(encoding="utf-8")
        for line in content.split("\n"):
            line = line.strip()
            for pattern, source in self.PLAN_CHECKLIST_PATTERNS:
                match = re.match(pattern, line)
                if match:
                    text = match.group(1).strip()
                    # Skip very short or generic items
                    if len(text) > 10:
                        items.append(InferredItem(
                            text=text,
                            source=f"plan:{source}",
                            confidence=0.7,
                        ))
                    break

        return items

    def from_git_diff_stat(self, diff_stat: str) -> list[InferredItem]:
        """Infer work items from git diff --stat output."""
        items = []
        lines = diff_stat.strip().split("\n")

        for line in lines:
            # Match: "path/to/file.py | 42 +++++---"
            match = re.match(r"\s*(.+?)\s*\|\s*(\d+)", line)
            if match:
                filepath = match.group(1).strip()
                changes = int(match.group(2))
                if changes > 20:  # Only significant changes
                    items.append(InferredItem(
                        text=f"Review changes in {filepath}",
                        source="diff:significant_change",
                        confidence=0.5,
                    ))

        return items

    def infer_title(self, context: dict) -> str:
        """
        Infer a card title from session context.

        Context keys:
            branch: str — git branch name
            first_message: str — first user message
            project_name: str — project directory name
            commit_messages: list[str] — recent commits
        """
        # Priority 1: Branch name (most descriptive)
        branch = context.get("branch", "")
        if branch and branch not in ("main", "master", "develop", "HEAD"):
            title = self._branch_to_title(branch)
            if title:
                return title

        # Priority 2: First user message (truncated)
        first_msg = context.get("first_message", "")
        if first_msg:
            title = self._message_to_title(first_msg)
            if title:
                return title

        # Priority 3: Most recent commit
        commits = context.get("commit_messages", [])
        if commits:
            return commits[0][:60]

        # Fallback: project name + timestamp
        project = context.get("project_name", "unknown")
        return f"Session: {project}"

    def branch_to_title(self, branch: str) -> Optional[str]:
        """Public: derive a readable card title from a branch name.

        Stable cross-module surface (the hook's _refresh_title uses this)
        so callers don't reach into the private _branch_to_title —
        addresses the HAL Council encapsulation note on #2889.
        """
        return self._branch_to_title(branch)

    def _branch_to_title(self, branch: str) -> Optional[str]:
        """Convert branch name to readable title."""
        # Remove common prefixes
        for prefix in ("feat/", "fix/", "feature/", "bugfix/", "chore/", "refactor/"):
            if branch.startswith(prefix):
                branch = branch[len(prefix):]
                break

        # Replace separators with spaces, capitalize
        title = branch.replace("-", " ").replace("_", " ").strip()
        if len(title) < 3:
            return None
        return title.capitalize()

    def _message_to_title(self, message: str) -> Optional[str]:
        """Extract a title from the first user message."""
        # Take first line, truncate
        first_line = message.strip().split("\n")[0][:80]

        # Try to match a purpose pattern
        for pattern, _ in self.SESSION_PURPOSE_PATTERNS:
            match = re.search(pattern, first_line, re.IGNORECASE)
            if match:
                return match.group(0)[:60].capitalize()

        # Use first line if it looks like a task description
        if len(first_line) > 10 and not first_line.startswith("/"):
            return first_line[:60]

        return None

    @staticmethod
    def _norm_tokens(text: str) -> set:
        """Lowercased content-word set: alnum tokens, stopwords/short dropped."""
        words = re.findall(r"[a-z0-9]+", (text or "").lower())
        return {
            w for w in words
            if len(w) > 2 and w not in FocusInference._STOPWORDS
        }

    def completion_match(self, commit_text: str, pending_items: list):
        """Return the pending item a commit closes, or None.

        Deterministic Jaccard overlap of normalised content words. Used
        by the PostToolUse hook so a commit *completes* a matching open
        planned/manual item instead of accreting a parallel one — half
        of the GRIP #2885 no-completion-path fix. No LLM: this is
        value-producing matching, not a monotone safety gate.
        """
        ct = self._norm_tokens(commit_text)
        if not ct:
            return None
        best, best_score = None, 0.0
        for item in pending_items:
            it = self._norm_tokens(getattr(item, "text", ""))
            if not it:
                continue
            score = len(ct & it) / len(ct | it)
            if score > best_score:
                best, best_score = item, score
        return best if best_score >= self.COMPLETION_MATCH_THRESHOLD else None

    def merge_items(self, items: list[InferredItem], min_confidence: float = 0.5) -> list[InferredItem]:
        """Deduplicate and filter inferred items by confidence."""
        seen_texts = set()
        merged = []
        for item in sorted(items, key=lambda i: i.confidence, reverse=True):
            if item.confidence < min_confidence:
                continue
            # Simple dedup: lowercase + strip
            key = item.text.lower().strip()
            if key not in seen_texts:
                seen_texts.add(key)
                merged.append(item)
        return merged


# CLI interface
if __name__ == "__main__":
    import sys

    inference = FocusInference()

    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        # Demo with sample data
        items = inference.from_commit_message("feat: add user authentication via OAuth2")
        print("From commit:")
        for item in items:
            print(f"  [{item.confidence:.1f}] {item.text} ({item.source})")

        context = {
            "branch": "feat/focus-cards-phase1",
            "first_message": "Implement Focus Cards core data model",
            "project_name": "GRIP",
        }
        title = inference.infer_title(context)
        print(f"\nInferred title: {title}")
    else:
        print("Usage: python3 focus_inference.py demo")
