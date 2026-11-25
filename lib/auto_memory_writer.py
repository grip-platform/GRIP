"""Auto-memory writer — persists a ScoredInsight to three places atomically.

Writes:
1. ``memory/<type>_<slug>.md`` — the full memory file with YAML frontmatter
2. ``memory/MEMORY.md`` — one-line hook appended to the index
3. ``learning/insights.jsonl`` — machine-readable record that ``kono_auto_capture``
   will later route into the KONO substrate

Safety invariants:
- ``fcntl.flock`` held during MEMORY.md update (prevents concurrent-session
  interleave from two Claude processes appending simultaneously)
- Atomic file creation via ``tempfile`` + ``os.replace`` (rename is atomic on
  POSIX, so the file either exists fully or not at all)
- Idempotent: if the target .md already exists, skip (dedup is the caller's
  job via ``insight_value_scorer.load_existing_fingerprints``)
- Best-effort: all exceptions logged, never raised — auto-memory must never
  break a session end
- Budget-aware: an append that leaves ``MEMORY.md`` over the harness load
  budget is REPORTED (an error log plus ``PersistResult.memory_md_over_budget``)
  instead of succeeding silently. An over-budget index is truncated on the next
  session load, so the insight is written but never read again — the failure
  hides itself, which is why the write reports it rather than the reader
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from lib.insight_value_scorer import ScoredInsight
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

# The harness warns at ~200 chars per index line and silently truncates the
# whole index past its load budget, so the cap is the load-bearing constant.
INDEX_TITLE_CHARS = 60
INDEX_ENTRY_CAP = 200

logger = logging.getLogger(__name__)

# Claude Code truncates MEMORY.md on session load at ~24.4 KiB of CHARACTERS
# (24.4 * 1024 = 24_986). This writer appends a pointer line per captured
# insight, so it is the per-session growth surface for the index.
#
# It is NOT the only writer of the index. Re-checked 2026-09-19 by finding the
# real write sites rather than trusting the line numbers previously recorded here,
# which had all drifted:
#   lib/broly/ltm.py            NO LONGER an index writer. It appends "### key"
#                               blocks to its own _ltm_store and MIGRATED the
#                               legacy ones OUT of MEMORY.md. Listing it here was
#                               stale.
#   lib/remember.py             appends a pointer (operator-invoked) — NOW CAPPED
#                               at 200 chars, same shape as format_index_entry.
#   lib/session_steer.py        rewrites a "## TODOs" section (operator-invoked);
#                               uncapped, but human-paced and bounded in practice.
#   lib/unified_memory_brief.py rewrites the brief, capped by LINE count — the
#                               WRONG dimension for this failure.
# unified_memory_brief's line cap is the WRONG dimension for this failure: the
# comment in lib/grip_doctor.py:218 records a 195-line / 46k-char index being
# truncated, i.e. under any line limit and far over the character budget.
# Extending the signal to those writers is deliberately left out of scope here.
#
# Keep this number in sync with ``_HARNESS_INDEX_CHAR_BUDGET`` in
# lib/grip_doctor.py:224 and ``HARNESS_CHAR_BUDGET`` in lib/memory-guard.sh:75.
# The value is duplicated rather than imported on purpose: grip_doctor.py uses
# flat sys.path imports (``from telemetry_field_health import ...``) while this
# module and its caller (hooks/auto-memory-hook.py, which puts only the repo
# root on sys.path) use package imports, so ``from lib.grip_doctor import ...``
# raises ModuleNotFoundError in the real hook path and would silently disable
# this check. A duplicated literal that works beats an import that does not.
_HARNESS_INDEX_CHAR_BUDGET = 24_986

# Approach band derived FROM the budget (85%), never a second literal, so a
# future budget change cannot desync the two thresholds.
_HARNESS_INDEX_APPROACH_RATIO = 0.85

# `name` and `description` carry arbitrary Insight-block prose, so they are
# emitted as JSON strings rather than interpolated bare. YAML 1.2 is a superset
# of JSON, so a json.dumps() result is a valid double-quoted scalar with every
# quote, backslash, colon and newline already escaped.
#
# Interpolating them bare produced UNPARSEABLE frontmatter whenever the prose
# contained ": " (YAML reads it as a nested mapping) or opened with a quote or
# backtick (YAML starts a quoted scalar and never finds its end). Measured
# 2026-09-17: 42 of 598 memory records had invalid frontmatter, essentially all
# of them auto-captured, and `binding_of` raises on every one -- so an
# auto-captured record could never be read by any frontmatter-driven tool.
_FRONTMATTER = """---
name: {name}
description: {description}
type: {memory_type}
source: auto-captured from ★ Insight block
captured_at: {timestamp}
score: {score}
fingerprint: {fingerprint}
---

{content}
"""


@dataclass(frozen=True)
class PersistResult:
    saved: bool
    md_path: Path | None
    memory_md_updated: bool
    insights_jsonl_appended: bool
    reason: str
    # True when the append left MEMORY.md over the harness load budget, i.e.
    # the index will be silently truncated on the next session load. Defaults
    # to False so the field is additive for every existing caller.
    memory_md_over_budget: bool = False


def _memory_filename(scored: ScoredInsight) -> str:
    """Build a unique filename like ``reference_fallback_abc12345.md``."""
    return f"{scored.memory_type}_{scored.slug}_{scored.fingerprint[:8]}.md"


def _write_md_file(scored: ScoredInsight, target: Path) -> bool:
    """Atomic write of the memory file. Returns True on success."""
    body = _FRONTMATTER.format(
        name=json.dumps(scored.hook[:60]),
        description=json.dumps(scored.hook),
        memory_type=scored.memory_type,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        score=f"{scored.score:.2f}",
        fingerprint=scored.fingerprint,
        content=scored.content,
    )
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=".auto_memory_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fp:
            fp.write(body)
        os.replace(tmp_path, target)
        return True
    except OSError as exc:
        logger.error("auto_memory_writer: failed to write %s: %s", target, exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


def format_index_entry(md_filename: str, hook: str, cap: int = INDEX_ENTRY_CAP) -> str:
    """One index line: a short title, then only what the title did not already say.

    The previous form was ``f"- [{hook[:60]}](./{f}) — {hook}"``, which capped the
    LINK TITLE at 60 characters and then repeated the WHOLE hook after the dash. The
    first 60 characters therefore appeared twice and the line grew without bound --
    measured 2026-09-19, the index stood at 29,804 bytes against a 24,400-byte load
    budget, and its last 20 pointers reached no session at all. The harness says the
    quiet part itself: "Keep index entries to one line under ~200 chars; move detail
    into topic files."

    So the tail starts where the title stopped, and the whole line is capped. A
    pointer's job is to be findable, not to restate the record it points at.
    """
    # Collapse whitespace FIRST. Insight-block prose carries newlines, and a raw
    # newline here splits one pointer into two lines -- the remainder then reads as
    # an orphan bullet in the index. The previous format interpolated the hook raw
    # and had the same defect.
    hook = " ".join(hook.split())
    title = hook[:INDEX_TITLE_CHARS].rstrip()
    prefix = f"- [{title}](./{md_filename})"
    tail = hook[len(title):].strip(" —-—")
    if not tail:
        return prefix + "\n"
    room = cap - len(prefix) - len(" — ") - 1
    if room <= 0:
        return prefix + "\n"
    if len(tail) > room:
        tail = tail[: max(room - 1, 0)].rstrip() + "…"
    return f"{prefix} — {tail}\n"


def _append_memory_md_entry(
    memory_md: Path, md_filename: str, hook: str
) -> bool:
    """Append a one-line pointer to MEMORY.md under an advisory lock."""
    entry = format_index_entry(md_filename, hook)
    try:
        memory_md.parent.mkdir(parents=True, exist_ok=True)
        with open(memory_md, "a+", encoding="utf-8") as fp:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
                fp.write(entry)
                fp.flush()
                os.fsync(fp.fileno())
            finally:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        return True
    except OSError as exc:
        logger.error("auto_memory_writer: MEMORY.md append failed: %s", exc)
        return False


def _check_index_budget(memory_md: Path) -> bool:
    """Measure MEMORY.md against the harness load budget after an append.

    Returns True when the index is OVER budget — meaning the harness will
    truncate it on the next session load and the entries past the cut are
    written but never read. Logs an error when over budget and a warning in
    the approach band.

    Read-only and best-effort: an unreadable index reports False (not over
    budget) and logs the reason, so a measurement failure can never be
    mistaken for a clean measurement, nor break a session end.
    """
    try:
        char_count = len(memory_md.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.error(
            "auto_memory_writer: could not measure MEMORY.md against the "
            "%d-char load budget: %s", _HARNESS_INDEX_CHAR_BUDGET, exc
        )
        return False

    if char_count > _HARNESS_INDEX_CHAR_BUDGET:
        logger.error(
            "auto_memory_writer: MEMORY.md is at %d chars, OVER the %d-char "
            "harness load budget — the index is now SILENTLY TRUNCATED on "
            "session load and appended entries past the cut will never be "
            "read. Shorten entries to one line (< ~200 chars) and move detail "
            "into the linked topic files, or archive older entries.",
            char_count, _HARNESS_INDEX_CHAR_BUDGET,
        )
        return True

    approach = int(_HARNESS_INDEX_CHAR_BUDGET * _HARNESS_INDEX_APPROACH_RATIO)
    if char_count > approach:
        logger.warning(
            "auto_memory_writer: MEMORY.md is at %d chars, approaching the "
            "%d-char harness load budget (%d chars of headroom left).",
            char_count, _HARNESS_INDEX_CHAR_BUDGET,
            _HARNESS_INDEX_CHAR_BUDGET - char_count,
        )
    return False


def _append_insights_jsonl(
    insights_jsonl: Path, scored: ScoredInsight, md_path: Path
) -> bool:
    """Append a JSONL record so ``kono_auto_capture.py`` can route to KONO."""
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "auto_memory_writer",
        "context": "insight_block_capture",
        "text": scored.content,
        "memory_type": scored.memory_type,
        "md_path": str(md_path),
        "fingerprint": scored.fingerprint,
        "learning_score": round(scored.score, 3),
        "reasons": list(scored.reasons),
        "tags": ["auto-captured", "insight-block", scored.memory_type],
    }
    try:
        insights_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with open(insights_jsonl, "a", encoding="utf-8") as fp:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
                fp.write(json.dumps(record) + "\n")
                fp.flush()
                os.fsync(fp.fileno())
            finally:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        return True
    except OSError as exc:
        logger.error("auto_memory_writer: insights.jsonl append failed: %s", exc)
        return False


def _mesh_broadcast(scored: ScoredInsight) -> None:
    """Real-time cross-machine propagation of a locally-captured insight.

    Fail-soft: any error (mesh absent, import failure) leaves local capture
    untouched. See lib/mesh_insight_bridge.py.
    """
    try:
        from mesh_insight_bridge import broadcast_insight
        broadcast_insight(
            text=scored.content,
            tags=["auto-captured", getattr(scored, "memory_type", "insight")],
            fingerprint=getattr(scored, "fingerprint", None),
        )
    except Exception:
        pass


def persist_insight(
    scored: ScoredInsight,
    memory_dir: Path,
    insights_jsonl: Path,
) -> PersistResult:
    """Persist a save-worthy insight to all three sinks. Best-effort."""
    if not scored.should_save:
        return PersistResult(
            saved=False, md_path=None,
            memory_md_updated=False, insights_jsonl_appended=False,
            reason="below_threshold",
        )

    memory_dir.mkdir(parents=True, exist_ok=True)
    md_filename = _memory_filename(scored)
    md_path = memory_dir / md_filename

    if md_path.exists():
        return PersistResult(
            saved=False, md_path=md_path,
            memory_md_updated=False, insights_jsonl_appended=False,
            reason="already_exists",
        )

    md_written = _write_md_file(scored, md_path)
    if not md_written:
        return PersistResult(
            saved=False, md_path=None,
            memory_md_updated=False, insights_jsonl_appended=False,
            reason="md_write_failed",
        )

    memory_md = memory_dir / "MEMORY.md"
    memory_md_updated = _append_memory_md_entry(
        memory_md, md_filename, scored.hook
    )
    # The insight is still written when the index goes over budget — dropping
    # it would lose the insight outright, which is worse than an index that
    # needs pruning. What changes is that the overflow is now REPORTED.
    over_budget = _check_index_budget(memory_md) if memory_md_updated else False
    insights_appended = _append_insights_jsonl(
        insights_jsonl, scored, md_path
    )
    if insights_appended:
        _mesh_broadcast(scored)
    return PersistResult(
        saved=True, md_path=md_path,
        memory_md_updated=memory_md_updated,
        insights_jsonl_appended=insights_appended,
        reason="ok",
        memory_md_over_budget=over_budget,
    )
