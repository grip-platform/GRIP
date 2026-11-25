"""Closed-loop convergence memory — the lessons substrate.

A convergence loop that forgets what each wave learned is open-loop: it
re-discovers the same fix every run. This module is the *closed* loop's
memory. Each wave appends a structured lesson; the next wave loads the
**folded** view of prior lessons for the same target and threads it into
its own reasoning. Learning from the previous loop, applied in the next —
the charter's "automatic learning ... applied in CLOSED-LOOP".

The fold (the Banach anchor)
----------------------------
``fold_text`` is a deterministic reducer over the raw JSONL: it dedupes by
a semantic key (normalised lesson text hash, newest-wins by file position),
bounds each target to the newest ``MAX_LESSONS_PER_TARGET``, and re-emits
the survivors in stable order with canonical (sort-keys) serialisation.

It is **idempotent and byte-identical on refold**: ``fold_text(fold_text(x))
== fold_text(x)``. This is exactly the contraction property
``lib/holistic_synthesiser.py`` relies on (``payload_digest`` there is the
same idea) — folding an already-folded substrate is a fixed point. Tested
explicitly as the Banach anchor.

Design (mirrors holistic_synthesiser)
-------------------------------------
* **Pure over content** — the fold's output never depends on wall-clock.
  ``ts`` is carried through as data; ordering is by file position, so the
  same input bytes always produce the same output bytes.
* **Append is pure-append** — no read-modify-write on the hot path (no torn
  files). Compaction is an explicit, atomic, idempotent ``compact_file``
  (``os.replace``, the holistic pattern).
* **stdlib only · DIP** — the lessons path is injected; ``default_path``
  resolves the real one (path-portable).

Public API::

    from lib.converge_lessons import append_lesson, load_lessons

    append_lesson("voice-w1", "voice-hardening", depth=1,
                  outcome="passed", lesson="tts seam is DIP-injected ...",
                  falsifier="wrong if the seam ...")
    prior = load_lessons("voice-hardening")   # folded view (<=40, deduped)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Path-portability shim (rules/path-portability.md).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

_GRIP_HOME = get_claude_dir()

#: Per-target bound. A wave threads at most this many prior lessons — enough
#: signal, bounded context. The 41st lesson folds the oldest out.
MAX_LESSONS_PER_TARGET = 40

#: Fields a lesson record carries. ``key`` + ``ts`` are derived/stamped by
#: ``append_lesson``; the rest are caller-supplied.
_FIELDS = ("wave_id", "target", "depth", "outcome", "lesson", "falsifier", "ts", "key")

_WS = re.compile(r"\s+")


def semantic_key(lesson: str) -> str:
    """Stable dedupe key = hash of the normalised lesson text.

    Normalisation (lowercase + whitespace-collapse + strip) means two waves
    that learned the *same thing* phrased with incidental spacing/case
    differences collapse to one entry (newest-wins), while genuinely
    different lessons keep distinct keys.
    """
    normalised = _WS.sub(" ", lesson.lower()).strip()
    return hashlib.sha256(normalised.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True)
class _Record:
    """An immutable parsed lesson row, retaining its raw dict for round-trip."""

    target: str
    key: str
    data: dict

    def canonical(self) -> str:
        """Byte-stable serialisation — sort_keys makes refold a fixed point."""
        return json.dumps(self.data, sort_keys=True, ensure_ascii=False)


def _coerce(raw: dict) -> _Record | None:
    """A raw dict → ``_Record``, or ``None`` if it is not a usable lesson.

    Tolerant by construction: a row missing ``target`` or ``lesson`` is
    skipped (never crashes the fold), and ``key`` is recomputed from the
    lesson so a hand-edited file self-heals to canonical keys.
    """
    target = raw.get("target")
    lesson = raw.get("lesson")
    if not isinstance(target, str) or not isinstance(lesson, str) or not target:
        return None
    data = {k: raw.get(k) for k in _FIELDS}
    data["target"] = target
    data["lesson"] = lesson
    data["key"] = semantic_key(lesson)  # self-healing canonical key
    return _Record(target=target, key=data["key"], data=data)


def _parse(raw_text: str) -> list[_Record]:
    """Parse JSONL → records, skipping blank/malformed/non-lesson lines."""
    out: list[_Record] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            rec = _coerce(obj)
            if rec is not None:
                out.append(rec)
    return out


def _dedupe_keep_last(records: list[_Record]) -> list[_Record]:
    """Newest-wins dedupe by key, preserving the survivors' stable order.

    Single map pass records each key's last index; a single forward pass
    emits a record only at that index — O(n), order-stable, no nesting.
    Idempotent: applied to already-unique input it is the identity.
    """
    last_index: dict[str, int] = {}
    for i, rec in enumerate(records):
        last_index[rec.key] = i
    return [rec for i, rec in enumerate(records) if last_index[rec.key] == i]


def _bound_per_target(records: list[_Record], limit: int) -> list[_Record]:
    """Keep only the newest ``limit`` records per target, stable order.

    'Newest' = latest file position. One reverse pass tallies per-target and
    marks keepers; one forward comprehension emits them — O(n), no nesting.
    Idempotent: re-applying to an already-bounded list changes nothing.
    """
    seen: dict[str, int] = {}
    keep = [False] * len(records)
    for i in range(len(records) - 1, -1, -1):
        t = records[i].target
        if seen.get(t, 0) < limit:
            seen[t] = seen.get(t, 0) + 1
            keep[i] = True
    return [rec for i, rec in enumerate(records) if keep[i]]


def _fold_records(records: list[_Record], *, limit: int) -> list[_Record]:
    """The fold: dedupe newest-wins, then bound per target. Order-stable."""
    return _bound_per_target(_dedupe_keep_last(records), limit)


def fold_text(raw_text: str, *, limit: int = MAX_LESSONS_PER_TARGET) -> str:
    """Reduce raw JSONL to its canonical folded form (the Banach anchor).

    Idempotent + byte-identical on refold: ``fold_text(fold_text(x)) ==
    fold_text(x)``. Empty in ⇒ empty out (the empty file is its own fixed
    point). A trailing newline is present iff there is at least one record,
    so the canonical form is unambiguous.
    """
    folded = _fold_records(_parse(raw_text), limit=limit)
    if not folded:
        return ""
    return "\n".join(rec.canonical() for rec in folded) + "\n"


def load_lessons(
    target: str,
    *,
    path: Path | None = None,
    limit: int = MAX_LESSONS_PER_TARGET,
) -> list[dict]:
    """Return the folded lesson view for ``target`` — what a wave threads in.

    Folded = deduped (newest-wins) + bounded (newest ``limit``), in stable
    order. Returns the lesson dicts (not ``_Record``) so the runner and the
    executor consume plain data. Missing file ⇒ empty list (no error).
    """
    path = path or default_path()
    if not path.exists():
        return []
    records = [r for r in _parse(path.read_text(encoding="utf-8", errors="replace"))
               if r.target == target]
    return [rec.data for rec in _fold_records(records, limit=limit)]


def append_lesson(
    wave_id: str,
    target: str,
    depth: int,
    outcome: str,
    lesson: str,
    falsifier: str = "",
    *,
    path: Path | None = None,
    ts: str | None = None,
) -> Path:
    """Append one lesson row to the JSONL (pure-append, no rewrite).

    ``ts`` defaults to a UTC ISO timestamp (data only — never affects the
    fold's byte-identity, which orders by file position). Returns the path.
    """
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "wave_id": wave_id,
        "target": target,
        "depth": int(depth),
        "outcome": outcome,
        "lesson": lesson,
        "falsifier": falsifier,
        "ts": ts or _utc_now(),
        "key": semantic_key(lesson),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def compact_file(path: Path | None = None, *, limit: int = MAX_LESSONS_PER_TARGET) -> bool:
    """Atomically rewrite the file to its folded form (the holistic pattern).

    Compare-and-skip: if the file already equals its fold, do nothing and
    return ``False`` (idempotent no-op). Otherwise write the fold via
    ``os.replace`` (atomic — readers see old-or-new, never torn) and return
    ``True``. Optional maintenance; the hot path stays pure-append.
    """
    path = path or default_path()
    if not path.exists():
        return False
    current = path.read_text(encoding="utf-8", errors="replace")
    folded = fold_text(current, limit=limit)
    if folded == current:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(folded, encoding="utf-8")
    os.replace(tmp, path)
    return True


def default_path() -> Path:
    """Resolve the canonical lessons file (path-portable, gitignored state)."""
    return _GRIP_HOME / "state" / "converge" / "lessons.jsonl"


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
