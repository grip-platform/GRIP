"""The main-session tier: rules only the main session needs, delivered to it in full.

A rule listed in config/rules-scope.json ``main_tier`` is kept out of Claude Code's
native loading by settings.claudeMdExcludes, so no subagent loads it, and it reaches
the main session whole through hooks/main-tier-inject.py. Settings bind that hook on
SessionStart once per chunk, ``--chunk 0`` to ``--chunk 5``, for every kind of start,
so the rules arrive again after /clear and after compaction (KERN v2, ruling 5,
2026-10-18).

Two measured limits shape the packing
(docs/measurements/2026-10-06-kern-v2-vendor-probes.md):

- a SessionStart value over 10,000 characters reaches the model as a 2KB preview
  (GRIP#6446), so every chunk, its header included, stays at or under CHUNK_LIMIT.
  The cap is on the additionalContext string, not on the escaped JSON line: "For
  JSON output, each field is measured separately" (code.claude.com/docs/en/hooks,
  read 2026-10-18);
- the values of several hooks all arrive, but in no guaranteed order, so a chunk
  carries whole rules, and a rule too long for one chunk is cut at line ends into
  parts that each name the rule, their place in it, and that they apply as one rule.

Characters are UTF-16 code units, Claude Code's own unit (lib/lexicon/surfaces.py).
The same configuration and rule files always give the same chunks, whatever order
``main_tier`` lists the rules in.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lexicon.surfaces import load_text, utf16_len

CHUNK_LIMIT = 9_500  # under the 10,000 at which the harness shows only a preview

# The widest headers a piece and a chunk can carry, reserved before anything is
# packed, so no chunk can pass CHUNK_LIMIT whatever its number or its rule's parts.
_MOST_PARTS = 999
_MOST_CHUNKS = 99


@dataclass(frozen=True)
class Piece:
    """A whole rule, or one labelled part of a rule too long for a chunk."""

    name: str
    index: int  # 1-based
    total: int
    body: str

    @property
    def header(self) -> str:
        if self.total == 1:
            return f"## rules/{self.name}\n\n"
        return (f"## rules/{self.name}, part {self.index} of {self.total}\n"
                f"These {self.total} parts are one rule and apply together; they may "
                "arrive in any order.\n\n")

    @property
    def text(self) -> str:
        """The piece as delivered: always two newlines after the body, so the next piece
        starts clean and the body can be read back exactly from what was delivered."""
        return self.header + self.body + "\n\n"

    @property
    def size(self) -> int:
        return utf16_len(self.text)


def chunk_header(number: int, count: int) -> str:
    """The opening of chunk ``number`` of ``count``: what the rules are and how to read them."""
    return (f"# GRIP main-session rules: chunk {number} of {count}\n"
            "The rules below apply to THIS main session exactly as if they were natively "
            "loaded. They are kept out of native loading so that no subagent carries them "
            "(config/rules-scope.json `main_tier`), and SessionStart hooks deliver them here "
            f"whole, in {count} chunks that may arrive in any order. A rule too long for one "
            "chunk comes in labelled parts: read its parts together as one rule.\n\n")


# What a chunk holds once the widest chunk header is paid for.
CAPACITY = CHUNK_LIMIT - utf16_len(chunk_header(_MOST_CHUNKS, _MOST_CHUNKS))


def names(cfg: dict, rules_dir: Path) -> list[str]:
    """The main-tier rules present under ``rules_dir``, sorted, so the order of the
    config's list never changes the chunks."""
    return sorted(n for n in set(cfg.get("main_tier") or []) if (rules_dir / n).is_file())


def _cut(line: str, room: int) -> list[str]:
    """``line`` itself, or slices of it that each fit ``room`` when the line alone does
    not. A code point is at most two UTF-16 units, so a slice of room // 2 always fits."""
    if utf16_len(line) <= room:
        return [line]
    step = max(1, room // 2)
    return [line[i:i + step] for i in range(0, len(line), step)]


def split(text: str, room: int) -> list[str]:
    """Consecutive slices of ``text``, each at most ``room`` UTF-16 units and cut at a
    line end wherever a line fits. Joined, they give back ``text`` exactly."""
    atoms = [atom for line in text.splitlines(keepends=True) for atom in _cut(line, room)]
    parts: list[str] = []
    current: list[str] = []
    used = 0
    for atom in atoms:
        size = utf16_len(atom)
        if current and used + size > room:
            parts.append("".join(current))
            current, used = [], 0
        current.append(atom)
        used += size
    return parts + ["".join(current)] if current else parts


def pieces(name: str, text: str, capacity: int = CAPACITY) -> list[Piece]:
    """``text`` as one piece when it fits ``capacity``, else as labelled parts that do."""
    if not text:
        return []
    whole = Piece(name, 1, 1, text)
    if whole.size <= capacity:
        return [whole]
    room = capacity - Piece(name, _MOST_PARTS, _MOST_PARTS, "").size
    bodies = split(text, room)
    return [Piece(name, k, len(bodies), body) for k, body in enumerate(bodies, 1)]


def pack(found: list[Piece], capacity: int = CAPACITY) -> list[list[Piece]]:
    """First fit, largest piece first, so the tier takes few chunks and the same chunks
    every time. Within a chunk the pieces keep rule order."""
    packed: list[list[Piece]] = []
    used: list[int] = []
    for piece in sorted(found, key=lambda p: (-p.size, p.name, p.index)):
        # A linear search of the chunks opened so far: a handful of pieces into a
        # handful of chunks, so the whole pack stays trivially small.
        slot = next((i for i, u in enumerate(used) if u + piece.size <= capacity), len(packed))
        if slot == len(packed):
            packed.append([])
            used.append(0)
        packed[slot].append(piece)
        used[slot] += piece.size
    return [sorted(group, key=lambda p: (p.name, p.index)) for group in packed]


def _text(path: Path) -> str:
    """A rule's text; empty for git-crypt ciphertext, which carries nothing to deliver."""
    text, encrypted = load_text(path)
    return "" if encrypted else text


def chunks(cfg: dict, rules_dir: Path) -> list[str]:
    """Every chunk of the main tier under ``rules_dir``, each at most CHUNK_LIMIT."""
    found = [p for name in names(cfg, rules_dir) for p in pieces(name, _text(rules_dir / name))]
    packed = pack(found)
    return [chunk_header(number, len(packed)) + "".join(p.text for p in group)
            for number, group in enumerate(packed, 1)]


def chunk(cfg: dict, rules_dir: Path, index: int) -> str:
    """Chunk ``index``, counted from 0, or an empty string when the tier has no such chunk."""
    found = chunks(cfg, rules_dir)
    return found[index] if 0 <= index < len(found) else ""
