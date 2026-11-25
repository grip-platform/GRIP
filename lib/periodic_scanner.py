#!/usr/bin/env python3
"""
Periodic Pattern Scanner
========================

Analyzes recent conversation for generalizable patterns when
[PERIODIC: PATTERN_SCAN_DUE] signal is emitted.

Uses similar detection logic to learning-detector.py but focused on:
1. Repeated patterns (3x YSH threshold)
2. Novel problem-solving approaches
3. Workarounds that should become permanent
4. User corrections that indicate missing skills

Usage:
    python3 periodic-scanner.py <transcript_path> [--last-n 15]

Output:
    [PATTERN DETECTED: {description}] or [NO PATTERNS]

Version: 1.0.0 (Gen 239)
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Optional

# Configuration
CLAUDE_DIR = Path.home() / ".claude"
OPERATION_STATE = CLAUDE_DIR / "state" / "session" / "operation-state.json"
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

# Pattern detection thresholds
YSH_THRESHOLD = 3  # Pattern must appear 3x to trigger YSH
MIN_NOVELTY_SCORE = 0.7  # Minimum novelty to propose skill creation

# Patterns that indicate generalizable behavior
GENERALIZABLE_PATTERNS = [
    # Repeated tool sequences
    r"(Read|Edit|Write|Bash).*\1.*\1",

    # Workaround indicators
    r"workaround|hack|temporary|for now|quick fix",

    # User correction signals
    r"no,?\s+(actually|instead|I meant)",
    r"that's not (what|how)",
    r"try (again|this instead)",

    # Skill-worthy patterns
    r"every time I|always have to|keep doing",
    r"wish (claude|you|this) could",
    r"automate (this|that)",
]

# Patterns to exclude (meta-discussion, not actual patterns)
EXCLUSION_PATTERNS = [
    r"PATTERN DETECTED",  # Don't detect our own detection
    r"skill.creation",
    r"PERIODIC.*SCAN",
]


def log_scanner(level: str, message: str):
    """Log scanner activity."""
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(HOOK_LOG, "a") as f:
            f.write(f"{timestamp} [SCANNER][{level}] {message}\n")
    except Exception:
        pass


def load_transcript(transcript_path: str, last_n: int = 15) -> list[dict]:
    """Load last N entries from conversation JSONL."""
    entries = []

    if not Path(transcript_path).exists():
        return entries

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log_scanner("ERROR", f"Failed to load transcript: {e}")
        return entries

    # Return last N entries
    return entries[-last_n:] if len(entries) > last_n else entries


def extract_content(entries: list[dict]) -> str:
    """Extract text content from entries."""
    content_parts = []

    for entry in entries:
        if "message" in entry:
            msg = entry["message"]
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    content_parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            text = block.get("text", "")
                            if text:
                                content_parts.append(text)

        if "tool_input" in entry:
            content_parts.append(json.dumps(entry["tool_input"]))

    return "\n".join(content_parts)


def extract_tool_sequence(entries: list[dict]) -> list[str]:
    """Extract sequence of tool names from entries."""
    tools = []

    for entry in entries:
        if "tool_name" in entry:
            tools.append(entry["tool_name"])

    return tools


def detect_repeated_sequences(tools: list[str], min_length: int = 3) -> list[tuple]:
    """Detect repeated tool sequences."""
    sequences = []

    # Generate all subsequences of min_length
    for i in range(len(tools) - min_length + 1):
        seq = tuple(tools[i:i + min_length])
        sequences.append(seq)

    # Count occurrences
    counts = Counter(sequences)

    # Return sequences that appear >= YSH_THRESHOLD times
    return [(seq, count) for seq, count in counts.items() if count >= YSH_THRESHOLD]


def detect_text_patterns(content: str) -> list[tuple]:
    """Detect generalizable patterns in text content."""
    patterns_found = []

    # Check exclusions first
    for excl in EXCLUSION_PATTERNS:
        if re.search(excl, content, re.IGNORECASE):
            log_scanner("INFO", "Skipping meta-discussion content")
            return []

    # Check each pattern
    for pattern in GENERALIZABLE_PATTERNS:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            patterns_found.append((pattern, len(matches), matches[:3]))  # Keep first 3 examples

    return patterns_found


def calculate_novelty_score(patterns: list, tool_sequences: list) -> float:
    """Calculate novelty score (0.0 - 1.0) based on detected patterns."""
    if not patterns and not tool_sequences:
        return 0.0

    score = 0.0

    # Text patterns contribute up to 0.5
    if patterns:
        pattern_score = min(len(patterns) * 0.15, 0.5)
        score += pattern_score

    # Tool sequences contribute up to 0.5
    if tool_sequences:
        seq_score = min(len(tool_sequences) * 0.2, 0.5)
        score += seq_score

    return min(score, 1.0)


def format_detection_result(
    patterns: list,
    tool_sequences: list,
    novelty_score: float
) -> str:
    """Format the detection result as output."""
    if novelty_score < MIN_NOVELTY_SCORE and not tool_sequences:
        return "[NO PATTERNS]"

    descriptions = []

    # Describe tool sequences
    for seq, count in tool_sequences:
        seq_str = " -> ".join(seq)
        descriptions.append(f"Tool sequence '{seq_str}' repeated {count}x")

    # Describe text patterns
    for pattern, count, examples in patterns[:3]:  # Limit to 3 patterns
        if examples:
            example = examples[0] if isinstance(examples[0], str) else str(examples[0])
            descriptions.append(f"Pattern '{example}' detected {count}x")

    if not descriptions:
        return "[NO PATTERNS]"

    # Build output
    desc_text = "; ".join(descriptions[:3])  # Limit descriptions
    result = f"[PATTERN DETECTED: {desc_text}]"

    if novelty_score >= MIN_NOVELTY_SCORE:
        result += f" [NOVELTY: {novelty_score:.1f}] Consider skill creation."

    return result


def clear_pattern_scan_gate():
    """Clear the pattern_scan from pending gates.

    RSI WRITE SCOPE (security audit documented):
    This script ONLY writes to .operation-state.json (pending_gates removal).
    It MUST NOT modify: context-gate-hook.py, patterns.json, settings.json
    """
    try:
        if OPERATION_STATE.exists():
            state = json.loads(OPERATION_STATE.read_text())
            if "pattern_scan" in state.get("pending_gates", []):
                state["pending_gates"].remove("pattern_scan")
                OPERATION_STATE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log_scanner("ERROR", f"Failed to clear gate: {e}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Scan for generalizable patterns")
    parser.add_argument("transcript_path", nargs="?", help="Path to conversation JSONL")
    parser.add_argument("--last-n", type=int, default=15, help="Number of entries to scan")
    args = parser.parse_args()

    # If no transcript path, try to find it from environment or recent sessions
    transcript_path = args.transcript_path
    if not transcript_path:
        # Try to find most recent session
        projects_dir = CLAUDE_DIR / "projects"
        if projects_dir.exists():
            jsonl_files = list(projects_dir.rglob("*.jsonl"))
            if jsonl_files:
                # Sort by modification time, get most recent
                transcript_path = str(max(jsonl_files, key=lambda p: p.stat().st_mtime))

    if not transcript_path or not Path(transcript_path).exists():
        print("[NO PATTERNS] No transcript available for analysis")
        return

    log_scanner("INFO", f"Scanning {transcript_path} (last {args.last_n} entries)")

    # Load and analyze
    entries = load_transcript(transcript_path, args.last_n)
    if not entries:
        print("[NO PATTERNS] Empty transcript")
        return

    content = extract_content(entries)
    tools = extract_tool_sequence(entries)

    # Detect patterns
    text_patterns = detect_text_patterns(content)
    tool_sequences = detect_repeated_sequences(tools)

    # Calculate novelty
    novelty = calculate_novelty_score(text_patterns, tool_sequences)

    log_scanner("INFO", f"Found {len(text_patterns)} text patterns, {len(tool_sequences)} tool sequences, novelty={novelty:.2f}")

    # Format and output result
    result = format_detection_result(text_patterns, tool_sequences, novelty)
    print(result)

    # Clear the pending gate
    clear_pattern_scan_gate()


if __name__ == "__main__":
    main()
