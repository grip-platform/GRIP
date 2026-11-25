#!/usr/bin/env python3
"""
Compaction Memory Guard -- persists key facts before context window compression.

When Claude Code compresses its context window (compaction), key facts like
session IDs, file paths, decision rationale, and entity names can be lost.
This module extracts those facts from conversation text and persists them
to a JSONL log so they survive across compactions.

Usage:
    from lib.compaction_guard import extract_key_facts, persist_facts, deduplicate

    facts = extract_key_facts(conversation_text)
    facts = deduplicate(facts, log_path)
    persist_facts(facts, memory_dir)
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fact:
    """A single extractable fact from conversation text."""

    category: str          # e.g. "session_id", "file_path", "git_sha", ...
    value: str             # the extracted value
    source_line: str       # the line it was extracted from (trimmed)
    confidence: float      # 0.0-1.0 heuristic confidence

    def to_dict(self) -> dict:
        """Serialize for JSONL storage, adding timestamp."""
        d = asdict(self)
        d["timestamp"] = datetime.now(timezone.utc).isoformat()
        return d

    def dedup_key(self) -> tuple:
        """Identity key for deduplication (category + normalized value)."""
        return (self.category, self.value.strip().lower())


# ---------------------------------------------------------------------------
# Extraction patterns
# ---------------------------------------------------------------------------

# UUID v4 pattern (session IDs, instance IDs)
_UUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)

# Git SHA (7-40 hex chars, word-bounded, but not pure-numeric or short hex
# that could be a color code)
_GIT_SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")

# File paths: absolute Unix/Windows or relative with extension
_FILE_PATH_RE = re.compile(
    r"(?:"
    r"(?:/[a-zA-Z0-9_.@-]+){2,}"           # Unix absolute: /foo/bar/baz
    r"|(?:[A-Z]:\\[^\s:*?\"<>|]+)"          # Windows absolute: C:\foo\bar
    r"|(?:\$HOME/[^\s]+)"                   # $HOME-relative
    r"|(?:~/[^\s]+)"                        # tilde-relative
    r"|(?:(?:\./|\.\./)[\w./-]+)"           # explicit relative: ./foo ../bar
    r"|(?:[\w.-]+/[\w./-]*\.\w{1,10})"     # implicit relative with extension
    r")"
)

# Branch names: feat/xxx, fix/xxx, chore/xxx etc. or main/master/develop
_BRANCH_RE = re.compile(
    r"\b((?:feat|fix|chore|docs|test|refactor|perf|style|hotfix)/[\w./-]+)\b"
    r"|\bbranch[:\s]+[`'\"]?([\w./-]+)[`'\"]?"
)

# PR/Issue references: #123, PR #123, issue #123, GH-123
_PR_ISSUE_RE = re.compile(
    r"(?:PR|pr|Issue|issue|#)\s*#?(\d{1,6})\b"
    r"|\bGH-(\d{1,6})\b"
)

# Decision rationale: sentences containing decision keywords
_DECISION_KEYWORDS = re.compile(
    r"\b(because|decided|chose|chosen|rationale|reason(?:ing)?|trade-?off|opted)\b",
    re.IGNORECASE,
)

# Person signatures: X>> pattern (GRIP convention)
_SIGNATURE_RE = re.compile(r"\b([A-Z]{1,3})>>(?=\s|$)")

# Numeric metrics: percentages, scores, counts with labels
_METRIC_RE = re.compile(
    r"(?:"
    r"(\d+(?:\.\d+)?)\s*%"                                     # 95.5%
    r"|(?:score|accuracy|coverage|rate|confidence)[:\s]+(\d+(?:\.\d+)?(?:/\d+)?)"  # score: 85/100
    r"|(\d+(?:\.\d+)?)\s*(?:out of|/)\s*(\d+)"                # 85 out of 100
    r")",
    re.IGNORECASE,
)

# Generation references: Gen 123, Generation 123
_GEN_RE = re.compile(r"\bGen(?:eration)?\s+(\d{1,4})\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Extraction engine
# ---------------------------------------------------------------------------

def _extract_uuids(line: str) -> list[Fact]:
    """Extract UUID-shaped session/instance IDs."""
    facts = []
    for m in _UUID_RE.finditer(line):
        facts.append(Fact(
            category="session_id",
            value=m.group(1),
            source_line=line.strip()[:200],
            confidence=0.95,
        ))
    return facts


def _extract_git_shas(line: str) -> list[Fact]:
    """Extract git commit SHAs (7-40 lowercase hex)."""
    facts = []
    for m in _GIT_SHA_RE.finditer(line):
        sha = m.group(1)
        # Skip if it looks like a UUID fragment (handled separately)
        if _UUID_RE.search(line) and sha in _UUID_RE.search(line).group(0):
            continue
        # Must contain at least one letter (pure digits aren't SHAs)
        if not re.search(r"[a-f]", sha):
            continue
        # Skip very short matches that could be hex color codes
        if len(sha) < 7:
            continue
        facts.append(Fact(
            category="git_sha",
            value=sha,
            source_line=line.strip()[:200],
            confidence=0.85 if len(sha) >= 10 else 0.70,
        ))
    return facts


def _extract_file_paths(line: str) -> list[Fact]:
    """Extract file paths (absolute, relative, $HOME-prefixed)."""
    facts = []
    for m in _FILE_PATH_RE.finditer(line):
        path_str = m.group(0).rstrip(".,;:)\"'`")
        # Skip very short matches
        if len(path_str) < 4:
            continue
        facts.append(Fact(
            category="file_path",
            value=path_str,
            source_line=line.strip()[:200],
            confidence=0.90,
        ))
    return facts


def _extract_branches(line: str) -> list[Fact]:
    """Extract git branch names."""
    facts = []
    for m in _BRANCH_RE.finditer(line):
        branch = m.group(1) or m.group(2)
        if branch:
            facts.append(Fact(
                category="branch",
                value=branch,
                source_line=line.strip()[:200],
                confidence=0.90,
            ))
    return facts


def _extract_pr_issues(line: str) -> list[Fact]:
    """Extract PR/issue number references."""
    facts = []
    for m in _PR_ISSUE_RE.finditer(line):
        number = m.group(1) or m.group(2)
        if number:
            facts.append(Fact(
                category="pr_issue",
                value=f"#{number}",
                source_line=line.strip()[:200],
                confidence=0.85,
            ))
    return facts


def _extract_decisions(line: str) -> list[Fact]:
    """Extract decision rationale sentences."""
    if not _DECISION_KEYWORDS.search(line):
        return []
    # Only extract if the line is substantial (not just a keyword mention)
    stripped = line.strip()
    if len(stripped) < 20:
        return []
    return [Fact(
        category="decision",
        value=stripped[:300],
        source_line=stripped[:200],
        confidence=0.75,
    )]


def _extract_signatures(line: str) -> list[Fact]:
    """Extract person signatures (X>> GRIP convention)."""
    facts = []
    for m in _SIGNATURE_RE.finditer(line):
        facts.append(Fact(
            category="signature",
            value=m.group(1),
            source_line=line.strip()[:200],
            confidence=0.95,
        ))
    return facts


def _extract_metrics(line: str) -> list[Fact]:
    """Extract numeric metrics (scores, percentages, counts)."""
    facts = []
    for m in _METRIC_RE.finditer(line):
        # Build a readable metric value from whichever group matched
        if m.group(1):
            val = f"{m.group(1)}%"
        elif m.group(2):
            val = m.group(2)
        elif m.group(3) and m.group(4):
            val = f"{m.group(3)}/{m.group(4)}"
        else:
            continue
        facts.append(Fact(
            category="metric",
            value=val,
            source_line=line.strip()[:200],
            confidence=0.80,
        ))
    return facts


def _extract_generations(line: str) -> list[Fact]:
    """Extract GRIP generation references."""
    facts = []
    for m in _GEN_RE.finditer(line):
        facts.append(Fact(
            category="generation",
            value=f"Gen {m.group(1)}",
            source_line=line.strip()[:200],
            confidence=0.90,
        ))
    return facts


# Ordered list of all extractors
_EXTRACTORS = [
    _extract_uuids,
    _extract_git_shas,
    _extract_file_paths,
    _extract_branches,
    _extract_pr_issues,
    _extract_decisions,
    _extract_signatures,
    _extract_metrics,
    _extract_generations,
]


def extract_key_facts(text: str) -> list[Fact]:
    """
    Extract all key facts from conversation text.

    Scans each line through all extractors and returns a deduplicated
    list of Fact objects.

    Args:
        text: Raw conversation text (multi-line string)

    Returns:
        List of extracted Fact objects, deduplicated within the input.
    """
    if not text or not text.strip():
        return []

    facts: list[Fact] = []
    seen: set[tuple] = set()

    for line in text.splitlines():
        if not line.strip():
            continue
        for extractor in _EXTRACTORS:
            for fact in extractor(line):
                key = fact.dedup_key()
                if key not in seen:
                    seen.add(key)
                    facts.append(fact)

    return facts


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _get_memory_dir() -> Path:
    """Resolve the memory directory from environment or default."""
    claude_dir = os.environ.get("CLAUDE_DIR", os.path.join(os.environ.get("HOME", ""), ".claude"))
    return Path(claude_dir) / "memory"


def _get_log_path(memory_dir: Optional[Path] = None) -> Path:
    """Resolve the compaction log path."""
    if memory_dir is None:
        memory_dir = _get_memory_dir()
    return Path(memory_dir) / "compaction-log.jsonl"


def persist_facts(facts: list[Fact], memory_dir: Optional[Path] = None) -> Path:
    """
    Append facts to the compaction log (JSONL format).

    Args:
        facts: List of Fact objects to persist
        memory_dir: Directory for memory files (default: $CLAUDE_DIR/memory)

    Returns:
        Path to the log file written to
    """
    if not facts:
        log_path = _get_log_path(memory_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    log_path = _get_log_path(memory_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "a", encoding="utf-8") as f:
        for fact in facts:
            f.write(json.dumps(fact.to_dict(), ensure_ascii=False) + "\n")

    return log_path


def load_existing_facts(log_path: Path) -> list[dict]:
    """
    Load existing facts from a JSONL log file.

    Args:
        log_path: Path to the compaction-log.jsonl file

    Returns:
        List of fact dicts from the log
    """
    if not log_path.exists():
        return []

    facts = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                facts.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # Skip malformed lines gracefully
    return facts


def deduplicate(facts: list[Fact], existing_log: Optional[Path] = None) -> list[Fact]:
    """
    Remove facts that already exist in the persisted log.

    Args:
        facts: Newly extracted facts
        existing_log: Path to the existing JSONL log file

    Returns:
        Facts not yet present in the log
    """
    if existing_log is None:
        existing_log = _get_log_path()

    existing = load_existing_facts(existing_log)
    existing_keys: set[tuple] = set()
    for entry in existing:
        cat = entry.get("category", "")
        val = entry.get("value", "").strip().lower()
        existing_keys.add((cat, val))

    return [f for f in facts if f.dedup_key() not in existing_keys]


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    "session_id": "Session IDs",
    "file_path": "Files",
    "git_sha": "Git commits",
    "branch": "Branches",
    "pr_issue": "PRs/Issues",
    "decision": "Decisions",
    "signature": "People",
    "metric": "Metrics",
    "generation": "Generations",
}


def summarize_session(facts: list[Fact]) -> str:
    """
    Generate a one-paragraph session summary from extracted facts.

    Groups facts by category and produces a human-readable summary
    suitable for appending to session context.

    Args:
        facts: List of extracted Fact objects

    Returns:
        Summary string (empty string if no facts)
    """
    if not facts:
        return ""

    # Group by category
    groups: dict[str, list[str]] = {}
    for fact in facts:
        groups.setdefault(fact.category, []).append(fact.value)

    parts = []

    # People first
    if "signature" in groups:
        names = sorted(set(groups["signature"]))
        parts.append(f"Participants: {', '.join(n + '>>' for n in names)}")

    # Session IDs
    if "session_id" in groups:
        ids = groups["session_id"]
        if len(ids) == 1:
            parts.append(f"Session: {ids[0][:8]}...")
        else:
            parts.append(f"{len(ids)} session IDs tracked")

    # Generations
    if "generation" in groups:
        gens = sorted(set(groups["generation"]))
        parts.append(f"Generations: {', '.join(gens)}")

    # Branches
    if "branch" in groups:
        branches = sorted(set(groups["branch"]))
        parts.append(f"Branches: {', '.join(branches[:5])}")

    # PRs/Issues
    if "pr_issue" in groups:
        refs = sorted(set(groups["pr_issue"]))
        parts.append(f"References: {', '.join(refs[:10])}")

    # Files (count only, too verbose otherwise)
    if "file_path" in groups:
        count = len(set(groups["file_path"]))
        parts.append(f"{count} file(s) referenced")

    # Git SHAs (count + first few)
    if "git_sha" in groups:
        shas = sorted(set(groups["git_sha"]))
        if len(shas) <= 3:
            parts.append(f"Commits: {', '.join(s[:7] for s in shas)}")
        else:
            parts.append(f"{len(shas)} commits referenced")

    # Metrics
    if "metric" in groups:
        metrics = groups["metric"][:5]
        parts.append(f"Metrics: {', '.join(metrics)}")

    # Decisions (count)
    if "decision" in groups:
        count = len(groups["decision"])
        parts.append(f"{count} decision(s) recorded")

    return ". ".join(parts) + "." if parts else ""


# ---------------------------------------------------------------------------
# CLI entry point (for hook integration)
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point: reads stdin, extracts facts, persists them."""
    import sys

    text = sys.stdin.read()
    if not text.strip():
        print("compaction_guard: no input", file=sys.stderr)
        sys.exit(0)

    facts = extract_key_facts(text)
    log_path = _get_log_path()
    facts = deduplicate(facts, log_path)

    if not facts:
        print("compaction_guard: no new facts to persist")
        sys.exit(0)

    persist_facts(facts)
    summary = summarize_session(facts)
    print(f"compaction_guard: persisted {len(facts)} fact(s)")
    if summary:
        print(f"  Summary: {summary}")


if __name__ == "__main__":
    main()
