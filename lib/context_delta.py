#!/usr/bin/env python3
"""
context_delta.py -- Context delta tracker for cross-session awareness.

Tracks what changed between /save invocations. Each delta captures:
- genome generation + gene count
- insight count (learning/insights.jsonl line count)
- MEMORY.md hash
- instance state hash
- timestamp

Deltas are append-only JSONL. Sessions can query "what changed since last time?"

Usage:
    from lib.context_delta import compute_delta, append_delta, get_deltas_since
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DELTA_PATH = CLAUDE_DIR / "cache" / "context-deltas.jsonl"


def _file_hash(path: Path) -> str:
    """SHA-256 of file contents, or empty string if missing."""
    if not path.exists():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _line_count(path: Path) -> int:
    """Count lines in a file, 0 if missing."""
    if not path.exists():
        return 0
    try:
        with open(path) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _genome_info() -> dict:
    """Extract genome generation and gene count from cache."""
    genome_cache = CLAUDE_DIR / "cache" / "genome.json"
    if not genome_cache.exists():
        return {"generation": 0, "gene_count": 0}
    try:
        data = json.loads(genome_cache.read_text())
        return {
            "generation": data.get("generation", 0),
            "gene_count": data.get("gene_count", 0),
        }
    except (json.JSONDecodeError, OSError):
        return {"generation": 0, "gene_count": 0}


def _memory_hash() -> str:
    """Hash of project MEMORY.md (CWD-scoped)."""
    cwd = os.getcwd()
    encoded = cwd.replace("/", "-").replace(".", "-")
    mem_path = CLAUDE_DIR / "projects" / encoded / "memory" / "MEMORY.md"
    return _file_hash(mem_path)


def compute_delta(previous_hash: str = "") -> dict:
    """Compute current context state and delta from previous hash.

    Returns dict with context_hash, genome info, insight count,
    memory hash, and whether anything changed.
    """
    genome = _genome_info()
    insight_count = _line_count(CLAUDE_DIR / "learning" / "insights.jsonl")
    memory_h = _memory_hash()

    # Build composite hash
    state_str = f"{genome['generation']}:{genome['gene_count']}:{insight_count}:{memory_h}"
    context_hash = hashlib.sha256(state_str.encode()).hexdigest()[:16]

    return {
        "timestamp": time.time(),
        "context_hash": context_hash,
        "previous_hash": previous_hash,
        "changed": context_hash != previous_hash,
        "genome_generation": genome["generation"],
        "genome_gene_count": genome["gene_count"],
        "insight_count": insight_count,
        "memory_hash": memory_h,
    }


def append_delta(delta: dict) -> None:
    """Append a delta to the JSONL file with file locking."""
    DELTA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DELTA_PATH, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(delta) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def get_deltas_since(timestamp: float = 0, limit: int = 50) -> list[dict]:
    """Return deltas since a given timestamp, most recent first."""
    if not DELTA_PATH.exists():
        return []
    deltas = []
    with open(DELTA_PATH) as f:
        for line in f:
            try:
                d = json.loads(line)
                if d.get("timestamp", 0) > timestamp:
                    deltas.append(d)
            except json.JSONDecodeError:
                continue
    return sorted(deltas, key=lambda x: x.get("timestamp", 0), reverse=True)[:limit]


def get_latest_hash() -> str:
    """Return the most recent context hash, or empty string."""
    if not DELTA_PATH.exists():
        return ""
    last_line = ""
    try:
        with open(DELTA_PATH) as f:
            for line in f:
                if line.strip():
                    last_line = line
        if last_line:
            return json.loads(last_line).get("context_hash", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""
