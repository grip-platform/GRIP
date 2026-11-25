#!/usr/bin/env python3
"""
Session State Extractor - Extract next_up from session transcript.

Reads a Claude Code JSONL transcript and extracts:
- Completed work (commits, "done", "fixed", "created")
- Remaining work ("TODO", "remaining", "next", "still need")
- Blockers ("blocked", "failed", "error" patterns)

Output: JSON to stdout for session-end.sh to capture.

VERSION: 1.0.0
"""

import json
import re
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional


# Patterns indicating completed work
COMPLETED_PATTERNS = [
    re.compile(r'\b(?:feat|fix|chore|refactor|docs|test|perf|style):\s*(.{10,80})', re.IGNORECASE),
    re.compile(r'\b(?:completed|finished|done|implemented|created|added|fixed|resolved|built)\b.*?[.!]', re.IGNORECASE),
    re.compile(r'(?:file|component|function|module|test)\s+(?:created|written|added)', re.IGNORECASE),
]

# Patterns indicating remaining work
REMAINING_PATTERNS = [
    re.compile(r'\b(?:TODO|FIXME|HACK|remaining|still need|next step|should also|need to)\b.*?[.!]', re.IGNORECASE),
    re.compile(r'\b(?:haven\'t|didn\'t|not yet|pending|upcoming|later)\b.*?[.!]', re.IGNORECASE),
]

# Patterns indicating blockers
# Constrained: cannot span lines, max 120 chars, only actionable keywords
BLOCKER_PATTERNS = [
    re.compile(r'\b(?:blocked|blocking|cannot|can\'t)\b[^.!?\n]{0,120}[.!?]?', re.IGNORECASE),
]


def load_transcript_messages(transcript_path: str, last_n: int = 20) -> List[Dict[str, Any]]:
    """Load the last N user/assistant messages from transcript JSONL."""
    messages = []
    try:
        with open(transcript_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    msg_type = data.get('type', '')
                    if msg_type in ('user', 'assistant'):
                        content = data.get('message', {}).get('content', '')
                        if isinstance(content, list):
                            content = ' '.join(
                                c.get('text', '') for c in content
                                if isinstance(c, dict) and c.get('type') == 'text'
                            )
                        if content and content.strip():
                            messages.append({
                                'type': msg_type,
                                'content': content,
                                'timestamp': data.get('timestamp', ''),
                            })
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        return []

    return messages[-last_n:] if len(messages) > last_n else messages


def extract_matches(text: str, patterns: List[re.Pattern], max_items: int = 5) -> List[str]:
    """Extract unique matches from text using pattern list."""
    results = []
    seen = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            item = match.group(0).strip().rstrip('.')
            # Deduplicate by lowercase
            key = item.lower()
            if key not in seen and len(item) > 10:
                seen.add(key)
                results.append(item[:200])  # Cap length
                if len(results) >= max_items:
                    return results
    return results


def extract_session_state(transcript_path: str, session_id: str = "",
                          last_assistant_msg_path: str = "") -> Optional[Dict[str, Any]]:
    """Extract session state from transcript + optional Stop-hook-captured message.

    The transcript remains source of truth for full conversation (user prompts +
    assistant messages). The Stop hook (CC 2.1.47+) provides the final assistant
    message without JSONL IO, supplementing the transcript.

    Returns dict with completed, remaining, blockers, notes, updated_at
    or None if no meaningful content found.
    """
    messages = load_transcript_messages(transcript_path, last_n=20)

    # Supplement with Stop-hook-captured last assistant message
    last_captured = ""
    if last_assistant_msg_path:
        try:
            last_captured = Path(last_assistant_msg_path).read_text().strip()
        except (IOError, OSError):
            pass

    if not messages and not last_captured:
        return None

    # Combine: captured final message + historical assistant messages
    assistant_text = ' '.join(
        m['content'] for m in messages if m['type'] == 'assistant'
    )
    if last_captured:
        assistant_text = last_captured + ' ' + assistant_text

    # User prompts from transcript (NOT from Stop hook -- it only has assistant)
    user_text = ' '.join(
        m['content'] for m in messages if m['type'] == 'user'
    )
    combined = assistant_text + ' ' + user_text

    completed = extract_matches(assistant_text, COMPLETED_PATTERNS, max_items=8)
    remaining = extract_matches(combined, REMAINING_PATTERNS, max_items=8)
    blockers = extract_matches(combined, BLOCKER_PATTERNS, max_items=5)

    # If nothing extracted, not worth saving
    if not completed and not remaining and not blockers:
        return None

    # Build notes from last user message (task context)
    notes = ""
    user_msgs = [m for m in messages if m['type'] == 'user']
    if user_msgs:
        last_user = user_msgs[-1]['content'][:300]
        notes = f"Last user request: {last_user}"

    return {
        "completed": completed,
        "remaining": remaining,
        "blockers": blockers,
        "notes": notes,
        "session_id": session_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description='Extract session state from transcript')
    parser.add_argument('--transcript', '-t', required=True, help='Path to JSONL transcript')
    parser.add_argument('--session-id', '-s', default='', help='Session ID')
    parser.add_argument('--last-assistant-msg', default='', help='Path to Stop-hook-captured message')
    args = parser.parse_args()

    result = extract_session_state(args.transcript, args.session_id, args.last_assistant_msg)
    if result:
        print(json.dumps(result))
    else:
        # Empty output signals nothing meaningful extracted
        sys.exit(0)


if __name__ == '__main__':
    main()
