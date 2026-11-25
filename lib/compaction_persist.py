#!/usr/bin/env python3
"""
compaction_persist.py -- Pre-compaction fact extraction and persistence.

Scans the current session JSONL for ephemeral facts (PR numbers, branch names,
file paths, task IDs, key decisions) and persists them to session-context.md
so they survive Claude Code context compaction.

Usage:
    python3 lib/compaction_persist.py              # Auto-detect + persist if threshold met
    python3 lib/compaction_persist.py --force       # Persist now regardless of heuristic
    python3 lib/compaction_persist.py --dry-run     # Show what would be persisted
    python3 lib/compaction_persist.py --session FILE # Use specific session file

Part of GRIP v6.0.0 -- TODO #119
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# --- Fact Extraction ---

# Pre-compiled patterns for performance
_PR_PATTERN = re.compile(r"(?:PR\s*|#)(\d{1,5})\b")
_BRANCH_PATTERN = re.compile(r"\b(feat|fix|chore|docs|test|refactor|perf|hotfix)/[\w./-]+")
_FILE_PATH_PATTERN = re.compile(
    r"(?:^|[\s\"'])([a-zA-Z0-9_./-]+\.[a-zA-Z]{1,10})\b"
)
_TASK_ID_PATTERN = re.compile(r"#(\d{1,5})\b")
_DECISION_PATTERN = re.compile(
    r".*(decided|choosing|going with|settled on|picked|selected|opting for).*",
    re.IGNORECASE,
)

# Minimum path depth to avoid matching bare filenames like "a.py"
_MIN_PATH_SEGMENTS = 2

# Common false-positive file extensions to skip
_SKIP_EXTENSIONS = {".0", ".1", ".2", ".3", ".4", ".5", ".6", ".7", ".8", ".9"}


def _extract_text_from_entry(entry: dict) -> str:
    """Extract human-readable text from a JSONL entry, skipping thinking blocks."""
    parts: list[str] = []

    msg = entry.get("message", {})
    if isinstance(msg, dict):
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    # Skip thinking blocks -- only extract visible content
                    if block.get("type") == "thinking":
                        continue
                    text = block.get("text", "")
                    if text:
                        parts.append(text)

    return "\n".join(parts)


def extract_session_facts(session_file: str) -> dict:
    """Scan a JSONL session file and extract structured facts.

    Returns dict with keys: pr_numbers, branches, file_paths, task_ids, decisions.
    Only extracts facts, never conversation content (privacy).
    """
    pr_numbers: set[str] = set()
    branches: set[str] = set()
    file_paths: set[str] = set()
    task_ids: set[str] = set()
    decisions: list[str] = []
    message_count = 0

    path = Path(session_file)
    if not path.exists() or path.stat().st_size == 0:
        return {
            "pr_numbers": sorted(pr_numbers),
            "branches": sorted(branches),
            "file_paths": sorted(file_paths),
            "task_ids": sorted(task_ids),
            "decisions": decisions,
            "message_count": 0,
        }

    seen_decisions: set[str] = set()

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            if entry_type in ("user", "assistant"):
                message_count += 1

            text = _extract_text_from_entry(entry)
            if not text:
                continue

            # PR numbers
            for m in _PR_PATTERN.finditer(text):
                num = m.group(1)
                # Filter noise: PR/issue numbers are typically > 0 and < 99999
                if 1 <= int(num) <= 99999:
                    pr_numbers.add(f"#{num}")

            # Branch names
            for m in _BRANCH_PATTERN.finditer(text):
                branches.add(m.group(0))

            # File paths (only paths with directory separators to reduce noise)
            for m in _FILE_PATH_PATTERN.finditer(text):
                fp = m.group(1)
                ext = Path(fp).suffix
                if ext in _SKIP_EXTENSIONS:
                    continue
                if "/" in fp and len(fp.split("/")) >= _MIN_PATH_SEGMENTS:
                    # Skip dependency/build paths
                    if not any(
                        seg in fp
                        for seg in (
                            "node_modules",
                            "__pycache__",
                            ".next",
                            "dist/",
                            "venv/",
                        )
                    ):
                        file_paths.add(fp)

            # Task IDs (subset of PR pattern but contextual)
            for m in _TASK_ID_PATTERN.finditer(text):
                task_ids.add(f"#{m.group(1)}")

            # Key decisions (deduplicated by normalized content)
            if _DECISION_PATTERN.match(text):
                for line_text in text.split("\n"):
                    line_text = line_text.strip()
                    if _DECISION_PATTERN.match(line_text) and len(line_text) < 200:
                        norm = line_text.lower().strip()
                        if norm not in seen_decisions:
                            seen_decisions.add(norm)
                            decisions.append(line_text)

    # Cap decisions to most recent 10 to avoid bloat
    decisions = decisions[-10:]

    return {
        "pr_numbers": sorted(pr_numbers),
        "branches": sorted(branches),
        "file_paths": sorted(file_paths),
        "task_ids": sorted(task_ids),
        "decisions": decisions,
        "message_count": message_count,
    }


# --- Persistence ---

_SECTION_HEADER = "## Session Facts (auto-persisted)"


def _load_existing_facts(memory_path: str) -> set[str]:
    """Load already-persisted fact lines (full text after '- ' prefix)."""
    existing: set[str] = set()
    path = Path(memory_path)
    if not path.exists():
        return existing

    in_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == _SECTION_HEADER:
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break  # Next section
            stripped = line.strip()
            if stripped.startswith("- "):
                existing.add(stripped[2:].strip())

    return existing


def _parse_items_from_line(line: str, prefix: str) -> set[str]:
    """Extract individual items from a persisted line like 'PRs referenced: #1, #2'."""
    if not line.startswith(prefix):
        return set()
    rest = line[len(prefix):]
    return {item.strip() for item in rest.split(",") if item.strip()}


# Aggregate fact prefixes that get merged (updated in-place) rather than appended
_AGGREGATE_PREFIXES = {
    "pr_numbers": "PRs referenced: ",
    "branches": "Branches: ",
    "file_paths": "Key files: ",
    "task_ids": "Task IDs: ",
}


def _content_with_section(path: Path) -> str:
    """The file's text, with the auto-persisted section header appended if absent."""
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if _SECTION_HEADER not in content:
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"\n{_SECTION_HEADER}\n"
    return content


def _section_bounds(lines: list[str]) -> tuple[int | None, int | None]:
    """(first line after the section header, index of the next '## ' header or EOF)."""
    section_start = None
    section_end = None
    for i, line in enumerate(lines):
        if line.strip() == _SECTION_HEADER:
            section_start = i + 1
        elif section_start is not None and line.startswith("## "):
            section_end = i
            break
    if section_start is not None and section_end is None:
        section_end = len(lines)
    return section_start, section_end


def _section_entries(
    lines: list[str], start: int | None, end: int | None
) -> list[tuple[int, str]]:
    """(index, text after '- ') for every bullet line inside the section."""
    if start is None:
        return []
    bullets = [(i, lines[i].strip()) for i in range(start, end)]
    return [(i, s[2:].strip()) for i, s in bullets if s.startswith("- ")]


def _new_aggregate_items(facts: dict, key: str) -> set[str]:
    new_items = set(facts.get(key, []))
    if key == "file_paths":
        new_items = set(list(sorted(new_items))[:20])  # Cap to 20
    return new_items


def _existing_aggregate(
    entries: list[tuple[int, str]], prefix: str
) -> tuple[int | None, set[str]]:
    """Index and items of the existing line for an aggregate prefix, if any."""
    for idx, text in entries:
        if text.startswith(prefix):
            return idx, _parse_items_from_line(text, prefix)
    return None, set()


def _merge_aggregates(
    facts: dict, lines: list[str], entries: list[tuple[int, str]],
    section_end: int, changed: list[str],
) -> tuple[int, list[tuple[int, str]]]:
    """Merge aggregate facts into their lines in place; return (section_end, entries)."""
    for key, prefix in _AGGREGATE_PREFIXES.items():
        new_items = _new_aggregate_items(facts, key)
        if not new_items:
            continue
        existing_idx, existing_items = _existing_aggregate(entries, prefix)
        merged = existing_items | new_items
        if merged == existing_items:
            continue  # Nothing new
        merged_line = f"{prefix}{', '.join(sorted(merged))}"
        if existing_idx is not None:
            lines[existing_idx] = f"- {merged_line}"
        else:
            lines.insert(section_end, f"- {merged_line}")
            section_end += 1  # Adjust indices after insertion
            entries = [(i + 1 if i >= section_end - 1 else i, t) for i, t in entries]
        changed.append(merged_line)
    return section_end, entries


def _append_decisions(
    facts: dict, lines: list[str], entries: list[tuple[int, str]],
    section_end: int, changed: list[str],
) -> None:
    """Append decisions not already present (no merge, just dedup)."""
    seen = {text for _, text in entries if text.startswith("Decision: ")}
    for decision in facts.get("decisions", []):
        line = f"Decision: {decision[:150]}"
        if line not in seen:
            lines.insert(section_end, f"- {line}")
            section_end += 1
            seen.add(line)
            changed.append(line)


def persist_facts(facts: dict, memory_path: str) -> list[str]:
    """Persist facts to session-context.md under the auto-persisted section.

    Aggregate facts (PRs, branches, files, tasks) are merged into existing lines.
    Decision facts are appended if new. Returns list of changed/added fact lines.
    """
    path = Path(memory_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = _content_with_section(path).split("\n")
    section_start, section_end = _section_bounds(lines)
    entries = _section_entries(lines, section_start, section_end)

    changed_lines: list[str] = []
    section_end, entries = _merge_aggregates(facts, lines, entries, section_end, changed_lines)
    _append_decisions(facts, lines, entries, section_end, changed_lines)

    if not changed_lines:
        return []

    path.write_text("\n".join(lines), encoding="utf-8")
    return changed_lines


# --- Heuristic ---


def should_persist(message_count: int, threshold: int = 50) -> bool:
    """Determine if facts should be persisted based on message count heuristic.

    Persists every `threshold` messages (default 50).
    """
    if message_count <= 0:
        return False
    return message_count % threshold == 0 or message_count >= threshold


# --- Session Discovery ---


def _find_latest_session(project_path: str | None = None) -> str | None:
    """Find the most recent non-agent session JSONL file."""
    if project_path:
        encoded = project_path.replace("/", "-").replace(".", "-")
    else:
        cwd = os.getcwd()
        encoded = cwd.replace("/", "-").replace(".", "-")

    proj_dir = CLAUDE_DIR / "projects" / encoded
    if not proj_dir.exists():
        return None

    jsonl_files = sorted(
        proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    # Exclude agent sessions
    jsonl_files = [f for f in jsonl_files if not f.name.startswith("agent-")]

    return str(jsonl_files[0]) if jsonl_files else None


def _get_memory_path(project_path: str | None = None) -> str:
    """Get the session-context.md path for the current project."""
    if project_path:
        encoded = project_path.replace("/", "-").replace(".", "-")
    else:
        cwd = os.getcwd()
        encoded = cwd.replace("/", "-").replace(".", "-")

    return str(CLAUDE_DIR / "rules" / "session-context.md")


# --- CLI ---


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-compaction fact extraction and persistence"
    )
    parser.add_argument(
        "--force", action="store_true", help="Persist regardless of heuristic"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be persisted without writing",
    )
    parser.add_argument("--session", type=str, help="Path to specific session JSONL")
    parser.add_argument("--project", type=str, help="Project path for session discovery")
    args = parser.parse_args()

    # Find session file
    session_file = args.session or _find_latest_session(args.project)
    if not session_file:
        print("[compaction-persist] No session file found", file=sys.stderr)
        return 1

    if not Path(session_file).exists():
        print(f"[compaction-persist] Session file not found: {session_file}", file=sys.stderr)
        return 1

    # Extract facts
    facts = extract_session_facts(session_file)

    # Check heuristic
    if not args.force and not args.dry_run:
        if not should_persist(facts["message_count"]):
            print(
                f"[compaction-persist] {facts['message_count']} messages, "
                f"threshold not met. Use --force to override."
            )
            return 0

    # Report
    total = (
        len(facts["pr_numbers"])
        + len(facts["branches"])
        + len(facts["file_paths"])
        + len(facts["task_ids"])
        + len(facts["decisions"])
    )
    print(f"[compaction-persist] Extracted {total} facts from {facts['message_count']} messages")

    if facts["pr_numbers"]:
        print(f"  PRs: {', '.join(facts['pr_numbers'])}")
    if facts["branches"]:
        print(f"  Branches: {', '.join(facts['branches'])}")
    if facts["file_paths"]:
        print(f"  Files: {len(facts['file_paths'])} paths")
    if facts["task_ids"]:
        print(f"  Tasks: {', '.join(facts['task_ids'])}")
    if facts["decisions"]:
        print(f"  Decisions: {len(facts['decisions'])}")

    if args.dry_run:
        print("[compaction-persist] Dry run -- no changes written")
        return 0

    # Persist
    memory_path = _get_memory_path(args.project)
    new_facts = persist_facts(facts, memory_path)

    if new_facts:
        print(f"[compaction-persist] Persisted {len(new_facts)} new facts to {memory_path}")
    else:
        print("[compaction-persist] No new facts to persist (all already present)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
