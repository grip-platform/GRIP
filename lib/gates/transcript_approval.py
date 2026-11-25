"""Transcript-Aware Approval Bridge.

Reads the session transcript to detect user approval after a gate block.
Solves the "hooks can't see conversation context" problem by using the
transcript_path field that hooks already receive.

Algorithm:
1. Read last N lines of transcript JSONL (tail, not full parse)
2. Find the most recent GUARDRAIL block for this gate type
3. Check if any user message appeared AFTER that block
4. If user message is not an explicit rejection -> treat as approval
5. Grant file-based approval so the retry succeeds

Safety: Only triggers when there's a clear block->user_response->retry
sequence. Explicit rejections ("no", "cancel", "stop", "don't") are
respected. Ambiguous messages are treated as approval since the user
is responding to a block that asked for explicit approval.

Origin: Gen 469. V>> session where git stash drop was blocked 3 times
despite user saying "drop it" and "admin mode".
"""

import json
import os
from pathlib import Path

# Patterns that indicate the user is explicitly rejecting the action
_REJECTION_PATTERNS = frozenset({
    "no", "nope", "cancel", "stop", "abort", "don't", "dont",
    "never mind", "nevermind", "skip", "forget it", "back",
})

# Maximum number of transcript lines to read from the tail
_MAX_TAIL_LINES = 40

# Gate marker prefix to search for in tool_result content
_GUARDRAIL_PREFIX = "[GUARDRAIL: DESTRUCTIVE GIT]"


def _tail_transcript(transcript_path: str, max_lines: int = _MAX_TAIL_LINES) -> list[str]:
    """Read the last N lines of a JSONL transcript file efficiently.

    Uses seek-from-end to avoid reading the entire file (transcripts
    can be megabytes for long sessions).
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return []

    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return []

            chunk_size = min(file_size, max_lines * 4096)
            f.seek(max(0, file_size - chunk_size))
            data = f.read().decode("utf-8", errors="replace")

        lines = data.strip().split("\n")
        return lines[-max_lines:]
    except (OSError, UnicodeDecodeError):
        return []


def _parse_transcript_entries(lines: list[str]) -> list[dict]:
    """Parse JSONL lines into structured entries, skipping malformed lines."""
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _is_user_message(entry: dict) -> str | None:
    """Extract user message text if this is a user message. Returns None otherwise."""
    if entry.get("type") != "user":
        return None
    msg = entry.get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
                elif item.get("type") == "tool_result":
                    texts.append(str(item.get("content", "")))
        return " ".join(texts) if texts else None
    return None


def _is_guardrail_block(entry: dict, prefix: str = _GUARDRAIL_PREFIX) -> bool:
    """Check if this transcript entry contains a guardrail block."""
    content = entry.get("content", "")
    if isinstance(content, str) and prefix in content:
        return True

    msg = entry.get("message", {})
    msg_content = msg.get("content", [])
    if isinstance(msg_content, list):
        for item in msg_content:
            if isinstance(item, dict):
                item_content = str(item.get("content", ""))
                if prefix in item_content:
                    return True
    return False


def _is_rejection(user_text: str) -> bool:
    """Check if user message is an explicit rejection."""
    text_lower = user_text.strip().lower()
    words = text_lower.split()
    if len(words) <= 3:
        return any(word in _REJECTION_PATTERNS for word in words)
    return any(pattern in text_lower for pattern in _REJECTION_PATTERNS)


def check_transcript_for_approval(
    transcript_path: str,
    guardrail_prefix: str = _GUARDRAIL_PREFIX,
    log_fn=None,
) -> bool:
    """Check if the user approved a previously-blocked action via conversation.

    Reads the transcript tail, finds the most recent guardrail block,
    and checks if the user responded (anything not an explicit rejection).

    Returns True if user approval was detected, False otherwise.
    """
    lines = _tail_transcript(transcript_path)
    if not lines:
        return False

    entries = _parse_transcript_entries(lines)
    if not entries:
        return False

    # Walk backwards to find the most recent guardrail block
    last_block_idx = None
    for i in range(len(entries) - 1, -1, -1):
        if _is_guardrail_block(entries[i], guardrail_prefix):
            last_block_idx = i
            break

    if last_block_idx is None:
        return False

    # Check for user messages AFTER the block
    user_messages_after_block = []
    for i in range(last_block_idx + 1, len(entries)):
        user_text = _is_user_message(entries[i])
        if user_text:
            user_messages_after_block.append(user_text)

    if not user_messages_after_block:
        return False

    latest_user_msg = user_messages_after_block[-1]

    if _is_rejection(latest_user_msg):
        if log_fn:
            log_fn("INFO", f"Transcript approval: user rejected ({latest_user_msg[:50]})")
        return False

    if log_fn:
        log_fn("INFO", f"Transcript approval: user approved ({latest_user_msg[:50]})")
    return True
