#!/usr/bin/env python3
"""Auto-TODO resolver: match MEMORY.md TODOs against merged PRs.

Scans MEMORY.md TODO table and matches items against merged PRs
using Jaccard similarity with file path and commit message boosting.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from path_encoding import encode_project_path
from claude_dir import get_claude_dir


# --- Data structures ---

@dataclass
class TodoItem:
    """A single TODO from MEMORY.md."""
    number: int
    description: str
    origin: str
    status: str
    notes: str


@dataclass
class PrInfo:
    """Merged PR metadata."""
    number: int
    title: str
    body: str
    files_changed: List[str] = field(default_factory=list)
    merged_at: str = ""


@dataclass
class Match:
    """A scored match between a TODO and a PR."""
    todo: TodoItem
    pr: PrInfo
    score: float
    confidence: str
    reasons: List[str] = field(default_factory=list)


# --- Constants ---

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_NONE = "NO MATCH"

FILE_BOOST = 0.2
COMMIT_BOOST = 0.1

THRESHOLD_HIGH = 0.7
THRESHOLD_MEDIUM = 0.5
THRESHOLD_LOW = 0.3


# --- Tokenization ---

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "its", "as", "be", "was",
    "are", "been", "has", "have", "had", "do", "does", "did", "this",
    "that", "these", "those", "not", "no", "so", "if", "then", "than",
    "can", "will", "all", "each", "any", "which", "when", "where", "how",
    "what", "who", "up", "out", "into", "over", "after", "before",
})


def tokenize(text: str) -> set:
    """Tokenize text into lowercase word set, filtering stop words and short tokens."""
    if not text:
        return set()
    words = re.findall(r"[a-zA-Z0-9_-]+", text.lower())
    return {w for w in words if len(w) > 1 and w not in _STOP_WORDS}


# --- Similarity ---

def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def classify_confidence(score: float) -> str:
    """Classify a score into a confidence level."""
    if score > THRESHOLD_HIGH:
        return CONFIDENCE_HIGH
    elif score > THRESHOLD_MEDIUM:
        return CONFIDENCE_MEDIUM
    elif score > THRESHOLD_LOW:
        return CONFIDENCE_LOW
    return CONFIDENCE_NONE


# --- Parsing ---

def parse_todos(memory_content: str) -> List[TodoItem]:
    """Extract TODO items from MEMORY.md table.

    Expected format:
    | # | Item | Origin | Status | Notes |
    |---|------|--------|--------|-------|
    | 45 | Description | Gen 297 | PENDING | Some notes |
    """
    todos = []
    # Match table rows with 5 columns: #, Item, Origin, Status, Notes
    pattern = re.compile(
        r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|",
        re.MULTILINE,
    )
    for m in pattern.finditer(memory_content):
        num_str, desc, origin, status, notes = m.groups()
        # Skip header separator rows
        if set(num_str.strip()) <= {"-"}:
            continue
        todos.append(TodoItem(
            number=int(num_str.strip()),
            description=desc.strip(),
            origin=origin.strip(),
            status=status.strip(),
            notes=notes.strip(),
        ))
    return todos


# --- PR fetching ---

def fetch_merged_prs(limit: int = 100, pr_data: Optional[List[dict]] = None) -> List[PrInfo]:
    """Fetch merged PRs. If pr_data is provided, use it instead of calling gh CLI.

    Args:
        limit: Maximum number of PRs to fetch.
        pr_data: Optional list of dicts with keys: number, title, body, files, mergedAt.
                 Used for testing / dependency injection.
    """
    if pr_data is not None:
        return [
            PrInfo(
                number=pr.get("number", 0),
                title=pr.get("title", ""),
                body=pr.get("body", ""),
                files_changed=pr.get("files", []),
                merged_at=pr.get("mergedAt", ""),
            )
            for pr in pr_data
        ]

    # Real gh CLI call
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--state", "merged",
                "--limit", str(limit),
                "--json", "number,title,body,files,mergedAt",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"Warning: gh pr list failed: {result.stderr}", file=sys.stderr)
            return []
        raw = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Warning: could not fetch PRs: {exc}", file=sys.stderr)
        return []

    prs = []
    for item in raw:
        files = []
        for f in item.get("files", []) or []:
            if isinstance(f, dict):
                files.append(f.get("path", ""))
            elif isinstance(f, str):
                files.append(f)
        prs.append(PrInfo(
            number=item.get("number", 0),
            title=item.get("title", ""),
            body=item.get("body", ""),
            files_changed=[p for p in files if p],
            merged_at=item.get("mergedAt", ""),
        ))
    return prs


# --- Matching ---

def match_todo_to_prs(
    todo: TodoItem,
    prs: List[PrInfo],
    commit_messages: Optional[List[str]] = None,
) -> List[Match]:
    """Score a TODO against each PR and return matches sorted by score descending.

    Args:
        todo: The TODO item to match.
        prs: List of PRs to match against.
        commit_messages: Optional list of commit messages for commit-keyword boost.
    """
    if not prs:
        return []

    todo_tokens = tokenize(todo.description) | tokenize(todo.notes)
    matches = []

    for pr in prs:
        reasons = []
        pr_tokens = tokenize(pr.title) | tokenize(pr.body)
        score = jaccard_similarity(todo_tokens, pr_tokens)

        if score > 0:
            reasons.append(f"Jaccard similarity: {score:.3f}")

        # File path boost
        file_tokens = set()
        for fp in pr.files_changed:
            file_tokens |= tokenize(fp)
        if todo_tokens & file_tokens:
            overlap = todo_tokens & file_tokens
            score += FILE_BOOST
            reasons.append(f"File path overlap: {', '.join(sorted(overlap))}")

        # Commit message boost
        if commit_messages:
            commit_tokens = set()
            for msg in commit_messages:
                commit_tokens |= tokenize(msg)
            if todo_tokens & commit_tokens:
                overlap = todo_tokens & commit_tokens
                score += COMMIT_BOOST
                reasons.append(f"Commit keyword overlap: {', '.join(sorted(overlap))}")

        # Cap at 1.0
        score = min(score, 1.0)
        confidence = classify_confidence(score)

        matches.append(Match(
            todo=todo,
            pr=pr,
            score=score,
            confidence=confidence,
            reasons=reasons,
        ))

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


# --- Reporting ---

def generate_report(matches: dict) -> str:
    """Generate a formatted resolution report.

    Args:
        matches: dict mapping TODO number to list of Match objects.

    Returns:
        Formatted report string.
    """
    if not matches:
        return "No matches found."

    lines = ["# TODO Resolution Report", ""]
    for todo_num in sorted(matches.keys()):
        todo_matches = matches[todo_num]
        if not todo_matches:
            continue
        todo = todo_matches[0].todo
        lines.append(f"## TODO #{todo_num}: {todo.description}")
        lines.append(f"Status: {todo.status} | Origin: {todo.origin}")
        lines.append("")

        for m in todo_matches:
            if m.confidence == CONFIDENCE_NONE:
                continue
            lines.append(f"  - PR #{m.pr.number}: {m.pr.title}")
            lines.append(f"    Score: {m.score:.3f} ({m.confidence})")
            for reason in m.reasons:
                lines.append(f"    - {reason}")
            lines.append("")

        # Check if any matches had confidence
        has_match = any(m.confidence != CONFIDENCE_NONE for m in todo_matches)
        if not has_match:
            lines.append("  No confident matches found.")
            lines.append("")

    return "\n".join(lines)


def find_unresolved(matches: dict, threshold: float = 0.6) -> List[TodoItem]:
    """Return TODOs with no match above the threshold.

    Args:
        matches: dict mapping TODO number to list of Match objects.
        threshold: Minimum score for a TODO to be considered resolved.
    """
    unresolved = []
    for todo_num, todo_matches in matches.items():
        best_score = max((m.score for m in todo_matches), default=0.0)
        if best_score < threshold:
            if todo_matches:
                unresolved.append(todo_matches[0].todo)
    return unresolved


def apply_resolutions(
    memory_content: str,
    matches: dict,
    threshold: float = 0.6,
) -> str:
    """Update MEMORY.md content, marking resolved TODOs.

    For each TODO with a best match above the threshold, update the status
    from PENDING to DONE and append the PR number to the notes.

    Args:
        memory_content: Current MEMORY.md content.
        matches: dict mapping TODO number to list of Match objects.
        threshold: Minimum score to consider a TODO resolved.

    Returns:
        Updated MEMORY.md content.
    """
    updated = memory_content
    for todo_num, todo_matches in matches.items():
        if not todo_matches:
            continue
        best = max(todo_matches, key=lambda m: m.score)
        if best.score < threshold:
            continue

        todo = best.todo
        # Build a regex to find this specific row and update it
        # Match the row with this TODO number
        row_pattern = re.compile(
            r"(\|\s*" + str(todo_num) + r"\s*\|.*?\|\s*.*?\|\s*)"
            r"PENDING"
            r"(\s*\|.*?\|)",
            re.DOTALL,
        )
        match = row_pattern.search(updated)
        if match:
            # Replace PENDING with DONE and append PR info to notes
            prefix = match.group(1)
            suffix = match.group(2)
            # Extract existing notes from suffix
            notes_match = re.match(r"\s*\|\s*(.*?)\s*\|", suffix)
            existing_notes = notes_match.group(1) if notes_match else ""
            pr_note = f"Resolved by PR #{best.pr.number}"
            new_notes = f"{existing_notes}. {pr_note}" if existing_notes else pr_note
            new_row = f"{prefix}DONE | {new_notes} |"
            updated = updated[:match.start()] + new_row + updated[match.end():]

    return updated


# --- CLI ---

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="todo_resolver",
        description="Match MEMORY.md TODOs against merged PRs.",
    )
    parser.add_argument(
        "--memory", "-m",
        default=str(get_claude_dir() / "projects" / encode_project_path(get_claude_dir()) / "memory" / "MEMORY.md"),
        help="Path to MEMORY.md file",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int, default=100,
        help="Maximum number of merged PRs to fetch (default: 100)",
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float, default=0.6,
        help="Confidence threshold for resolution (default: 0.6)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.add_parser("scan", help="Show TODO-to-PR matches")
    subparsers.add_parser("apply", help="Update MEMORY.md with resolutions")
    subparsers.add_parser("unresolved", help="List TODOs with no confident match")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    # Read MEMORY.md
    memory_path = Path(args.memory)
    if not memory_path.exists():
        print(f"Error: {memory_path} not found", file=sys.stderr)
        return 1

    memory_content = memory_path.read_text()
    todos = parse_todos(memory_content)

    if not todos:
        print("No TODOs found in MEMORY.md")
        return 0

    # Fetch PRs
    prs = fetch_merged_prs(limit=args.limit)

    # Build matches
    all_matches = {}
    for todo in todos:
        if todo.status.upper() in ("DONE", "LEARNED", "FIXED", "SKIPPED"):
            continue
        todo_matches = match_todo_to_prs(todo, prs)
        all_matches[todo.number] = todo_matches

    if args.command == "scan":
        report = generate_report(all_matches)
        print(report)
        return 0

    elif args.command == "apply":
        updated = apply_resolutions(memory_content, all_matches, threshold=args.threshold)
        if updated == memory_content:
            print("No TODOs resolved. MEMORY.md unchanged.")
        else:
            memory_path.write_text(updated)
            print(f"Updated {memory_path}")
        return 0

    elif args.command == "unresolved":
        unresolved = find_unresolved(all_matches, threshold=args.threshold)
        if not unresolved:
            print("All TODOs have confident matches!")
        else:
            print(f"Unresolved TODOs ({len(unresolved)}):")
            for todo in unresolved:
                print(f"  #{todo.number}: {todo.description} [{todo.status}]")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
