"""HAL Memory Engine — unified memory interface absorbing CC memdir patterns.

Reads and writes CC-compatible MEMORY.md format (200-line / 25KB limits),
integrates with GRIP's KONO semantic search, and provides a runtime-agnostic
memory interface for the HAL.

Usage:
    from lib.hal.memory_engine import MemoryEngine

    engine = MemoryEngine()
    state = engine.read_memory()       # Full memory state
    engine.write_entry("topic.md", frontmatter, content)
    engine.update_entrypoint()         # Rebuild MEMORY.md index
"""
import logging
import json
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# CC constants (from TF++ src/memdir/memdir.ts)
ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000

from lib.hal.home import HAL_HOME as _HAL_HOME
from lib.frontmatter_scalar import unquote as _unquote
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class MemoryEntry:
    """A single memory file with frontmatter metadata."""

    def __init__(
        self,
        filename: str,
        name: str = "",
        description: str = "",
        memory_type: str = "",
        content: str = "",
    ):
        self.filename = filename
        self.name = name
        self.description = description
        self.memory_type = memory_type
        self.content = content

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "name": self.name,
            "description": self.description,
            "type": self.memory_type,
            "content": self.content,
        }


class MemoryState:
    """Complete memory state for a memory directory."""

    def __init__(
        self,
        entrypoint: str = "",
        entries: Optional[list[MemoryEntry]] = None,
        truncated: bool = False,
    ):
        self.entrypoint = entrypoint
        self.entries = entries or []
        self.truncated = truncated


class MemoryEngine:
    """Reads and writes CC-compatible memory directories."""

    def __init__(self, memory_dir: Optional[str] = None):
        self._dir = memory_dir or _default_memory_dir()

    @property
    def memory_dir(self) -> str:
        return self._dir

    def read_memory(self) -> MemoryState:
        """Read the full memory state: entrypoint + all linked entries."""
        entrypoint_path = os.path.join(self._dir, ENTRYPOINT_NAME)
        if not os.path.exists(entrypoint_path):
            return MemoryState()

        raw = _read_file(entrypoint_path)
        truncated_content, was_truncated = truncate_entrypoint(raw)
        entries = self._scan_entries()

        return MemoryState(
            entrypoint=truncated_content,
            entries=entries,
            truncated=was_truncated,
        )

    def read_entry(self, filename: str) -> Optional[MemoryEntry]:
        """Read a single memory entry by filename."""
        path = os.path.join(self._dir, filename)
        if not os.path.exists(path):
            return None
        return _parse_entry(filename, _read_file(path))

    def write_entry(
        self,
        filename: str,
        name: str,
        description: str,
        memory_type: str,
        content: str,
    ) -> None:
        """Write a memory entry with frontmatter."""
        os.makedirs(self._dir, exist_ok=True)
        path = os.path.join(self._dir, filename)

        # Both `name` and `description` are arbitrary caller-supplied prose, so
        # both are emitted as JSON strings. YAML 1.2 is a superset of JSON, so
        # json.dumps gives a valid double-quoted scalar with every colon, quote,
        # backslash and newline escaped. Bare interpolation makes a colon-space
        # read as a nested mapping and an opening quote start a scalar that never
        # closes -- measured across 42 records in the GRIP corpus and fixed at
        # lib/auto_memory_writer.py (GRIP#6096) and lib/remember.py (GRIP#6100).
        #
        # `memory_type` is a short enum-like token and stays bare.
        frontmatter = (
            f"---\nname: {json.dumps(name)}\n"
            f"description: {json.dumps(description)}\n"
            f"type: {memory_type}\n---\n\n"
        )
        _write_file(path, frontmatter + content)

    def delete_entry(self, filename: str) -> bool:
        """Delete a memory entry. Returns True if deleted."""
        path = os.path.join(self._dir, filename)
        if os.path.exists(path) and filename != ENTRYPOINT_NAME:
            os.remove(path)
            return True
        return False

    def update_entrypoint(self, content: str) -> bool:
        """Write the MEMORY.md entrypoint, enforcing CC limits.

        Returns True when the content had to be truncated to fit, meaning the
        dropped tail is now unreadable. ``truncate_entrypoint`` already computes
        that flag; this reports it rather than discarding it, so an overflowing
        write cannot look identical to a clean one. Mirrors the explicit signal
        Claude Code 2.1.210 introduced for the same situation upstream.

        Existing callers may keep ignoring the return value — the log line
        carries the same information.
        """
        os.makedirs(self._dir, exist_ok=True)
        truncated, was_truncated = truncate_entrypoint(content)
        path = os.path.join(self._dir, ENTRYPOINT_NAME)
        _write_file(path, truncated)
        if was_truncated:
            logger.error(
                "memory_engine: %s truncated on write — %d chars / %d lines in, "
                "%d chars / %d lines written (limits: %d lines, %d bytes). The "
                "dropped tail will not be read back.",
                ENTRYPOINT_NAME,
                len(content), content.count(chr(10)) + 1,
                len(truncated), truncated.count(chr(10)) + 1,
                MAX_ENTRYPOINT_LINES, MAX_ENTRYPOINT_BYTES,
            )
        return was_truncated

    def list_entries(self) -> list[str]:
        """List all .md files in the memory directory (excluding MEMORY.md)."""
        if not os.path.isdir(self._dir):
            return []
        return sorted(
            f for f in os.listdir(self._dir)
            if f.endswith(".md") and f != ENTRYPOINT_NAME
        )

    def _scan_entries(self) -> list[MemoryEntry]:
        """Scan all memory entries and parse frontmatter."""
        return [
            _parse_entry(fn, _read_file(os.path.join(self._dir, fn)))
            for fn in self.list_entries()
            if _read_file(os.path.join(self._dir, fn))
        ]


def truncate_entrypoint(raw: str) -> tuple[str, bool]:
    """Truncate MEMORY.md to CC limits (200 lines / 25KB).

    Returns (truncated_content, was_truncated).

    Reproduces the truncation logic of CC's ``truncateEntrypointContent``
    (memdir.ts) as it behaved BEFORE 2.1.210. Since 2.1.210 a memory write that
    leaves the index over its read limit produces an explicit error upstream
    instead of truncating silently, so truncating here is no longer sufficient
    to match upstream's WRITE contract — callers must surface ``was_truncated``
    rather than discard it. Whether the upstream helper itself still truncates
    for the READ path is not verifiable from here; only the write behaviour
    change is documented.
    """
    trimmed = raw.strip()
    lines = trimmed.split("\n")

    was_truncated = len(lines) > MAX_ENTRYPOINT_LINES or len(trimmed) > MAX_ENTRYPOINT_BYTES

    if not was_truncated:
        return trimmed, False

    truncated = "\n".join(lines[:MAX_ENTRYPOINT_LINES]) if len(lines) > MAX_ENTRYPOINT_LINES else trimmed

    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut_at = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES)
        truncated = truncated[:cut_at] if cut_at > 0 else truncated[:MAX_ENTRYPOINT_BYTES]

    return truncated, True


def _parse_entry(filename: str, raw: str) -> MemoryEntry:
    """Parse a memory file with optional YAML frontmatter."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return MemoryEntry(filename=filename, content=raw.strip())

    fm = _parse_simple_frontmatter(match.group(1))
    return MemoryEntry(
        filename=filename,
        name=fm.get("name", ""),
        description=fm.get("description", ""),
        memory_type=fm.get("type", ""),
        content=raw[match.end():].strip(),
    )


def _parse_simple_frontmatter(text: str) -> dict[str, str]:
    """Parse simple key: value frontmatter (no nested structures).

    write_entry emits ``name`` and ``description`` as JSON strings (GRIP#6096), so
    a double-quoted value is decoded back. Without that, a name written as Test
    read back as "Test", with its quotes and any escapes still in it.
    """
    result = {}
    for line in text.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = _unquote(val.strip())
    return result


def _default_memory_dir() -> str:
    """Resolve the default memory directory for the current project."""
    cwd = os.getcwd()
    project_key = cwd.replace("/", "-").replace(".", "-")
    project_mem = os.path.join(_HAL_HOME, "projects", project_key, "memory")
    if os.path.isdir(project_mem):
        return project_mem
    return os.path.join(_HAL_HOME, "memory")


def _read_file(path: str) -> str:
    """Read file contents, returning empty string on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _write_file(path: str, content: str) -> None:
    """Write content to file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
