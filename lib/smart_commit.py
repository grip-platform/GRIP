"""Smart commit message generator that analyzes git diffs and generates conventional commit messages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DiffHunk:
    file_path: str
    additions: int
    deletions: int
    changed_lines: list[str]
    file_type: str

    @property
    def is_binary(self) -> bool:
        return any(
            "Binary file" in line or "GIT binary patch" in line
            for line in self.changed_lines
        )

    @property
    def is_rename(self) -> bool:
        return any(
            line.startswith("rename from") or line.startswith("rename to")
            for line in self.changed_lines
        )

    @property
    def total_changes(self) -> int:
        return self.additions + self.deletions


@dataclass
class CommitSuggestion:
    type: str
    scope: str
    subject: str
    body: str
    breaking: bool
    confidence: float

    def format(self) -> str:
        return format_commit_message(self)


@dataclass
class CommitAnalysis:
    hunks: list[DiffHunk] = field(default_factory=list)
    suggestions: list[CommitSuggestion] = field(default_factory=list)
    files_changed: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    primary_language: str = ""
    change_categories: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Extension / language mapping
# ---------------------------------------------------------------------------

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    "py": "python",
    "ts": "typescript",
    "tsx": "typescript",
    "js": "javascript",
    "jsx": "javascript",
    "rs": "rust",
    "go": "go",
    "java": "java",
    "kt": "kotlin",
    "rb": "ruby",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "md": "markdown",
    "yml": "yaml",
    "yaml": "yaml",
    "json": "json",
    "toml": "toml",
    "css": "css",
    "scss": "css",
    "html": "html",
    "sql": "sql",
    "c": "c",
    "cpp": "cpp",
    "h": "c",
    "hpp": "cpp",
    "swift": "swift",
    "dockerfile": "docker",
}


# ---------------------------------------------------------------------------
# Classification patterns
# ---------------------------------------------------------------------------

PATH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(^|/)tests?/", re.IGNORECASE), "test"),
    (re.compile(r"(^|/)__tests__/", re.IGNORECASE), "test"),
    (re.compile(r"(^|/)spec/", re.IGNORECASE), "test"),
    (re.compile(r"\.test\.[a-z]+$", re.IGNORECASE), "test"),
    (re.compile(r"\.spec\.[a-z]+$", re.IGNORECASE), "test"),
    (re.compile(r"test_[^/]+\.[a-z]+$", re.IGNORECASE), "test"),
    (re.compile(r"(^|/)docs?/", re.IGNORECASE), "docs"),
    (re.compile(r"\.md$", re.IGNORECASE), "docs"),
    (re.compile(r"(^|/)\.github/workflows/", re.IGNORECASE), "chore"),
    (re.compile(r"(^|/)(Makefile|Dockerfile|docker-compose)", re.IGNORECASE), "chore"),
    (re.compile(r"(^|/)\.(eslint|prettier|babel|editorconfig)", re.IGNORECASE), "chore"),
    (
        re.compile(
            r"(^|/)(package\.json|requirements\.txt|Cargo\.toml|go\.mod)",
            re.IGNORECASE,
        ),
        "chore",
    ),
    (re.compile(r"(^|/)\.gitignore$", re.IGNORECASE), "chore"),
]

FEAT_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"^\+\s*(def|class|function|const|let|var|export|pub fn|func)\s+\w+",
        re.MULTILINE,
    ),
    re.compile(
        r"^\+.*new\s+(feature|endpoint|route|api|component|module)",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^\+\s*@(app\.|router\.|api_router\.)(get|post|put|delete|patch)",
        re.MULTILINE,
    ),
]

FIX_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(bug|fix|error|issue|crash|wrong|broken|patch|resolve|correct)",
        re.IGNORECASE,
    ),
    re.compile(r"^\+\s*(try|except|catch|if\s+err|if\s+error)", re.MULTILINE),
    re.compile(r"^\-.*raise\s+\w+Error", re.MULTILINE),
]

REFACTOR_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\b(rename|move|extract|restructure|reorganize|simplify|clean\s*up)\b",
        re.IGNORECASE,
    ),
    re.compile(r"rename\s+from", re.IGNORECASE),
]

PERF_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(cache|memoize|optimize|speed|perf|fast|lazy|batch|pool|index|async)",
        re.IGNORECASE,
    ),
    re.compile(r"(O\(n\)|O\(1\)|O\(log)", re.IGNORECASE),
]

STYLE_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(format|indent|whitespace|lint|style|spacing|trailing)", re.IGNORECASE
    ),
]

IMPORT_ONLY_PATTERN = re.compile(
    r"^[+-]\s*(import\s|from\s|require\(|use\s|#include)", re.MULTILINE
)

BREAKING_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"^-\s*(def|class|function|export|pub fn|pub struct)\s+\w+", re.MULTILINE
    ),
    re.compile(r"^-\s*(export\s+(default\s+)?)", re.MULTILINE),
    re.compile(r"BREAKING[\s_-]?CHANGE", re.IGNORECASE),
    re.compile(r"removed?\s+(public|exported|api)", re.IGNORECASE),
]

SIGNATURE_CHANGE_PATTERN = re.compile(
    r"^-\s*(def|function|fn|func|pub fn)\s+(\w+)\s*\(([^)]*)\).*$", re.MULTILINE
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_extension(file_path: str) -> str:
    """Extract file extension without dot, lowercased."""
    name = PurePosixPath(file_path).name.lower()
    if name == "dockerfile":
        return "dockerfile"
    parts = name.rsplit(".", 1)
    return parts[1] if len(parts) > 1 else ""


def _extract_new_definitions(changed_lines: list[str]) -> list[str]:
    """Extract names of new function/class definitions from added lines."""
    defs: list[str] = []
    patterns = [
        re.compile(r"^\+\s*(?:async\s+)?def\s+(\w+)"),
        re.compile(r"^\+\s*class\s+(\w+)"),
        re.compile(r"^\+\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"),
        re.compile(r"^\+\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="),
        re.compile(r"^\+\s*pub\s+fn\s+(\w+)"),
    ]
    for line in changed_lines:
        for p in patterns:
            m = p.match(line)
            if m:
                defs.append(m.group(1))
                break
    return defs


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _parse_file_diff(file_diff: str) -> DiffHunk | None:
    """Parse one stripped `diff --git` file section into a DiffHunk.

    Returns None for a section without a parseable `diff --git a/... b/...`
    header so the caller can skip it.
    """
    if not file_diff.startswith("diff --git"):
        return None

    header_match = re.match(
        r"diff --git a/(.*?) b/(.*)", file_diff.split("\n")[0]
    )
    if not header_match:
        return None

    file_path = header_match.group(2)
    ext = _get_extension(file_path)
    lines = file_diff.split("\n")
    changed_lines: list[str] = []
    additions = 0
    deletions = 0

    is_binary = any(
        "Binary files" in line or "GIT binary patch" in line for line in lines
    )

    if is_binary:
        changed_lines = [l for l in lines if "Binary" in l or "GIT binary" in l]
    else:
        for line in lines:
            if line.startswith("rename from") or line.startswith("rename to"):
                changed_lines.append(line)
            elif line.startswith("+") and not line.startswith("+++"):
                additions += 1
                changed_lines.append(line)
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
                changed_lines.append(line)

    return DiffHunk(
        file_path=file_path,
        additions=additions,
        deletions=deletions,
        changed_lines=changed_lines,
        file_type=ext,
    )


def parse_diff(diff_text: str) -> list[DiffHunk]:
    """Parse unified diff format into structured hunks.

    Handles regular diffs, binary files, renames, new files, and deletions.
    """
    if not diff_text or not diff_text.strip():
        return []

    file_diffs = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
    hunks: list[DiffHunk] = []
    for file_diff in file_diffs:
        hunk = _parse_file_diff(file_diff.strip())
        if hunk is not None:
            hunks.append(hunk)
    return hunks


# Content keyword-pattern groups, in priority/insertion order. Order is
# load-bearing: `max(scores, ...)` returns the FIRST key on a score tie, so this
# tuple's order must equal the original scores-dict insertion order
# (feat, fix, refactor, perf, style) for byte-identical tie-breaking.
_CONTENT_PATTERN_GROUPS = (
    (FEAT_PATTERNS, "feat"),
    (FIX_PATTERNS, "fix"),
    (REFACTOR_PATTERNS, "refactor"),
    (PERF_PATTERNS, "perf"),
    (STYLE_PATTERNS, "style"),
)


def _classify_by_structure(hunk: DiffHunk) -> str | None:
    """Strong structural signals: path pattern, binary, pure rename, import-only.

    Returns a change type when one fires (highest-priority signal first), else
    None so the caller falls through to content scoring.
    """
    for pattern, change_type in PATH_PATTERNS:
        if pattern.search(hunk.file_path):
            return change_type
    if hunk.is_binary:
        return "chore"
    if hunk.is_rename and hunk.additions == 0 and hunk.deletions == 0:
        return "refactor"
    non_empty = [l for l in hunk.changed_lines if l.strip() not in ("+", "-")]
    if non_empty and all(IMPORT_ONLY_PATTERN.match(l) for l in non_empty):
        return "chore"
    return None


def _score_content(hunk: DiffHunk) -> dict[str, float]:
    """Keyword-pattern hit counts per type, plus add/delete-ratio biases."""
    content = "\n".join(hunk.changed_lines)
    scores: dict[str, float] = {label: 0.0 for _, label in _CONTENT_PATTERN_GROUPS}
    for patterns, label in _CONTENT_PATTERN_GROUPS:
        scores[label] += sum(1.0 for p in patterns if p.search(content))

    # Bias: net additions -> feat
    if hunk.additions > hunk.deletions * 2:
        scores["feat"] += 0.5

    # Bias: balanced add/delete -> refactor
    if hunk.additions > 0 and hunk.deletions > 0:
        ratio = min(hunk.additions, hunk.deletions) / max(
            hunk.additions, hunk.deletions
        )
        if ratio > 0.7:
            scores["refactor"] += 0.3

    return scores


def _classify_by_content(hunk: DiffHunk) -> str | None:
    """Best-scoring content type, or None when no keyword pattern fired."""
    scores = _score_content(hunk)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


def _classify_fallback(hunk: DiffHunk) -> str:
    """Add/delete-ratio heuristic — always returns a type (last resort)."""
    if hunk.additions > hunk.deletions:
        return "feat"
    if hunk.deletions > hunk.additions:
        return "refactor"
    return "chore"


def classify_change(hunk: DiffHunk) -> str:
    """Classify a hunk as feat/fix/refactor/docs/test/chore/perf/style.

    Classification priority (first non-None wins):
    1. Structural signals — path patterns, binary, pure rename, import-only
    2. Content-based keyword scoring (with add/delete-ratio biases)
    3. Add/delete-ratio heuristic fallback
    """
    return (
        _classify_by_structure(hunk)
        or _classify_by_content(hunk)
        or _classify_fallback(hunk)
    )


def detect_scope(hunks: list[DiffHunk]) -> str:
    """Detect scope from common directory or module.

    Single file: deepest meaningful directory or filename stem.
    Multiple files: longest common prefix or most-frequent directory.
    """
    if not hunks:
        return ""

    skip_dirs = {"src", "lib", "app", "apps", "pkg", "packages", "internal"}

    if len(hunks) == 1:
        parts = PurePosixPath(hunks[0].file_path).parts
        if len(parts) == 1:
            name = parts[0]
            return name.rsplit(".", 1)[0] if "." in name else name
        for part in parts[:-1]:
            if part.lower() not in skip_dirs:
                return part
        if len(parts) > 1:
            return parts[-2]
        return parts[0]

    # Multiple files
    all_dirs: list[tuple[str, ...]] = []
    for h in hunks:
        parts = PurePosixPath(h.file_path).parts
        all_dirs.append(parts[:-1] if len(parts) > 1 else ())

    non_empty = [d for d in all_dirs if d]
    if not non_empty:
        return ""

    # Longest common prefix
    common = list(non_empty[0])
    for d in non_empty[1:]:
        new_common = []
        for a, b in zip(common, d):
            if a == b:
                new_common.append(a)
            else:
                break
        common = new_common

    if not common:
        dir_counts: Counter = Counter()
        for d in non_empty:
            for part in d:
                if part.lower() not in skip_dirs:
                    dir_counts[part] += 1
                    break
        if dir_counts:
            return dir_counts.most_common(1)[0][0]
        return ""

    for part in common:
        if part.lower() not in skip_dirs:
            return part
    return common[-1] if common else ""


def generate_subject(hunks: list[DiffHunk], change_type: str) -> str:
    """Generate concise subject line (max 50 chars)."""
    if not hunks:
        return "empty change"

    verbs = {
        "feat": "add",
        "fix": "fix",
        "refactor": "refactor",
        "docs": "update",
        "test": "add tests for",
        "chore": "update",
        "perf": "improve performance of",
        "style": "format",
    }
    verb = verbs.get(change_type, "update")

    if len(hunks) == 1:
        hunk = hunks[0]
        name = PurePosixPath(hunk.file_path).stem

        if hunk.is_rename:
            rename_from = rename_to = None
            for line in hunk.changed_lines:
                if line.startswith("rename from"):
                    rename_from = line.split("rename from ", 1)[1].strip()
                elif line.startswith("rename to"):
                    rename_to = line.split("rename to ", 1)[1].strip()
            if rename_from and rename_to:
                subj = f"rename {PurePosixPath(rename_from).name} to {PurePosixPath(rename_to).name}"
            else:
                subj = f"rename {name}"
        elif hunk.is_binary:
            subj = f"{verb} {name} binary"
        elif change_type == "test":
            subj = f"add tests for {name}"
        elif change_type == "docs":
            subj = f"update {name} documentation"
        else:
            new_defs = _extract_new_definitions(hunk.changed_lines)
            if new_defs and change_type == "feat":
                subj = f"add {new_defs[0]}"
            elif new_defs and change_type == "fix":
                subj = f"fix {new_defs[0]}"
            else:
                subj = f"{verb} {name}"
    else:
        file_types = Counter(h.file_type for h in hunks)
        scope = detect_scope(hunks)

        if change_type == "test":
            subj = f"add tests for {scope}" if scope else "add tests"
        elif change_type == "docs":
            subj = f"update {scope} documentation" if scope else "update documentation"
        elif scope:
            subj = f"{verb} {scope}"
        else:
            most_common_type = (
                file_types.most_common(1)[0][0] if file_types else ""
            )
            if most_common_type:
                subj = f"{verb} {len(hunks)} {most_common_type} files"
            else:
                subj = f"{verb} {len(hunks)} files"

    if len(subj) > 50:
        subj = subj[:47] + "..."

    return subj


def generate_body(hunks: list[DiffHunk]) -> str:
    """Generate detailed body with file summary and stats."""
    if not hunks:
        return ""

    lines: list[str] = []
    total_add = sum(h.additions for h in hunks)
    total_del = sum(h.deletions for h in hunks)

    if len(hunks) <= 10:
        lines.append("Changes:")
        for h in hunks:
            indicator = ""
            if h.is_rename:
                indicator = " (renamed)"
            elif h.is_binary:
                indicator = " (binary)"
            elif h.additions > 0 and h.deletions == 0:
                indicator = " (new)"
            elif h.additions == 0 and h.deletions > 0:
                indicator = " (deleted)"
            lines.append(
                f"  - {h.file_path}: +{h.additions}/-{h.deletions}{indicator}"
            )
    else:
        lines.append(f"Changed {len(hunks)} files")
        dir_counts: Counter = Counter()
        for h in hunks:
            parts = PurePosixPath(h.file_path).parts
            d = parts[0] if len(parts) > 1 else "(root)"
            dir_counts[d] += 1
        for d, count in dir_counts.most_common(5):
            lines.append(f"  - {d}/: {count} files")

    lines.append("")
    lines.append(f"Stats: +{total_add}/-{total_del} across {len(hunks)} file(s)")

    all_new_defs: list[str] = []
    for h in hunks:
        all_new_defs.extend(_extract_new_definitions(h.changed_lines))
    if all_new_defs:
        lines.append("")
        lines.append("New definitions:")
        for d in all_new_defs[:10]:
            lines.append(f"  - {d}")
        if len(all_new_defs) > 10:
            lines.append(f"  ... and {len(all_new_defs) - 10} more")

    return "\n".join(lines)


def detect_breaking_change(hunks: list[DiffHunk]) -> bool:
    """Check for breaking changes (removed public APIs, changed signatures)."""
    for hunk in hunks:
        content = "\n".join(hunk.changed_lines)
        added_content = "\n".join(
            l for l in hunk.changed_lines if l.startswith("+")
        )
        removed_content = "\n".join(
            l for l in hunk.changed_lines if l.startswith("-")
        )

        # Explicit BREAKING CHANGE marker (in any line)
        breaking_marker = re.compile(r"BREAKING[\s_-]?CHANGE", re.IGNORECASE)
        if breaking_marker.search(content):
            return True

        # "removed public/exported/api" comment (in added lines = documenting removal)
        removed_api_pattern = re.compile(
            r"removed?\s+(public|exported|api)", re.IGNORECASE
        )
        if removed_api_pattern.search(added_content):
            return True

        # Collect removed and added function/class signatures
        removed_sigs: dict[str, str] = {}
        added_sigs: dict[str, str] = {}
        removed_names: set[str] = set()
        added_names: set[str] = set()

        name_pattern = re.compile(
            r"^\s*(def|class|function|export|pub fn|pub struct)\s+(\w+)"
        )
        sig_pattern = re.compile(
            r"^\s*(def|function|fn|func|pub fn)\s+(\w+)\s*\(([^)]*)\)"
        )

        for line in hunk.changed_lines:
            raw = line[1:]  # strip +/- prefix
            if line.startswith("-"):
                m = name_pattern.match(raw)
                if m:
                    removed_names.add(m.group(2))
                m2 = sig_pattern.match(raw)
                if m2:
                    removed_sigs[m2.group(2)] = m2.group(3).strip()
            elif line.startswith("+"):
                m = name_pattern.match(raw)
                if m:
                    added_names.add(m.group(2))
                m2 = sig_pattern.match(raw)
                if m2:
                    added_sigs[m2.group(2)] = m2.group(3).strip()

        # Signature changes: same name, different params
        for func_name in removed_sigs:
            if func_name in added_sigs:
                if removed_sigs[func_name] != added_sigs[func_name]:
                    return True

        # Truly removed public names (not renamed/re-added)
        truly_removed = removed_names - added_names
        for name in truly_removed:
            if not name.startswith("_"):
                return True

    return False


def analyze_diff(diff_text: str) -> CommitAnalysis:
    """Full analysis pipeline: parse, classify, suggest."""
    hunks = parse_diff(diff_text)

    if not hunks:
        return CommitAnalysis()

    # Classify
    classifications: list[str] = [classify_change(h) for h in hunks]

    cat_counts: dict[str, int] = {}
    for c in classifications:
        cat_counts[c] = cat_counts.get(c, 0) + 1

    # Primary language by weighted change count
    lang_counts: Counter = Counter()
    for h in hunks:
        lang = EXTENSION_LANGUAGE_MAP.get(h.file_type, h.file_type or "unknown")
        lang_counts[lang] += h.total_changes or 1
    primary_language = (
        lang_counts.most_common(1)[0][0] if lang_counts else "unknown"
    )

    total_add = sum(h.additions for h in hunks)
    total_del = sum(h.deletions for h in hunks)
    breaking = detect_breaking_change(hunks)
    scope = detect_scope(hunks)

    # Suggestions ranked by category frequency
    suggestions: list[CommitSuggestion] = []
    sorted_cats = sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)

    for i, (cat, count) in enumerate(sorted_cats):
        cat_hunks = [
            h for h, c in zip(hunks, classifications) if c == cat
        ]
        cat_scope = detect_scope(cat_hunks) if len(sorted_cats) > 1 else scope

        confidence = count / len(hunks)
        if i > 0:
            confidence *= 0.7

        subject = generate_subject(cat_hunks, cat)
        body = generate_body(cat_hunks)

        suggestions.append(
            CommitSuggestion(
                type=cat,
                scope=cat_scope,
                subject=subject,
                body=body,
                breaking=breaking if i == 0 else False,
                confidence=round(confidence, 2),
            )
        )

    suggestions.sort(key=lambda s: s.confidence, reverse=True)

    return CommitAnalysis(
        hunks=hunks,
        suggestions=suggestions,
        files_changed=len(hunks),
        total_additions=total_add,
        total_deletions=total_del,
        primary_language=primary_language,
        change_categories=cat_counts,
    )


def _format_prefix(suggestion: CommitSuggestion) -> str:
    """Conventional-commit prefix: type, optional (scope), optional ! for breaking."""
    prefix = suggestion.type
    if suggestion.scope:
        prefix = f"{suggestion.type}({suggestion.scope})"
    if suggestion.breaking:
        prefix += "!"
    return prefix


def format_commit_message(suggestion: CommitSuggestion) -> str:
    """Format a CommitSuggestion as a conventional commit string."""
    prefix = _format_prefix(suggestion)

    msg = f"{prefix}: {suggestion.subject}"

    if suggestion.body:
        msg += f"\n\n{suggestion.body}"

    if suggestion.breaking:
        msg += "\n\nBREAKING CHANGE: This commit contains breaking changes."

    return msg


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_text(analysis: CommitAnalysis) -> str:
    """Terminal display with ranked suggestions."""
    if not analysis.suggestions:
        return "No changes detected."

    lines: list[str] = []
    lines.append(
        f"Analyzed {analysis.files_changed} file(s): "
        f"+{analysis.total_additions}/-{analysis.total_deletions}"
    )
    lines.append(f"Primary language: {analysis.primary_language}")
    cats_str = ", ".join(
        f"{k}({v})"
        for k, v in sorted(
            analysis.change_categories.items(), key=lambda x: x[1], reverse=True
        )
    )
    lines.append(f"Categories: {cats_str}")
    lines.append("")

    for i, suggestion in enumerate(analysis.suggestions):
        conf_pct = int(suggestion.confidence * 100)
        lines.append(f"--- Suggestion {i + 1} (confidence: {conf_pct}%) ---")
        lines.append(format_commit_message(suggestion))
        lines.append("")

    return "\n".join(lines)


def format_slack(analysis: CommitAnalysis) -> str:
    """Slack mrkdwn format (*bold* not **bold**, _italic_ not *italic*)."""
    if not analysis.suggestions:
        return "No changes detected."

    lines: list[str] = []
    lines.append("*Smart Commit Analysis*")
    lines.append(
        f"{analysis.files_changed} file(s) changed | "
        f"+{analysis.total_additions}/-{analysis.total_deletions} | "
        f"{analysis.primary_language}"
    )
    lines.append("")

    for i, suggestion in enumerate(analysis.suggestions):
        conf_pct = int(suggestion.confidence * 100)
        prefix = _format_prefix(suggestion)

        lines.append(f"*Option {i + 1}* ({conf_pct}% confidence)")
        lines.append(f"`{prefix}: {suggestion.subject}`")
        if suggestion.breaking:
            lines.append("_BREAKING CHANGE_")
        lines.append("")

    return "\n".join(lines)


def format_json(analysis: CommitAnalysis) -> str:
    """JSON output."""
    data = {
        "files_changed": analysis.files_changed,
        "total_additions": analysis.total_additions,
        "total_deletions": analysis.total_deletions,
        "primary_language": analysis.primary_language,
        "change_categories": analysis.change_categories,
        "suggestions": [
            {
                "type": s.type,
                "scope": s.scope,
                "subject": s.subject,
                "body": s.body,
                "breaking": s.breaking,
                "confidence": s.confidence,
                "formatted": format_commit_message(s),
            }
            for s in analysis.suggestions
        ],
    }
    return json.dumps(data, indent=2)


def format_options_picker(analysis: CommitAnalysis) -> dict:
    """Returns dict for grip_options_picker MCP app."""
    if not analysis.suggestions:
        return {
            "title": "Smart Commit",
            "description": "No changes detected",
            "options": [],
        }

    options: list[dict] = []
    for i, suggestion in enumerate(analysis.suggestions):
        prefix = _format_prefix(suggestion)

        options.append(
            {
                "id": f"option_{i + 1}",
                "label": f"{prefix}: {suggestion.subject}",
                "description": f"{int(suggestion.confidence * 100)}% confidence",
                "value": format_commit_message(suggestion),
                "metadata": {
                    "type": suggestion.type,
                    "scope": suggestion.scope,
                    "breaking": suggestion.breaking,
                    "confidence": suggestion.confidence,
                },
            }
        )

    return {
        "title": "Smart Commit Message",
        "description": (
            f"Analyzed {analysis.files_changed} file(s): "
            f"+{analysis.total_additions}/-{analysis.total_deletions}"
        ),
        "options": options,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart commit message generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--diff-file",
        type=str,
        default=None,
        help="Path to file containing diff (reads stdin if not provided)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "picker"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    if args.diff_file:
        with open(args.diff_file, "r") as f:
            diff_text = f.read()
    else:
        diff_text = sys.stdin.read()

    analysis = analyze_diff(diff_text)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "picker": lambda a: json.dumps(format_options_picker(a), indent=2),
    }

    print(formatters[args.format](analysis))


if __name__ == "__main__":
    main()
