#!/usr/bin/env python3
from __future__ import annotations
"""
Autonomous Insight Collector — Session-level insight accumulation.

Captures structured insights during sessions, stacks them in a buffer,
and batch-commits to the learning pipeline at session boundaries.

Architecture:
    Agent emits "★ Insight" block → calls `capture` → stacks in buffer
    Session-end hook → calls `flush` → writes to converge-metrics.jsonl
    Optional: `extract` parses transcripts for missed insights

Buffer: ~/.claude/.insight-stack.json (session-level, cleared on flush)
Target: learning/converge-metrics.jsonl (insight_captured events)

Usage:
    # Agent captures insight during session
    python3 insight_collector.py capture "Focus Cards uses dataclasses for zero-dep simplicity"

    # Session-end hook flushes stack to learning pipeline
    python3 insight_collector.py flush

    # Extract from transcript (backup for missed captures)
    python3 insight_collector.py extract <transcript.jsonl>

    # Show current stack
    python3 insight_collector.py stack
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
STACK_FILE = CLAUDE_DIR / "state" / "audit" / "insight-stack.json"
METRICS_FILE = CLAUDE_DIR / "learning" / "converge-metrics.jsonl"

# Pattern to extract ★ Insight blocks from conversation text
INSIGHT_PATTERN = re.compile(
    r"★ Insight[─ ]+\n(.+?)(?:\n─{10,}|\n`─)",
    re.DOTALL,
)


def load_stack() -> list[dict]:
    """Load the current insight stack."""
    if not STACK_FILE.exists():
        return []
    try:
        data = json.loads(STACK_FILE.read_text())
        return data.get("insights", [])
    except (json.JSONDecodeError, KeyError):
        return []


def save_stack(insights: list[dict]) -> None:
    """Save the insight stack."""
    STACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    STACK_FILE.write_text(json.dumps({
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "insights": insights,
    }, indent=2))


def capture(text: str, source: str = "agent", tags: list[str] | None = None) -> dict:
    """Capture a single insight to the stack."""
    insight = {
        "text": text.strip(),
        "source": source,
        "tags": tags or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    stack = load_stack()
    stack.append(insight)
    save_stack(stack)
    return insight


def flush(session_id: str = "unknown") -> int:
    """Flush the insight stack to the learning pipeline."""
    stack = load_stack()
    if not stack:
        return 0

    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    with open(METRICS_FILE, "a") as f:
        for insight in stack:
            entry = {
                "event": "insight_captured",
                "session_id": session_id,
                "text": insight["text"],
                "source": insight.get("source", "agent"),
                "tags": insight.get("tags", []),
                "captured_at": insight.get("timestamp", now),
                "flushed_at": now,
            }
            f.write(json.dumps(entry) + "\n")

    count = len(stack)
    # Clear the stack
    save_stack([])
    return count


def extract_from_transcript(transcript_path: Path) -> list[str]:
    """Extract insight blocks from a JSONL conversation transcript."""
    insights = []
    if not transcript_path.exists():
        return insights

    with open(transcript_path, "r") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Look in assistant message content
            msg = entry.get("message", {})
            if msg.get("role") != "assistant":
                continue

            content = msg.get("content", [])
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    matches = INSIGHT_PATTERN.findall(text)
                    for match in matches:
                        cleaned = match.strip()
                        if cleaned and len(cleaned) > 20:
                            insights.append(cleaned)

    return insights


def pop() -> dict | None:
    """Pop the most recent insight from the stack. Returns the removed insight."""
    stack = load_stack()
    if not stack:
        return None
    removed = stack.pop()
    save_stack(stack)
    return removed


def peek() -> dict | None:
    """Peek at the most recent insight without removing it."""
    stack = load_stack()
    return stack[-1] if stack else None


def show_stack() -> None:
    """Display the current insight stack."""
    stack = load_stack()
    if not stack:
        print("Insight stack is empty.")
        return
    print(f"Insight stack ({len(stack)} items):")
    for i, insight in enumerate(stack, 1):
        text_preview = insight["text"][:80]
        source = insight.get("source", "?")
        print(f"  {i}. [{source}] {text_preview}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: insight_collector.py {capture|flush|pop|peek|extract|stack} [args]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "capture":
        if len(sys.argv) < 3:
            print("Usage: insight_collector.py capture <text> [--source SOURCE] [--tags TAG1,TAG2]")
            sys.exit(1)
        text = sys.argv[2]
        source = "agent"
        tags = []
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--source" and i + 1 < len(sys.argv):
                source = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--tags" and i + 1 < len(sys.argv):
                tags = sys.argv[i + 1].split(",")
                i += 2
            else:
                i += 1
        insight = capture(text, source=source, tags=tags)
        print(f"Captured: {insight['text'][:60]}...")

    elif cmd == "flush":
        session_id = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        count = flush(session_id=session_id)
        print(f"Flushed {count} insights to {METRICS_FILE}")

    elif cmd == "extract":
        if len(sys.argv) < 3:
            print("Usage: insight_collector.py extract <transcript.jsonl>")
            sys.exit(1)
        path = Path(sys.argv[2])
        insights = extract_from_transcript(path)
        print(f"Extracted {len(insights)} insights from {path}")
        for i, text in enumerate(insights, 1):
            print(f"  {i}. {text[:80]}")

    elif cmd == "pop":
        removed = pop()
        if removed:
            print(f"Popped: {removed['text'][:80]}")
        else:
            print("Stack is empty, nothing to pop.")

    elif cmd == "peek":
        top = peek()
        if top:
            print(f"Top: [{top.get('source', '?')}] {top['text'][:120]}")
        else:
            print("Stack is empty.")

    elif cmd == "stack":
        show_stack()

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
