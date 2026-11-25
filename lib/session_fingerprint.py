#!/usr/bin/env python3
"""Session DNA fingerprint -- unique session signature from tool usage patterns.

Parses Claude Code JSONL session files and generates a compact fingerprint
string (e.g., "REEB-BGGG-RBBB") that captures the temporal tool usage pattern,
plus classification and entropy metrics.
"""

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass

# Tool name -> single-letter type code
TOOL_TYPE_MAP = {
    "Read": "R",
    "Edit": "E",
    "Bash": "B",
    "Glob": "G",
    "Grep": "G",
    "Write": "W",
    "Task": "T",
    "Skill": "S",
}


@dataclass
class Fingerprint:
    pattern: str
    tool_distribution: dict
    dominant_style: str
    entropy: float
    session_path: str
    total_tools: int


def tool_letter(name: str) -> str:
    """Map a tool name to its single-letter code."""
    return TOOL_TYPE_MAP.get(name, "O")


def extract_tools(session_path: str) -> list:
    """Parse a JSONL session file, return ordered list of tool names."""
    tools = []
    with open(session_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name:
                        tools.append(name)
    return tools


def calculate_entropy(distribution: dict) -> float:
    """Shannon entropy of a tool distribution. Higher = more varied."""
    total = sum(distribution.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for count in distribution.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return round(entropy, 4)


def build_fingerprint(
    tools: list, session_path: str = "", n_segments: int = 12
) -> Fingerprint:
    """Build a Fingerprint from an ordered list of tool names.

    Splits the tool sequence into n_segments equal time slices,
    finds the dominant tool type letter for each, then groups
    into dash-separated triplets.
    """
    # Build distribution
    distribution = {}
    for t in tools:
        letter = tool_letter(t)
        distribution[letter] = distribution.get(letter, 0) + 1

    total = len(tools)
    if total == 0:
        return Fingerprint(
            pattern="",
            tool_distribution=distribution,
            dominant_style="balanced",
            entropy=0.0,
            session_path=session_path,
            total_tools=0,
        )

    # Convert to letters
    letters = [tool_letter(t) for t in tools]

    # Split into segments
    segment_size = max(1, total // n_segments)
    segments = []
    for i in range(n_segments):
        start = i * segment_size
        end = start + segment_size if i < n_segments - 1 else total
        chunk = letters[start:end]
        if not chunk:
            # If fewer tools than segments, pad with last known
            segments.append(segments[-1] if segments else "O")
            continue
        # Find dominant letter in this chunk
        freq = {}
        for c in chunk:
            freq[c] = freq.get(c, 0) + 1
        dominant = max(freq, key=freq.get)
        segments.append(dominant)

    # Group into triplets separated by dashes
    triplets = []
    for i in range(0, len(segments), 3):
        triplets.append("".join(segments[i : i + 3]))
    pattern = "-".join(triplets)

    entropy = calculate_entropy(distribution)
    style = classify_session_from_distribution(distribution, total)

    return Fingerprint(
        pattern=pattern,
        tool_distribution=distribution,
        dominant_style=style,
        entropy=entropy,
        session_path=session_path,
        total_tools=total,
    )


def classify_session_from_distribution(distribution: dict, total: int) -> str:
    """Classify session style from tool distribution."""
    if total == 0:
        return "balanced"

    r_g = distribution.get("R", 0) + distribution.get("G", 0)
    e_w = distribution.get("E", 0) + distribution.get("W", 0)
    b = distribution.get("B", 0)
    e = distribution.get("E", 0)
    t = distribution.get("T", 0)

    if r_g / total > 0.6:
        return "explorer"
    if e_w / total > 0.6:
        return "builder"
    if b / total > 0.4 and e > 0:
        return "debugger"
    if t / total > 0.4:
        return "researcher"

    # Check if any single type exceeds 40%
    for count in distribution.values():
        if count / total > 0.4:
            return "balanced"  # Dominant but doesn't match named patterns

    return "balanced"


def classify_session(fp: Fingerprint) -> str:
    """Classify a Fingerprint's session style."""
    return classify_session_from_distribution(fp.tool_distribution, fp.total_tools)


def compare_fingerprints(fp_a: Fingerprint, fp_b: Fingerprint) -> float:
    """Similarity score 0-1 based on character-level edit distance normalized by length."""
    a = fp_a.pattern.replace("-", "")
    b = fp_b.pattern.replace("-", "")

    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    # Levenshtein distance
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp

    max_len = max(m, n)
    return round(1.0 - dp[n] / max_len, 4)


def fingerprint_recent(
    projects_dir: str = "", n: int = 5
) -> list:
    """Fingerprint the last N sessions by modification time."""
    if not projects_dir:
        projects_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects")

    all_jsonl = []
    for root, _dirs, files in os.walk(projects_dir):
        for fname in files:
            if fname.endswith(".jsonl"):
                fpath = os.path.join(root, fname)
                if os.path.getsize(fpath) > 0:
                    all_jsonl.append(fpath)

    # Sort by mtime descending, take N
    all_jsonl.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    recent = all_jsonl[:n]

    results = []
    for path in recent:
        tools = extract_tools(path)
        fp = build_fingerprint(tools, session_path=path)
        results.append(fp)
    return results


def _find_latest_session(projects_dir: str = "") -> str:
    """Find the most recently modified JSONL session file."""
    if not projects_dir:
        projects_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects")

    latest = None
    latest_mtime = 0
    for root, _dirs, files in os.walk(projects_dir):
        for fname in files:
            if fname.endswith(".jsonl"):
                fpath = os.path.join(root, fname)
                mt = os.path.getmtime(fpath)
                if mt > latest_mtime and os.path.getsize(fpath) > 0:
                    latest_mtime = mt
                    latest = fpath
    return latest or ""


def _format_fingerprint(fp: Fingerprint, as_json: bool = False) -> str:
    """Format a Fingerprint for display."""
    if as_json:
        return json.dumps(asdict(fp), indent=2)

    lines = [
        f"Session: {os.path.basename(fp.session_path)}",
        f"Pattern: {fp.pattern}",
        f"Style:   {fp.dominant_style}",
        f"Entropy: {fp.entropy}",
        f"Tools:   {fp.total_tools}",
        f"Distribution: {json.dumps(fp.tool_distribution, sort_keys=True)}",
    ]
    return "\n".join(lines)


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        description="Session DNA fingerprint from tool usage patterns"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")

    sub = parser.add_subparsers(dest="command")

    fp_parser = sub.add_parser("fingerprint", help="Fingerprint a session")
    fp_parser.add_argument("path", nargs="?", default="", help="Session JSONL path")

    recent_parser = sub.add_parser("recent", help="Fingerprint recent sessions")
    recent_parser.add_argument(
        "--last", type=int, default=5, help="Number of recent sessions"
    )

    cmp_parser = sub.add_parser("compare", help="Compare two sessions")
    cmp_parser.add_argument("path_a", help="First session JSONL path")
    cmp_parser.add_argument("path_b", help="Second session JSONL path")

    args = parser.parse_args(argv)

    if args.command == "fingerprint":
        path = args.path or _find_latest_session()
        if not path:
            print("No session files found.", file=sys.stderr)
            return 1
        tools = extract_tools(path)
        fp = build_fingerprint(tools, session_path=path)
        print(_format_fingerprint(fp, as_json=args.json))

    elif args.command == "recent":
        fps = fingerprint_recent(n=args.last)
        if not fps:
            print("No session files found.", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps([asdict(f) for f in fps], indent=2))
        else:
            for i, fp in enumerate(fps):
                if i > 0:
                    print("---")
                print(_format_fingerprint(fp))

    elif args.command == "compare":
        tools_a = extract_tools(args.path_a)
        tools_b = extract_tools(args.path_b)
        fp_a = build_fingerprint(tools_a, session_path=args.path_a)
        fp_b = build_fingerprint(tools_b, session_path=args.path_b)
        score = compare_fingerprints(fp_a, fp_b)
        if args.json:
            print(
                json.dumps(
                    {"similarity": score, "a": asdict(fp_a), "b": asdict(fp_b)},
                    indent=2,
                )
            )
        else:
            print(_format_fingerprint(fp_a))
            print("---")
            print(_format_fingerprint(fp_b))
            print(f"\nSimilarity: {score}")

    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
