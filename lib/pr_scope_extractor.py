"""PR scope extractor — parses unified git diff into structured hunks.

Input:  unified diff text (string or file path)
Output: PrScope dataclass with per-file hunks + language + size flags

Used by every downstream stage of the HAL Council pipeline:
  - pr_ast_differ reads ChangedFile.language == "python" for AST scoping
  - pr_mermaid_adapter reads ChangedFile.hunks to build the dep-flow graph
  - hal_council_review reads PrScope.too_large to gate Mermaid generation
  - complexity_profiler reads hunk line ranges for BIG-O mechanical scoring

Pure stdlib. No external deps. Safe to import in CI runners before pip install.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".mdx": "markdown",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
}


@dataclass(frozen=True)
class ChangedHunk:
    """One @@ block in a unified diff.

    Line numbers refer to the *new* file. `removed_lines` and `added_lines`
    hold the raw content without the leading +/- marker.
    """
    start_line: int
    end_line: int
    added_lines: tuple[str, ...]
    removed_lines: tuple[str, ...]

    @property
    def net_change(self) -> int:
        """Net line delta for this hunk (added - removed)."""
        return len(self.added_lines) - len(self.removed_lines)


@dataclass
class ChangedFile:
    """One file in the diff, with all its hunks."""
    path: str
    language: str
    hunks: list[ChangedHunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    is_renamed: bool = False
    old_path: Optional[str] = None
    is_binary: bool = False

    @property
    def total_added(self) -> int:
        return sum(len(h.added_lines) for h in self.hunks)

    @property
    def total_removed(self) -> int:
        return sum(len(h.removed_lines) for h in self.hunks)

    @property
    def total_changed(self) -> int:
        return self.total_added + self.total_removed


@dataclass
class PrScope:
    """Structured PR diff — the input contract for every downstream stage."""
    files: list[ChangedFile] = field(default_factory=list)
    size_limit: int = 500

    @property
    def total_added(self) -> int:
        return sum(f.total_added for f in self.files)

    @property
    def total_removed(self) -> int:
        return sum(f.total_removed for f in self.files)

    @property
    def total_changed(self) -> int:
        return self.total_added + self.total_removed

    @property
    def too_large(self) -> bool:
        """True when the diff exceeds the Mermaid-suppression threshold."""
        return self.total_changed > self.size_limit

    def files_by_language(self, language: str) -> list[ChangedFile]:
        return [f for f in self.files if f.language == language]

    def python_files(self) -> list[ChangedFile]:
        return self.files_by_language("python")


_FILE_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def _language_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return LANGUAGE_BY_EXTENSION.get(suffix, "other")


def parse_diff(diff_text: str) -> list[ChangedFile]:
    """Parse a unified diff into ChangedFile records.

    Handles: new file, deleted file, rename, binary, regular modification.
    Does NOT handle: combined-diff format (merges with conflicts). Treats
    unknown headers as regular modifications.
    """
    files: list[ChangedFile] = []
    current: Optional[ChangedFile] = None
    current_new_start = 0
    current_new_line = 0
    current_added: list[str] = []
    current_removed: list[str] = []
    current_end_line = 0

    def _flush_hunk() -> None:
        nonlocal current_added, current_removed
        if current and (current_added or current_removed):
            current.hunks.append(ChangedHunk(
                start_line=current_new_start,
                end_line=current_end_line,
                added_lines=tuple(current_added),
                removed_lines=tuple(current_removed),
            ))
        current_added = []
        current_removed = []

    for line in diff_text.splitlines():
        header_match = _FILE_HEADER.match(line)
        if header_match:
            _flush_hunk()
            if current is not None:
                files.append(current)
            new_path = header_match.group(2)
            current = ChangedFile(path=new_path, language=_language_for(new_path))
            continue

        if current is None:
            continue

        if line.startswith("new file mode"):
            current.is_new = True
            continue
        if line.startswith("deleted file mode"):
            current.is_deleted = True
            continue
        if line.startswith("rename from "):
            current.is_renamed = True
            current.old_path = line[len("rename from "):].strip()
            continue
        if line.startswith("rename to "):
            current.is_renamed = True
            continue
        if line.startswith("Binary files "):
            current.is_binary = True
            continue

        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match:
            _flush_hunk()
            current_new_start = int(hunk_match.group("new_start"))
            new_count_raw = hunk_match.group("new_count")
            new_count = int(new_count_raw) if new_count_raw is not None else 1
            current_new_line = current_new_start
            current_end_line = current_new_start + max(new_count - 1, 0)
            continue

        if line.startswith("+++ ") or line.startswith("--- "):
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current_added.append(line[1:])
            current_new_line += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            current_removed.append(line[1:])
            continue
        if line.startswith(" "):
            current_new_line += 1
            continue

    _flush_hunk()
    if current is not None:
        files.append(current)

    return files


def extract_scope(
    diff_text_or_path: str,
    size_limit: int = 500,
) -> PrScope:
    """Parse a diff and wrap it in a PrScope with size gating.

    Accepts either the raw diff text or a path to a file containing it.
    If the argument looks like an existing file path, it's read; otherwise
    it's treated as raw diff text.
    """
    if len(diff_text_or_path) < 4096 and Path(diff_text_or_path).is_file():
        diff_text = Path(diff_text_or_path).read_text()
    else:
        diff_text = diff_text_or_path

    files = parse_diff(diff_text)
    return PrScope(files=files, size_limit=size_limit)


__all__ = [
    "ChangedHunk",
    "ChangedFile",
    "PrScope",
    "extract_scope",
    "parse_diff",
    "LANGUAGE_BY_EXTENSION",
]
