"""
TaskOutput Cleaner - Extract final assistant summary from raw agent JSONL.

Problem: Claude Code's TaskOutput returns raw conversation JSONL from completed
subagents, including every tool call, hook progress event, and intermediate result.
This is orders of magnitude larger than the actual useful output.

Solution: Parse the JSONL and extract only the final assistant text block(s),
stripping all tool_use, tool_result, progress events, and intermediate messages.

Usage:
    from taskoutput_cleaner import clean_agent_output, clean_jsonl_file

    # Clean raw TaskOutput string
    summary = clean_agent_output(raw_taskoutput_text)

    # Clean a JSONL file on disk
    summary = clean_jsonl_file(Path("~/.claude/projects/.../subagents/agent-xxx.jsonl"))

    # CLI
    python3 taskoutput_cleaner.py <jsonl_file>
    python3 taskoutput_cleaner.py --stats <jsonl_file>
"""

import json
import sys
from pathlib import Path
from typing import Optional


def _extract_text_from_content(content) -> Optional[str]:
    """Extract text from a message content field.

    Content can be:
    - A plain string (direct text)
    - A list of content blocks (each with 'type' and content)

    Only extracts 'text' type blocks, ignoring tool_use/tool_result.
    """
    if isinstance(content, str):
        return content if content.strip() else None

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text.strip():
                    text_parts.append(text)
        return "\n".join(text_parts) if text_parts else None

    return None


def _is_noise_type(line_type: str) -> bool:
    """Check if a JSONL line type is noise (not useful output)."""
    return line_type in ("progress", "system")


def clean_agent_output(raw_output: str, max_chars: int = 0) -> str:
    """Clean raw TaskOutput/agent JSONL output to extract final summary.

    Extracts the last assistant message text content, which is the agent's
    final summary/report. Strips all tool calls, hook progress events,
    intermediate results, and tool_result messages.

    Args:
        raw_output: Raw JSONL string (one JSON object per line) or plain text.
        max_chars: Maximum characters to return. 0 = unlimited.

    Returns:
        Clean summary text from the last assistant message.
        Returns the original text if it's not JSONL format.
    """
    if not raw_output or not raw_output.strip():
        return ""

    lines = raw_output.strip().splitlines()

    # Quick check: is this actually JSONL?
    # Try parsing the first non-empty line
    first_line = ""
    for line in lines:
        if line.strip():
            first_line = line.strip()
            break

    if not first_line.startswith("{"):
        # Not JSONL - return as-is (might be plain text output)
        result = raw_output.strip()
        if max_chars > 0:
            result = result[:max_chars]
        return result

    # Parse JSONL and collect assistant text messages (last one wins)
    last_assistant_text = None
    all_assistant_texts = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        line_type = obj.get("type", "")

        # Skip noise
        if _is_noise_type(line_type):
            continue

        # Only process assistant messages
        if line_type != "assistant":
            continue

        message = obj.get("message", {})
        if not isinstance(message, dict):
            continue

        if message.get("role") != "assistant":
            continue

        content = message.get("content")
        text = _extract_text_from_content(content)
        if text:
            last_assistant_text = text
            all_assistant_texts.append(text)

    if last_assistant_text:
        result = last_assistant_text
        if max_chars > 0:
            result = result[:max_chars]
        return result

    # Fallback: if no clean assistant text found, try to find ANY text content
    # This handles edge cases where the JSONL might have unusual structure
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            message = obj.get("message", {})
            if isinstance(message, dict):
                content = message.get("content")
                text = _extract_text_from_content(content)
                if text and len(text) > 50:  # Skip tiny fragments
                    result = text
                    if max_chars > 0:
                        result = result[:max_chars]
                    return result
        except (json.JSONDecodeError, ValueError):
            continue

    return "[No summary text found in agent output]"


def clean_jsonl_file(filepath: Path, max_chars: int = 0) -> str:
    """Clean a JSONL file on disk to extract final summary.

    Args:
        filepath: Path to the .jsonl file.
        max_chars: Maximum characters to return. 0 = unlimited.

    Returns:
        Clean summary text.
    """
    filepath = Path(filepath).expanduser()
    if not filepath.exists():
        return f"[File not found: {filepath}]"

    raw = filepath.read_text(encoding="utf-8", errors="replace")
    return clean_agent_output(raw, max_chars=max_chars)


def get_stats(raw_output: str) -> dict:
    """Get bloat statistics for raw agent output.

    Returns dict with:
        - total_lines: Total JSONL lines
        - total_bytes: Total raw bytes
        - type_counts: Counter of line types
        - type_bytes: Bytes per line type
        - final_text_chars: Length of extracted summary
        - bloat_ratio: Raw bytes / summary chars
        - savings_pct: Percentage of bytes saved by cleaning
    """
    if not raw_output or not raw_output.strip():
        return {"total_lines": 0, "total_bytes": 0, "bloat_ratio": 0}

    lines = raw_output.strip().splitlines()
    total_bytes = len(raw_output.encode("utf-8"))

    type_counts = {}
    type_bytes = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            t = obj.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
            type_bytes[t] = type_bytes.get(t, 0) + len(line.encode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            type_counts["parse_error"] = type_counts.get("parse_error", 0) + 1

    summary = clean_agent_output(raw_output)
    summary_chars = len(summary)

    bloat_ratio = total_bytes / summary_chars if summary_chars > 0 else 0
    savings_pct = (1 - summary_chars / total_bytes) * 100 if total_bytes > 0 else 0

    return {
        "total_lines": len(lines),
        "total_bytes": total_bytes,
        "type_counts": type_counts,
        "type_bytes": type_bytes,
        "final_text_chars": summary_chars,
        "bloat_ratio": round(bloat_ratio, 1),
        "savings_pct": round(savings_pct, 1),
    }


# =========================================================================
# CLI
# =========================================================================

def main():
    """CLI interface for TaskOutput cleaner."""
    if len(sys.argv) < 2:
        print("Usage: python3 taskoutput_cleaner.py [--stats] <jsonl_file>")
        print("       python3 taskoutput_cleaner.py --stats <jsonl_file>")
        print("       echo '<jsonl>' | python3 taskoutput_cleaner.py -")
        sys.exit(1)

    show_stats = "--stats" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("Error: no file path provided")
        sys.exit(1)

    target = args[0]

    if target == "-":
        raw = sys.stdin.read()
    else:
        filepath = Path(target).expanduser()
        if not filepath.exists():
            print(f"Error: file not found: {filepath}", file=sys.stderr)
            sys.exit(1)
        raw = filepath.read_text(encoding="utf-8", errors="replace")

    if show_stats:
        stats = get_stats(raw)
        print(json.dumps(stats, indent=2))
    else:
        print(clean_agent_output(raw))


if __name__ == "__main__":
    main()
