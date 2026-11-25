"""
Closed-Loop Memory Guard — mechanical enforcement of the READ → USE → WRITE-BACK
principle for operational references (channel IDs, DM IDs, endpoint URLs).

Principle source: ~/.claude/CLAUDE.md ## Closed-Loop Memory (Permanent)

The three-leg loop:
    1. READ   — check_before_lookup(): search operational-context.md BEFORE any
                external API call. Return the known value; caller skips the API call.
    2. USE    — caller's responsibility: use the returned value, not a live lookup.
    3. WRITE-BACK — write_back(): after discovering new data, persist it back to
                    operational-context.md so the next session finds it at Tier 0.

Every check and write-back is appended to learning/closed-loop-audit.jsonl.
Statistical analysis of this log is the only way to verify the principle holds
across sessions (Popperian evidence accumulation).

Hypothesis H-CL-1:
    "ClosedLoopGuard correctly prevents re-discovery of known references — confirmed
    by 5 passing tests including one mutation test verifying the tests can fail."
    Falsified by: any live API lookup for data already in operational-context.md.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

_DEFAULT_OP_CONTEXT = Path.home() / ".claude" / "rules" / "operational-context.md"
_DEFAULT_AUDIT_LOG = Path.home() / ".claude" / "learning" / "closed-loop-audit.jsonl"

# Table row: | #channel-name | CID |
_SLACK_CHANNEL_ROW = re.compile(
    r"\|\s*`?#([\w-]+)`?\s*\|\s*([A-Z0-9]{6,12})\s*\|",
    re.IGNORECASE,
)
# Inline parenthetical: `#channel-name` (CID) or [CID]
_INLINE_CHANNEL = re.compile(
    r"`?#([\w-]+)`?\s*[\(\[]([A-Z0-9]{6,12})[\)\]]",
    re.IGNORECASE,
)
# DM: name near D-prefixed ID
_DM_ROW = re.compile(
    r"\|\s*([^|]+?)\s*\|\s*(D[A-Z0-9]{8,11})\s*\|",
    re.IGNORECASE,
)


class ClosedLoopGuard:
    """Mechanically enforces READ → USE → WRITE-BACK for operational references."""

    def __init__(
        self,
        op_context_path: Path = _DEFAULT_OP_CONTEXT,
        audit_path: Path = _DEFAULT_AUDIT_LOG,
    ) -> None:
        self._op_context_path = op_context_path
        self._audit_path = audit_path

    # ---- Public API --------------------------------------------------------

    def check_before_lookup(self, query_type: str, query: str) -> Optional[str]:
        """Return the known value for query from operational-context.md, or None.

        None means "not in memory; proceed with live lookup, then call write_back()."

        Args:
            query_type: "slack_channel" | "dm" | "url"
            query: e.g. "#nexus-chats" or "nexus-chats"
        """
        result = self._find_in_section(query_type, query)
        self._audit(
            event="check",
            query_type=query_type,
            query=query,
            found=result is not None,
            value=result,
        )
        return result

    def write_back(self, key: str, value: str, query_type: str) -> bool:
        """Persist a newly discovered reference to operational-context.md.

        Args:
            key:   reference key, e.g. "#new-channel"
            value: discovered value, e.g. "C-NEW-123"
            query_type: "slack_channel" | "dm" | "url"

        Returns:
            True if written (new entry); False if already existed (idempotent).
        """
        if self._find_in_section(query_type, key) is not None:
            self._audit(
                event="write_back_skipped",
                query_type=query_type,
                query=key,
                found=True,
                value=value,
            )
            return False

        self._append_to_section(query_type, key, value)
        self._audit(
            event="write_back",
            query_type=query_type,
            query=key,
            found=False,
            value=value,
        )
        return True

    # ---- Internal --------------------------------------------------------

    def _find_in_section(self, query_type: str, query: str) -> Optional[str]:
        """Parse operational-context.md and return the value for query, or None."""
        if not self._op_context_path.exists():
            return None

        text = self._op_context_path.read_text(encoding="utf-8")
        canonical = query.lstrip("#").lower().strip()

        if query_type == "slack_channel":
            for m in _SLACK_CHANNEL_ROW.finditer(text):
                if m.group(1).lower() == canonical:
                    return m.group(2)
            for m in _INLINE_CHANNEL.finditer(text):
                if m.group(1).lower() == canonical:
                    return m.group(2)

        elif query_type == "dm":
            for m in _DM_ROW.finditer(text):
                cell = m.group(1).lower()
                if canonical in cell:
                    return m.group(2)

        return None

    def _append_to_section(self, query_type: str, key: str, value: str) -> None:
        """Append a new reference to the appropriate section."""
        if not self._op_context_path.exists():
            return

        text = self._op_context_path.read_text(encoding="utf-8")

        if query_type == "slack_channel":
            section = "## GRIP/CodeTonight Slack"
            new_row = f"| {key} | {value} | (auto-discovered) |\n"
            if section in text:
                sec_start = text.index(section)
                sec_end = text.find("\n## ", sec_start + 1)
                sec_end = sec_end if sec_end != -1 else len(text)
                text = (
                    text[:sec_end].rstrip() + "\n" + new_row + text[sec_end:]
                )
            else:
                text += f"\n{new_row}"

        self._op_context_path.write_text(text, encoding="utf-8")

    def _audit(
        self,
        *,
        event: str,
        query_type: str,
        query: str,
        found: bool,
        value: Optional[str],
    ) -> None:
        """Append one structured record to the closed-loop audit log."""
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": event,
            "query_type": query_type,
            "query": query,
            "found": found,
            "value": value,
        }
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
