#!/usr/bin/env python3
"""
Rule Conflict Detector for GRIP.

Detects conflicts, redundancies, and hierarchy violations across GRIP rule files
(CLAUDE.md, .claude/CLAUDE.md, rules/*.md).

Usage:
    python3 rule_conflict_detector.py [--json] [--threshold FLOAT] [--verbose] [--path DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Stop-words (common English words that add noise to similarity scoring)
# ---------------------------------------------------------------------------

STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "must", "need", "dare",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "about", "up", "it", "its", "this", "that", "these", "those",
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you",
    "your", "yours", "yourself", "yourselves", "he", "him", "his", "himself",
    "she", "her", "hers", "herself", "they", "them", "their", "theirs",
    "themselves", "what", "which", "who", "whom", "if", "or", "and", "but",
    "because", "until", "while", "any", "also", "e", "g", "eg", "ie",
    "etc", "see", "use", "using", "used",
}

# Contradiction signal pairs: if rule A has word[0] and rule B has word[1]
# on similar topics, that is a potential conflict.
CONTRADICTION_PAIRS: list[tuple[str, str]] = [
    ("never", "always"),
    ("must", "must not"),
    ("must", "never"),
    ("don't", "do"),
    ("dont", "do"),
    ("avoid", "prefer"),
    ("avoid", "use"),
    ("disable", "enable"),
    ("forbidden", "required"),
    ("prohibited", "allowed"),
    ("block", "allow"),
    ("reject", "accept"),
    ("skip", "require"),
    ("optional", "mandatory"),
    ("optional", "required"),
    ("ignore", "enforce"),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    """A single extracted rule."""
    source_file: str
    heading: str
    content: str
    keywords: set[str] = field(default_factory=set)
    line_number: int = 0

    def __post_init__(self) -> None:
        if not self.keywords:
            self.keywords = tokenize(self.content)

    @property
    def id(self) -> str:
        """Short identifier for display."""
        stem = Path(self.source_file).name
        short_heading = self.heading[:60] if self.heading else "(no heading)"
        return f"{stem}:{short_heading}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["keywords"] = sorted(self.keywords)
        d["id"] = self.id
        return d


@dataclass
class RulePair:
    """A pair of rules with similarity metrics."""
    rule_a: Rule
    rule_b: Rule
    similarity: float
    pair_type: str  # "conflict", "redundancy", "hierarchy_violation"
    severity: str   # "HIGH", "MEDIUM", "LOW"
    reason: str = ""
    contradictions: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_a": self.rule_a.id,
            "rule_b": self.rule_b.id,
            "rule_a_heading": self.rule_a.heading,
            "rule_b_heading": self.rule_b.heading,
            "rule_a_source": self.rule_a.source_file,
            "rule_b_source": self.rule_b.source_file,
            "similarity": round(self.similarity, 4),
            "pair_type": self.pair_type,
            "severity": self.severity,
            "reason": self.reason,
            "contradictions": self.contradictions,
        }


@dataclass
class TopicCluster:
    """A group of rules sharing a common topic."""
    topic_keywords: list[str]
    rules: list[Rule]

    def to_dict(self) -> dict:
        return {
            "topic_keywords": self.topic_keywords,
            "rule_count": len(self.rules),
            "rules": [r.id for r in self.rules],
        }


@dataclass
class ConflictReport:
    """Full analysis report."""
    total_rules: int
    total_files: int
    conflicts: list[RulePair]
    redundancies: list[RulePair]
    hierarchy_violations: list[RulePair]
    topic_clusters: list[TopicCluster]
    threshold: float

    def to_dict(self) -> dict:
        return {
            "summary": {
                "total_rules": self.total_rules,
                "total_files": self.total_files,
                "conflicts": len(self.conflicts),
                "redundancies": len(self.redundancies),
                "hierarchy_violations": len(self.hierarchy_violations),
                "topic_clusters": len(self.topic_clusters),
                "threshold": self.threshold,
            },
            "conflicts": [p.to_dict() for p in self.conflicts],
            "redundancies": [p.to_dict() for p in self.redundancies],
            "hierarchy_violations": [p.to_dict() for p in self.hierarchy_violations],
            "topic_clusters": [c.to_dict() for c in self.topic_clusters],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_text(self, verbose: bool = False) -> str:
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append("GRIP Rule Conflict Report")
        lines.append("=" * 70)
        lines.append(f"Rules scanned: {self.total_rules}")
        lines.append(f"Files scanned: {self.total_files}")
        lines.append(f"Similarity threshold: {self.threshold}")
        lines.append("")

        if self.conflicts:
            lines.append(f"--- CONFLICTS ({len(self.conflicts)}) ---")
            for p in self.conflicts:
                lines.append(f"  [{p.severity}] {p.rule_a.id}")
                lines.append(f"       vs {p.rule_b.id}")
                lines.append(f"       Similarity: {p.similarity:.4f}")
                lines.append(f"       Contradictions: {p.contradictions}")
                lines.append(f"       Reason: {p.reason}")
                if verbose:
                    lines.append(f"       A content: {p.rule_a.content[:120]}...")
                    lines.append(f"       B content: {p.rule_b.content[:120]}...")
                lines.append("")
        else:
            lines.append("--- CONFLICTS: None ---")
            lines.append("")

        if self.redundancies:
            lines.append(f"--- REDUNDANCIES ({len(self.redundancies)}) ---")
            for p in self.redundancies:
                lines.append(f"  [{p.severity}] {p.rule_a.id}")
                lines.append(f"       vs {p.rule_b.id}")
                lines.append(f"       Similarity: {p.similarity:.4f}")
                lines.append(f"       Reason: {p.reason}")
                if verbose:
                    lines.append(f"       A content: {p.rule_a.content[:120]}...")
                    lines.append(f"       B content: {p.rule_b.content[:120]}...")
                lines.append("")
        else:
            lines.append("--- REDUNDANCIES: None ---")
            lines.append("")

        if self.hierarchy_violations:
            lines.append(
                f"--- HIERARCHY VIOLATIONS ({len(self.hierarchy_violations)}) ---"
            )
            for p in self.hierarchy_violations:
                lines.append(f"  [{p.severity}] {p.rule_a.id}")
                lines.append(f"       vs {p.rule_b.id}")
                lines.append(f"       Similarity: {p.similarity:.4f}")
                lines.append(f"       Reason: {p.reason}")
                lines.append("")
        else:
            lines.append("--- HIERARCHY VIOLATIONS: None ---")
            lines.append("")

        if self.topic_clusters:
            lines.append(f"--- TOPIC CLUSTERS ({len(self.topic_clusters)}) ---")
            for c in self.topic_clusters:
                lines.append(
                    f"  Topic: {', '.join(c.topic_keywords[:5])} "
                    f"({len(c.rules)} rules)"
                )
                for r in c.rules:
                    lines.append(f"    - {r.id}")
                lines.append("")
        else:
            lines.append("--- TOPIC CLUSTERS: None ---")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9_]+(?:'[a-z]+)?")


def tokenize(text: str) -> set[str]:
    """Normalize text into a set of keyword tokens."""
    lowered = text.lower()
    tokens = set(_TOKEN_RE.findall(lowered))
    # Keep negation-bearing tokens even if they are in stopwords
    negation_tokens = {"not", "never", "don't", "dont", "no", "must"}
    filtered = {t for t in tokens if t not in STOPWORDS or t in negation_tokens}
    return filtered


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Compute Jaccard index between two keyword sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Rule extraction from markdown
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def extract_rules_from_markdown(text: str, source_file: str) -> list[Rule]:
    """Split markdown into rules by heading boundaries.

    Each heading starts a new rule that extends to the next heading of equal
    or higher level, or end of file.
    """
    if not text.strip():
        return []

    headings = list(_HEADING_RE.finditer(text))
    if not headings:
        # No headings: treat entire content as one rule
        content = text.strip()
        if content:
            return [Rule(
                source_file=source_file,
                heading="(document)",
                content=content,
                line_number=1,
            )]
        return []

    rules: list[Rule] = []

    # Content before first heading (preamble)
    preamble = text[: headings[0].start()].strip()
    if preamble and len(preamble) > 20:
        rules.append(Rule(
            source_file=source_file,
            heading="(preamble)",
            content=preamble,
            line_number=1,
        ))

    for i, match in enumerate(headings):
        level = len(match.group(1))
        heading_text = match.group(2).strip()
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        content = text[start:end].strip()

        # Skip headings with trivial content (less than 20 chars, likely
        # a table of contents entry or just a separator)
        if len(content) < 20:
            continue

        line_number = text[:match.start()].count("\n") + 1
        rules.append(Rule(
            source_file=source_file,
            heading=heading_text,
            content=content,
            line_number=line_number,
        ))

    return rules


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_rule_files(base_path: Path) -> list[Path]:
    """Find all GRIP rule files under the given path."""
    files: list[Path] = []

    # Top-level CLAUDE.md
    top_claude = base_path / "CLAUDE.md"
    if top_claude.is_file():
        files.append(top_claude)

    # .claude/CLAUDE.md (project-level)
    project_claude = base_path / ".claude" / "CLAUDE.md"
    if project_claude.is_file():
        files.append(project_claude)

    # rules/*.md
    rules_dir = base_path / "rules"
    if rules_dir.is_dir():
        for md in sorted(rules_dir.glob("*.md")):
            if md.is_file():
                files.append(md)

    return files


def load_rules(base_path: Path) -> list[Rule]:
    """Load and extract rules from all discovered files."""
    files = discover_rule_files(base_path)
    all_rules: list[Rule] = []
    for filepath in files:
        text = filepath.read_text(encoding="utf-8", errors="replace")
        extracted = extract_rules_from_markdown(text, str(filepath))
        all_rules.extend(extracted)
    return all_rules


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

def find_contradictions(
    kw_a: set[str], kw_b: set[str]
) -> list[tuple[str, str]]:
    """Find contradiction signal pairs between two keyword sets.

    A contradiction exists when one rule contains a word from a
    contradiction pair and the other rule contains its opposite.
    """
    found: list[tuple[str, str]] = []
    for word_pos, word_neg in CONTRADICTION_PAIRS:
        # Normalise multi-word signals
        parts_pos = set(word_pos.split())
        parts_neg = set(word_neg.split())

        a_has_pos = parts_pos <= kw_a
        a_has_neg = parts_neg <= kw_a
        b_has_pos = parts_pos <= kw_b
        b_has_neg = parts_neg <= kw_b

        if (a_has_pos and b_has_neg) or (a_has_neg and b_has_pos):
            found.append((word_pos, word_neg))
    return found


# ---------------------------------------------------------------------------
# Hierarchy analysis
# ---------------------------------------------------------------------------

_HIERARCHY_LEVELS: dict[str, int] = {
    "CLAUDE.md": 0,       # global (lowest precedence)
    "rules": 1,           # rules dir
    ".claude": 2,         # project-level (highest precedence)
}

OVERRIDE_KEYWORDS = {"never", "don't", "dont", "must", "not", "always", "mandatory"}


def get_hierarchy_level(source_file: str) -> int:
    """Determine the hierarchy level of a rule source."""
    if "/.claude/CLAUDE.md" in source_file or "\\.claude\\CLAUDE.md" in source_file:
        return 2
    if "/rules/" in source_file or "\\rules\\" in source_file:
        return 1
    return 0


def detect_hierarchy_violations(
    rules: list[Rule], threshold: float
) -> list[RulePair]:
    """Find cases where a lower-precedence rule uses strong override language
    that may conflict with a higher-precedence rule on the same topic."""
    violations: list[RulePair] = []
    n = len(rules)

    for i in range(n):
        for j in range(i + 1, n):
            ra, rb = rules[i], rules[j]
            level_a = get_hierarchy_level(ra.source_file)
            level_b = get_hierarchy_level(rb.source_file)

            # Only interesting when rules are at different hierarchy levels
            if level_a == level_b:
                continue

            sim = jaccard_similarity(ra.keywords, rb.keywords)
            if sim < threshold:
                continue

            # Check if the lower-precedence rule uses override language
            lower, higher = (ra, rb) if level_a < level_b else (rb, ra)
            lower_override = lower.keywords & OVERRIDE_KEYWORDS
            higher_override = higher.keywords & OVERRIDE_KEYWORDS

            if lower_override and higher_override:
                # Both use strong language on the same topic — potential conflict
                contradictions = find_contradictions(
                    lower.keywords, higher.keywords
                )
                if contradictions:
                    severity = "HIGH" if sim > 0.7 else "MEDIUM"
                    violations.append(RulePair(
                        rule_a=lower,
                        rule_b=higher,
                        similarity=sim,
                        pair_type="hierarchy_violation",
                        severity=severity,
                        reason=(
                            f"Lower-precedence rule ({lower.id}) uses override "
                            f"language that contradicts higher-precedence rule "
                            f"({higher.id}) on same topic"
                        ),
                        contradictions=contradictions,
                    ))

    return violations


# ---------------------------------------------------------------------------
# Topic clustering
# ---------------------------------------------------------------------------

def cluster_by_topic(
    rules: list[Rule], min_shared: int = 3, min_cluster_size: int = 2
) -> list[TopicCluster]:
    """Group rules that share significant keyword overlap."""
    if len(rules) < 2:
        return []

    # Build adjacency: rules that share >= min_shared non-trivial keywords
    clusters: list[set[int]] = []
    n = len(rules)

    for i in range(n):
        for j in range(i + 1, n):
            shared = rules[i].keywords & rules[j].keywords
            if len(shared) >= min_shared:
                # Try to merge into existing cluster
                merged = False
                for cluster in clusters:
                    if i in cluster or j in cluster:
                        cluster.add(i)
                        cluster.add(j)
                        merged = True
                        break
                if not merged:
                    clusters.append({i, j})

    # Merge overlapping clusters
    changed = True
    while changed:
        changed = False
        new_clusters: list[set[int]] = []
        for c in clusters:
            merged_into = None
            for nc in new_clusters:
                if c & nc:
                    nc |= c
                    merged_into = nc
                    changed = True
                    break
            if merged_into is None:
                new_clusters.append(set(c))
        clusters = new_clusters

    # Build TopicCluster objects
    result: list[TopicCluster] = []
    for cluster_indices in clusters:
        if len(cluster_indices) < min_cluster_size:
            continue
        cluster_rules = [rules[idx] for idx in sorted(cluster_indices)]
        # Find shared keywords across the cluster
        shared_kw = set.intersection(*(r.keywords for r in cluster_rules))
        topic_kw = sorted(shared_kw - STOPWORDS)[:10]
        if topic_kw:
            result.append(TopicCluster(
                topic_keywords=topic_kw,
                rules=cluster_rules,
            ))

    # Sort by cluster size descending
    result.sort(key=lambda c: len(c.rules), reverse=True)
    return result


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze(
    rules: list[Rule],
    threshold: float = 0.6,
    redundancy_threshold: float = 0.8,
) -> ConflictReport:
    """Run full analysis on a list of rules."""
    conflicts: list[RulePair] = []
    redundancies: list[RulePair] = []
    source_files = {r.source_file for r in rules}
    n = len(rules)

    for i in range(n):
        for j in range(i + 1, n):
            ra, rb = rules[i], rules[j]
            sim = jaccard_similarity(ra.keywords, rb.keywords)

            if sim >= redundancy_threshold:
                # High similarity = redundancy (DRY violation)
                redundancies.append(RulePair(
                    rule_a=ra,
                    rule_b=rb,
                    similarity=sim,
                    pair_type="redundancy",
                    severity="HIGH" if sim > 0.9 else "MEDIUM",
                    reason=(
                        f"Rules share {sim:.0%} keyword overlap "
                        f"(DRY violation)"
                    ),
                ))
            elif sim >= threshold:
                # Check for contradictions
                contradictions = find_contradictions(ra.keywords, rb.keywords)
                if contradictions:
                    severity = "HIGH" if sim > 0.7 else "MEDIUM"
                    conflicts.append(RulePair(
                        rule_a=ra,
                        rule_b=rb,
                        similarity=sim,
                        pair_type="conflict",
                        severity=severity,
                        reason=(
                            f"Rules discuss similar topic (similarity={sim:.2f}) "
                            f"but contain contradictory signals: {contradictions}"
                        ),
                        contradictions=contradictions,
                    ))

    hierarchy_violations = detect_hierarchy_violations(rules, threshold)
    topic_clusters = cluster_by_topic(rules)

    # Sort by severity then similarity
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for lst in (conflicts, redundancies, hierarchy_violations):
        lst.sort(key=lambda p: (severity_order.get(p.severity, 3), -p.similarity))

    return ConflictReport(
        total_rules=n,
        total_files=len(source_files),
        conflicts=conflicts,
        redundancies=redundancies,
        hierarchy_violations=hierarchy_violations,
        topic_clusters=topic_clusters,
        threshold=threshold,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect conflicts and redundancies in GRIP rule files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output report as JSON",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="Similarity threshold for conflict detection (default: 0.6)",
    )
    parser.add_argument(
        "--redundancy-threshold",
        type=float,
        default=0.8,
        help="Similarity threshold for redundancy detection (default: 0.8)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Show rule content excerpts in text output",
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Base directory to scan (default: $HOME/.claude)",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    base_path = Path(args.path) if args.path else Path.home() / ".claude"
    if not base_path.is_dir():
        print(f"Error: directory not found: {base_path}", file=sys.stderr)
        return 1

    rules = load_rules(base_path)
    if not rules:
        print(f"No rules found under {base_path}", file=sys.stderr)
        return 1

    report = analyze(
        rules,
        threshold=args.threshold,
        redundancy_threshold=args.redundancy_threshold,
    )

    if args.json:
        print(report.to_json())
    else:
        print(report.to_text(verbose=args.verbose))

    return 0


if __name__ == "__main__":
    sys.exit(main())
