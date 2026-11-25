"""Cheap metadata snapshots for freshness and deterministic memo keys."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from lib.claude_dir import get_claude_dir
from lib.recall import corpus


@dataclass(frozen=True)
class Snapshot:
    stores: dict
    moat: dict
    decision_log: dict
    rulings: dict

    def vector(self):
        return {"stores": self.stores, "moat": self.moat,
                "decision_log": self.decision_log, "rulings": self.rulings}

    def key(self) -> str:
        return corpus.digest(self.vector())

    def stale(self, report_epoch) -> bool:
        return any(store["epoch"] > report_epoch for store in self.stores.values())


def stamp(path):
    if path is None:
        return None
    try:
        st = Path(path).stat()
        return [st.st_mtime_ns, st.st_size]
    except FileNotFoundError:
        return None


def _moat(meta_path):
    if meta_path is None:
        return {"available": False, "built_at": None, "count": None}
    path = Path(meta_path)
    state = {"path": str(path), "available": False, "built_at": None, "count": None,
             "files": {name: stamp(path.parent / name) for name in
                       ("meta.json", "events.jsonl", "mmr.json", "index.db")}}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        state.update(available=True, built_at=meta.get("built_at"),
                     count=meta.get("row_count", meta.get("count", meta.get("artefacts"))))
    except (FileNotFoundError, ValueError):
        return state
    return state


def _last_line(handle):
    position = handle.seek(0, 2)
    tail = b""
    while position:
        start = max(0, position - 4096)
        handle.seek(start)
        tail = handle.read(position - start) + tail
        position = start
        stripped = tail.rstrip(b"\r\n")
        if b"\n" in stripped:
            return stripped.rsplit(b"\n", 1)[-1].rstrip(b"\r")
    return tail.rstrip(b"\r\n")


def _decision(path):
    state = {"path": str(path) if path is not None else None, "size": 0,
             "last_line_sha256": None, "available": False}
    if path is None:
        return state
    try:
        with Path(path).open("rb") as handle:
            state.update(size=handle.seek(0, 2), available=True,
                         last_line_sha256=hashlib.sha256(_last_line(handle)).hexdigest())
    except FileNotFoundError:
        return state
    return state


def take(roots, *, moat_meta=None, idr_path=None) -> Snapshot:
    stores = corpus.store_vectors(roots, corpus.scan(roots))
    memory = Path(roots.memory_dir) / "MEMORY.md" if roots.memory_dir is not None else None
    ruling_state = {"claude": stamp(get_claude_dir() / "CLAUDE.md"), "pinned": stamp(memory)}
    return Snapshot(stores, _moat(moat_meta), _decision(idr_path), ruling_state)
