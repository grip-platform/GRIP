"""Atomic JSONL append and read helpers.

Single shared shim — every Copilot writer goes through ``append`` and every
reader goes through ``read``. Atomic via ``os.fsync`` + line buffering;
crash-safe enough for this workload (single-writer per file, append-only).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List


def append(path: Path, record: Dict[str, Any]) -> None:
    """Append one JSON record to a JSONL file. Creates parents on demand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read(path: Path, limit: int = 0, reverse: bool = False) -> List[Dict[str, Any]]:
    """Read all records from a JSONL file. ``limit`` 0 means no cap."""
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if reverse:
        records.reverse()
    if limit > 0:
        records = records[:limit]
    return records


def stream(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield records one at a time. Cheap iteration for big files."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def write_all(path: Path, records: List[Dict[str, Any]]) -> None:
    """Atomically rewrite an entire JSONL file (tmp + rename).

    For read-modify-rewrite workloads — prediction grading, log compaction —
    where ``append`` cannot express a mutation. Single-writer + atomic rename,
    so a concurrent reader sees either the old file or the new one, never a
    half-written line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_json(path: Path, payload: Any) -> None:
    """Write a JSON snapshot atomically (tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    """Read a JSON file. Return ``default`` (or ``{}``) on any failure."""
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {} if default is None else default
