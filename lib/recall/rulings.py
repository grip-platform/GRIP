"""Explicit pinned laws and PARAMOUNT rules, with source line provenance."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from lib.recall import corpus
from lib.recall.normalise import normalise, content_words


@dataclass(frozen=True)
class Ruling:
    key: str
    title: str
    source: str
    superseded_by: str | None = None


def _read(path):
    # errors="replace", as the corpus layer decodes: an encrypted rule in a
    # clone without the git-crypt key is not UTF-8, and a strict read of it
    # failed this whole layer (found 2026-11-26).
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}, ""
    fields, _ = corpus.frontmatter(text)
    return fields, "" if fields.get("private") else text


def _ruling(title, path, number, fields):
    return Ruling(normalise(title), title, f"{path}:{number}", fields.get("superseded_by") or None)


def _pinned(memory):
    if memory is None:
        return []
    path = Path(memory) / "MEMORY.md"
    _, text = _read(path)
    active, found = False, []
    for number, line in enumerate(text.splitlines(), 1):
        if "<!-- GRIP-PINNED-START -->" in line:
            active = True
        elif "<!-- GRIP-PINNED-END -->" in line:
            active = False
        elif active and " :: " in line:
            title, record = line.split(" :: ", 1)
            target = Path(memory) / (record.strip().removesuffix(".md") + ".md")
            if target.parent != Path(memory):
                continue
            fields, _ = _read(target)
            if not fields.get("private"):
                found.append(_ruling(title.strip().lstrip("-* "), path, number, fields))
    return found


def _remembered(path):
    fields, text = _read(path)
    active, found = False, []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            active = line.strip() == "## Remembered laws"
        elif line.startswith("# "):
            active = False
        elif active:
            match = re.match(r"\s*-\s+\*\*(.+?)\*\*:", line)
            if match:
                found.append(_ruling(match.group(1), path, number, fields))
    return found


def _paramount(rules):
    found = []
    for _, entry_path, _ in corpus.scan_store(rules, "rule"):
        path = Path(entry_path)
        fields, text = _read(path)
        first = next(((n, line[2:].strip()) for n, line in enumerate(text.splitlines(), 1)
                      if line.startswith("# ")), None)
        if first is not None and "(PARAMOUNT)" in first[1]:
            found.append(_ruling(first[1], path, first[0], fields))
    return found


def load(claude_dir, *, roots=None) -> list[Ruling]:
    base = Path(claude_dir)
    if roots is None:
        roots = corpus.CorpusRoots(
            memory_dir=Path(os.environ.get("GRIP_RECALL_MEMORY_DIR") or corpus.default_memory_dir(base)),
            rules_dir=Path(os.environ.get("GRIP_RECALL_RULES_DIR") or base / "rules"))
    return _pinned(roots.memory_dir) + _remembered(base / "CLAUDE.md") + _paramount(roots.rules_dir)


def _matches(query, words, ruling):
    if ruling.superseded_by:
        return False
    return ruling.key == query or (len(words) >= 2 and set(words) <= set(content_words(ruling.key)))


def match(query, rulings) -> list[Ruling]:
    norm, words = normalise(query), content_words(query)
    return [r for r in rulings if _matches(norm, words, r)]
