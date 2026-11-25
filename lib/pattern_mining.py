"""Deterministic floor for session pattern mining — the part that needs no model.

GRIP's learning problem is not mainly that it fails to learn. Measured 2026-09-01: 221
feedback records exist and 182 of them (82%) have no pointer in MEMORY.md. Since a
dispatched subagent inherits MEMORY.md and the safety core and nothing else
(rules/binding-memory-records.md), those 182 lessons are written down and structurally
unable to fire. A miner that only hunted NEW patterns would add to a pile that is already
mostly unreachable.

So findings sort into three classes, and only one of them needs a language model:

    A  genuinely new      recurs across sessions, no memory covers it   -> model judges
    B  recurs DESPITE a memory   the lesson exists and still failed      -> model judges
    C  learned but unreachable   the record has no index pointer         -> ARITHMETIC

Class C is a set difference. It is computed here, exactly, with no model and no cost.
Class B needs a model for the judgement, but this module narrows the search first, so the
expensive lane only reads turns that carry a correction signal
(rules/llm-augmentation-pattern.md: deterministic floor, model above it).

WHAT THE FRICTION SCAN IS AND IS NOT. `friction_turns` is a PRE-FILTER, never a verdict.
It selects operator turns that look like a correction so a downstream agent reads a few
hundred turns instead of a few hundred thousand. It will over-select — "no" appears in
plenty of turns that correct nothing — and that is the intended direction of error, since
a missed correction is invisible while a false one is discarded by the reader. Never
report its count as a number of real frictions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# A record is referenced from the index either as a markdown link or as a bare `:: stem`
# trailer. Both forms are live in MEMORY.md today, and the link form appears with and
# without a leading "./", so the stem is captured and normalised rather than matched raw.
_LINK_POINTER = re.compile(r"\]\(\.?/?([A-Za-z0-9_.-]+)\.md\)")
_TRAILER_POINTER = re.compile(r"::\s*([A-Za-z0-9_-]+)")

# Openers that mark an operator turn as a correction, redirect or repeat. Deliberately
# broad: this narrows a corpus, it does not judge one.
_FRICTION_MARKERS = (
    "no,", "no ", "nope", "don't", "do not", "stop", "wrong", "incorrect",
    "actually", "i said", "as i said", "again", "you didn't", "you did not",
    "never ", "not what", "that's not", "thats not", "instead", "rather",
    "why did you", "you should have", "revert", "undo", "i asked",
)

_FRICTION_RE = re.compile("|".join(re.escape(m) for m in _FRICTION_MARKERS), re.IGNORECASE)

# Records that are indexes or archives themselves, not lessons that need a pointer.
_NON_RECORD_STEMS = ("MEMORY", "README", "CLAUDE")


def normalise_stem(stem: str) -> str:
    """Fold the spellings the corpus uses interchangeably (hyphen vs underscore, case).

    `reference-shared-constant-tautology` and `reference_shared_constant_tautology` name
    the same record; both forms are present in MEMORY.md today, so a raw string compare
    reports live records as unreachable.
    """
    return stem.strip().lower().replace("-", "_")


def index_pointers(index_text: str) -> set[str]:
    """Every record stem the index points at, normalised."""
    found = _LINK_POINTER.findall(index_text) + _TRAILER_POINTER.findall(index_text)
    return {normalise_stem(s) for s in found}


def _is_record(path: Path) -> bool:
    """A lesson file, as opposed to the index or an archive of it."""
    stem = path.stem
    if stem.startswith(_NON_RECORD_STEMS):
        return False
    return path.suffix == ".md"


def unreachable_records(memory_dir: Path, *, index_name: str = "MEMORY.md") -> list[Path]:
    """Class C: records on disk that the index never points at.

    Pointers are counted from the index ONLY. A record referenced solely from an archive
    file is still unreachable, because an archive is not inherited by a dispatched agent —
    which is the whole property that makes this class matter.
    """
    index = memory_dir / index_name
    if not index.is_file():
        # Never return "nothing unreachable" for a missing index: that reports the
        # healthiest possible answer from the least possible evidence.
        raise FileNotFoundError(f"no index at {index}")
    pointers = index_pointers(index.read_text(errors="replace"))
    records = sorted(p for p in memory_dir.glob("*.md") if _is_record(p))
    return [p for p in records if normalise_stem(p.stem) not in pointers]


def reachability_report(memory_dir: Path, *, index_name: str = "MEMORY.md") -> dict:
    """Counts plus the unreachable list, so a caller can act without re-globbing."""
    missing = unreachable_records(memory_dir, index_name=index_name)
    total = sum(1 for p in memory_dir.glob("*.md") if _is_record(p))
    return {
        "memory_dir": str(memory_dir),
        "records": total,
        "unreachable": len(missing),
        "reachable": total - len(missing),
        "share_unreachable": round(len(missing) / total, 3) if total else 0.0,
        "unreachable_records": [p.name for p in missing],
    }


def _turn_text(row: dict) -> str:
    """Flatten one transcript row's message content to plain text."""
    message = row.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(parts)


def _is_operator_turn(row: dict) -> bool:
    """A real operator turn: role user, and not a tool result or harness injection."""
    if row.get("type") != "user":
        return False
    message = row.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        if "tool_result" in kinds:
            return False
    return bool(_turn_text(row).strip())


def friction_turns(rows: list[dict], *, max_chars: int = 600) -> list[dict]:
    """Operator turns carrying a correction signal — a PRE-FILTER, never a verdict.

    Over-selection is intended: a missed correction is invisible, a false one is discarded
    by whoever reads it. Do not report the length of this list as a count of frictions.
    """
    hits = []
    for i, row in enumerate(rows):
        if not _is_operator_turn(row):
            continue
        text = _turn_text(row).strip()
        if not _FRICTION_RE.search(text):
            continue
        hits.append({
            "index": i,
            "timestamp": row.get("timestamp", ""),
            "text": text[:max_chars],
        })
    return hits


def load_transcript(path: Path) -> list[dict]:
    """Parse a session transcript, skipping malformed lines rather than aborting."""
    rows: list[dict] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def n_count(observations: list[dict], key: str = "pattern") -> dict[str, int]:
    """Count DISTINCT sessions per pattern, which is what the N=2 threshold means.

    Three mentions inside one session is one observation, not three
    (rules/knowledge-maturation-functor.md). Counting rows instead of sessions is the
    easiest way to manufacture an N that clears the bar without the evidence to match:
    at N=2, one conversation that repeats itself once would be enough.
    """
    seen: dict[str, set[str]] = {}
    for obs in observations:
        name = obs.get(key)
        session = obs.get("session")
        if not name or not session:
            continue
        seen.setdefault(name, set()).add(session)
    return {name: len(sessions) for name, sessions in seen.items()}
