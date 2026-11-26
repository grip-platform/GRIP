#!/usr/bin/env python3
"""Auto-memory Stop hook — capture ★ Insight blocks from assistant output.

Runs at end of each assistant turn. Reads the session transcript, extracts
the last assistant message, harvests ★ Insight blocks, scores each for
save-worthiness, dedupes against existing memories, persists high-value
ones to:

1. ``projects/<project>/memory/reference_<slug>_<hash>.md``
2. ``projects/<project>/memory/MEMORY.md`` (one-line pointer)
3. ``learning/insights.jsonl`` (so ``kono_auto_capture.py`` routes to KONO)

Best-effort. Never blocks the session end. Exit code 0 always.

Claude Code Stop hook input (stdin):
    {"session_id": "...", "transcript_path": "/path/to/transcript.jsonl",
     "hook_event_name": "Stop", "stop_hook_active": false}

Performance budget: < 1 second total. Fast-path exits when no ★ substring.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude")
LOG_FILE = CLAUDE_DIR / "logs" / "auto-memory-hook.log"
INSIGHTS_JSONL = CLAUDE_DIR / "learning" / "insights.jsonl"
# Rejection log: every insight the scorer declined to save (enables recalibration).
REJECTION_LOG = CLAUDE_DIR / "logs" / "auto-memory-rejections.jsonl"

sys.path.insert(0, str(CLAUDE_DIR))

# The shared folder-name encoder ships in lib/ beside this hook. CLAUDE_DIR
# says where STATE lives and tests redirect it, so the import resolves from
# the hook's own tree first, as hooks/post-compact-hook.py does.
LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if not (LIB_DIR / "path_encoding.py").is_file():
    LIB_DIR = CLAUDE_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from path_encoding import encode_project_path  # noqa: E402

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("auto_memory_hook")


def _resolve_project_memory_dir() -> Path:
    """Map the working directory to its project memory folder.

    The folder name comes from lib/path_encoding.py, the encoder Claude Code's
    own folder names agree with, so ``~/.claude`` still becomes
    ``-Users-you--claude``. The hand-rolled slug it replaces turned only slashes
    and dots into dashes. That left a Windows path absolute, so pathlib dropped
    ``CLAUDE_DIR / "projects"`` and the memories landed in a stray folder in
    the home directory that nothing reads (GRIP#6821).
    """
    return CLAUDE_DIR / "projects" / encode_project_path(Path.cwd().resolve()) / "memory"


def _extract_text(content) -> str:
    """Extract plain text from a message content value (str or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _insight_text(raw: str) -> str | None:
    """The insight-bearing text of one transcript line, or None to skip it.

    Every way a single line can be malformed is absorbed here, so the caller's
    loop has nothing to catch. A JSONL line can be valid JSON without being an
    object -- `null`, `[1, 2]` and `"text"` all parse, and .get() on any of them
    raises AttributeError -- so the type guard keeps one corrupt line from
    crashing a hook whose contract is exit 0.
    """
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    text = _extract_text((entry.get("message") or {}).get("content"))
    return text if text and "★ Insight" in text else None


def _read_insight_bearing_text(transcript_path: str) -> str | None:
    """Collect the assistant messages that contain a literal "★ Insight" block.

    Named for what it does. The previous name and docstring both said "ALL
    assistant messages", which is not what the loop below does — it keeps only
    the messages carrying the marker. Measured directly on a 55MB transcript
    (2026-08-10): 4 of 1,501 assistant messages matched, so the filter discards
    99.7% of assistant text. Anyone reading the old name would reasonably have
    believed the whole transcript reached the scorer, and it does not.

    The scan itself deliberately spans the WHOLE transcript rather than the last
    assistant turn. An earlier implementation stopped at the previous user turn
    and so captured nothing from the bulk of a multi-turn session; the dedup
    fingerprint in _process() is what stops the wider scan re-saving the same
    insight on a later run.

    Returns None when no message carries the marker — an empty transcript and a
    transcript full of ordinary prose are indistinguishable here, which is the
    upstream narrowing any future non-marker producer will have to reckon with.
    """
    # Streamed, and decoded leniently, because these files are big: the six most
    # recent in this project measured 0.4-39.3 MB on 2026-08-10 and this hook
    # opens one every turn. Iterating the handle is constant-memory where
    # readlines() materialised every line to keep a handful. errors="replace"
    # then keeps one bad byte from costing the rest: a strict decode raises
    # DURING iteration, unwinding past the loop and discarding every insight
    # already collected. Replacement puts malformed UTF-8 on the same graceful
    # path malformed JSON always had — the line parses carrying a U+FFFD, or
    # fails to parse and is skipped (AGORA on #4527, filed as #4532).
    segments: list[str] = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fp:
            for raw in fp:
                text = _insight_text(raw.strip())
                if text:
                    segments.append(text)
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is named as a backstop, not because this reader can
        # still raise one: it is a ValueError rather than an OSError, so before
        # it was named here it escaped to main() and crashed the hook on any
        # invalid byte. Keeping it means no future strict read can quietly
        # reinstate that crash.
        logger.warning("could not read transcript %s: %s", transcript_path, exc)
        return None

    if not segments:
        return None
    return "\n\n".join(segments)


def _maybe_log_rejection(scored, rejection_log: Path) -> None:
    """Append every rejected insight to the log, so the gate can be recalibrated.

    Keyed to `should_save` alone, deliberately. The previous version also
    required `score >= 0.30`, written when the save threshold was 0.55. The
    threshold was later lowered 0.55 -> 0.45 -> 0.20 and this floor was left
    behind, so a record needed `score < 0.20 AND score >= 0.30` — an empty
    band. The log had never once fired. Any floor written as a literal can go
    stale the same way the next time the threshold moves, so there is no
    floor: if it was not saved, it is recorded.

    That matters most at the bottom of the range. Real insight text scores
    exactly 0.0 today, because the scorer's positive vocabulary is tuned to
    shell/CLI register and matches nothing in descriptive prose. A floor above
    zero would hide precisely the starvation this log exists to expose.
    """
    if scored.should_save:
        return
    rejection_log.parent.mkdir(parents=True, exist_ok=True)
    import time as _time
    record = {
        "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
        "score": round(scored.score, 4),
        "fingerprint": scored.fingerprint,
        "text_preview": scored.content[:200],
        "reasons": list(scored.reasons),
    }
    with rejection_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _process(text: str, memory_dir: Path) -> dict:
    """Harvest → score → dedup → persist → propagate. Return summary counts.

    The propagation step (Rule 20 closed-loop) classifies each insight and
    writes it to canonical target files (operational-context, skill files,
    memory files) based on category. This closes the auto-insight loop.
    """
    from lib.insight_harvester import harvest
    from lib.insight_value_scorer import (
        is_duplicate,
        load_existing_fingerprints,
        score,
    )
    from lib.auto_memory_writer import persist_insight

    insights = harvest(text)
    if not insights:
        return {"harvested": 0, "saved": 0, "skipped": 0, "propagated": 0}

    existing = load_existing_fingerprints(memory_dir)
    saved = 0
    propagated = 0
    skipped_below = 0
    skipped_dup = 0

    # Lazy import propagator (avoid import cost on fast-path sessions)
    propagator = None

    for ins in insights:
        scored = score(ins.content)
        if not scored.should_save:
            skipped_below += 1
            _maybe_log_rejection(scored, REJECTION_LOG)
            continue
        if is_duplicate(scored, existing):
            skipped_dup += 1
            continue
        result = persist_insight(scored, memory_dir, INSIGHTS_JSONL)
        if result.saved:
            saved += 1
            existing.add(scored.fingerprint)
            logger.info(
                "saved fp=%s type=%s score=%.2f reasons=%s",
                scored.fingerprint, scored.memory_type,
                scored.score, ",".join(scored.reasons),
            )

            # Rule 20: Auto-propagate to canonical files
            try:
                if propagator is None:
                    from lib.insight_propagator import propagate
                    propagator = propagate

                prop_result = propagator(ins.content)
                if prop_result.targets_written:
                    propagated += 1
                    logger.info(
                        "propagated fp=%s category=%s targets=%d",
                        prop_result.fingerprint,
                        prop_result.category,
                        len(prop_result.targets_written),
                    )
            except Exception as exc:
                # Propagation failure is non-fatal — insight already saved
                logger.warning("propagation failed: %s", exc)

    return {
        "harvested": len(insights),
        "saved": saved,
        "propagated": propagated,
        "skipped_below_threshold": skipped_below,
        "skipped_duplicate": skipped_dup,
    }


def main() -> int:
    """Stop hook entry. Always returns 0 (best-effort)."""
    t0 = time.perf_counter()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0

    # Best-effort contract: nothing this function calls may prevent exit 0.
    # The reader handles its own I/O and decode errors; this catches anything
    # it does not anticipate rather than letting it reach the caller.
    try:
        text = _read_insight_bearing_text(transcript_path)
    except Exception as exc:
        logger.exception("auto-memory-hook transcript read failed: %s", exc)
        return 0
    if not text or "★ Insight" not in text:
        return 0

    memory_dir = _resolve_project_memory_dir()
    try:
        summary = _process(text, memory_dir)
    except Exception as exc:
        logger.exception("auto-memory-hook error: %s", exc)
        return 0

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "tick session=%s elapsed_ms=%.0f summary=%s",
        payload.get("session_id", "?")[:8], elapsed_ms, summary,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
