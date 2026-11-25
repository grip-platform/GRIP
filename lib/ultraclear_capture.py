#!/usr/bin/env python3
"""ULTRACLEAR capture+persistence FOUNDATION — the transcript front-end + on-disk
checkpoint layer for the "auto HOT-safe /clear" feature.

`lib/ultraclear.py` is the PURE planner (WHAT to keep: HOT/COLD/FOLDED).
`lib/ultraclear_session.py` turns a plan into a JSON-serialisable checkpoint dict
but explicitly leaves persistence to the caller ("The caller owns persistence").
THIS module is that caller: it (1) reads a live Claude Code session JSONL into
ordered `Turn`s, (2) writes the checkpoint dict atomically under
``state/ultraclear/`` + a per-project ``latest-*`` pointer, (3) loads the latest
fresh, other-session checkpoint, and (4) decides when a fresh checkpoint is due.

Design contract (mirrors hooks/focus-cards-hook.py conventions):
  * FAIL-SAFE — every public entry is wrapped so a missing/garbage transcript or a
    corrupt on-disk file yields an empty/None result, NEVER an exception. A hook
    built on these helpers can never crash the session.
  * DETERMINISTIC — the wall-clock is INJECTED (`now_iso`), never read here, so the
    hook passes real UTC at runtime and tests pass a fixed instant.
  * ATOMIC writes — tmp file in the same dir + ``os.replace`` (crash-safe).
  * PURE STDLIB — only json/os/re/sys/tempfile/datetime/pathlib plus the local
    ultraclear* / claude_dir shims (themselves stdlib-only). No network, no SDK.

The four public functions: `build_turns_from_transcript`, `write_checkpoint`,
`load_latest_checkpoint`, `should_checkpoint` (+ the `project_slug` helper that
both `write_checkpoint` and a calling hook use to address the per-project pointer).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402
from ultraclear import Turn, UltraclearConfig  # noqa: E402
from ultraclear_session import checkpoint as _uc_checkpoint  # noqa: E402

# Planner defaults shared with the front-end so "the most-recent keep_recent turns"
# here means exactly what the planner's recency rule means.
_CONFIG = UltraclearConfig()

# should_checkpoint thresholds (documented, mutation-pinned by tests).
_TURN_DELTA = 8
_TOKEN_DELTA = 15_000
_TIME_DELTA_S = 600

# Only conversational message lines become turns; metadata lines (summary/mode/
# last-prompt/system/...) and subagent sidechains are skipped.
_MESSAGE_TYPES = {"user", "assistant"}

# Operator-directive / decision heuristic (documented): a turn is PINNED — kept HOT
# regardless of age — if it carries an operator signature (``V>>`` / ``A>>`` ...) or
# strong decision language. Deliberately conservative so ordinary recent turns stay
# the COLD-able tail and the HOT manifest does not swallow the whole window.
_SIGNATURE_RE = re.compile(r"\b[A-Z]>>")
_DIRECTIVE_RE = re.compile(
    r"\b(decided|decision|directive|paramount|let'?s|we\s+will|going\s+with|"
    r"must|never|always|ship\s+it)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# 1. build_turns_from_transcript
# --------------------------------------------------------------------------

def _extract_text(message: dict) -> str:
    """Canonical visible-text extraction (mirrors lib/compaction_persist.py): a
    string content is taken verbatim; a block list drops ``thinking`` blocks and
    concatenates each block's ``text`` (so pure tool_use / tool_result / thinking
    turns yield "" and are dropped by the caller)."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") != "thinking"
    ]
    return "\n".join(p for p in parts if p)


def _parse_line(line: str) -> Optional[dict]:
    """Parse one JSONL line into a message record, or None to skip it (bad JSON,
    non-message type, or a subagent sidechain)."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") not in _MESSAGE_TYPES:
        return None
    if obj.get("isSidechain") is True:
        return None
    if obj.get("type") == "assistant" and obj.get("isApiErrorMessage") is True:
        return None
    return obj


def _iter_message_records(path: Path) -> Iterable[dict]:
    """Yield message records from a session JSONL, one per retained line."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = _parse_line(line)
            if record is not None:
                yield record


def _record_to_fields(obj: dict, index: int) -> Optional[dict]:
    """Project a message record to verbatim turn fields, or None if it carries no
    visible text. ``uuid`` is used verbatim as the turn id (synthesised only if a
    line is missing one)."""
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    text = _extract_text(message)
    if not text:
        return None
    return {
        "id": str(obj.get("uuid") or f"turn-{index}"),
        "role": str(message.get("role") or obj.get("type") or "user"),
        "content": text,
        "tokens": max(1, len(text) // 4),
    }


def _collect_fields(path: Path) -> list[dict]:
    """Ordered, text-bearing turn fields parsed from the transcript."""
    fields: list[dict] = []
    for obj in _iter_message_records(path):
        row = _record_to_fields(obj, len(fields))
        if row is not None:
            fields.append(row)
    return fields


def _is_directive(text: str) -> bool:
    """True for an operator-signature or strong-decision turn (pin heuristic)."""
    return bool(_SIGNATURE_RE.search(text) or _DIRECTIVE_RE.search(text))


def _fields_to_turns(fields: list[dict]) -> list[Turn]:
    """Build `Turn`s in chronological order. The most-recent ``keep_recent`` turns
    are PINNED — guaranteeing they land HOT and survive demotion (the HOT-safety
    the feature exists for) — as are any operator-directive/decision turns anywhere
    in history. ``in_kernel`` is False (folding detection is out of scope here)."""
    total = len(fields)
    keep = _CONFIG.keep_recent
    turns: list[Turn] = []
    for i, row in enumerate(fields):
        recent = i >= total - keep
        pinned = recent or _is_directive(row["content"])
        turns.append(
            Turn(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                tokens=row["tokens"],
                pinned=pinned,
                in_kernel=False,
            )
        )
    return turns


def _estimate_kernel_tokens() -> int:
    """Estimate holistic-kernel tokens (``rules/HOLISTIC.md`` len//4) for the
    planner's manifest budget. Fail-safe: missing kernel / any error -> 0."""
    try:
        kernel = get_claude_dir() / "rules" / "HOLISTIC.md"
        if not kernel.is_file():
            return 0
        return max(0, len(kernel.read_text(encoding="utf-8", errors="replace")) // 4)
    except Exception:
        return 0


def build_turns_from_transcript(transcript_path) -> tuple[list[Turn], int]:
    """Read a Claude Code session JSONL into ordered `Turn`s + a kernel-token
    estimate. Returns ``(turns, kernel_tokens)``. FAIL-SAFE: a missing or garbage
    transcript yields ``([], 0)`` and never raises."""
    try:
        path = Path(transcript_path)
        if not path.is_file():
            return [], 0
        fields = _collect_fields(path)
        if not fields:
            return [], 0
        return _fields_to_turns(fields), _estimate_kernel_tokens()
    except Exception:
        return [], 0


# --------------------------------------------------------------------------
# project slug + atomic IO
# --------------------------------------------------------------------------

def project_slug(cwd: Optional[str] = None) -> str:
    """Stable per-project slug from an absolute cwd (defaults to the live cwd),
    using GRIP's path-encoding convention ``/`` and ``.`` -> ``-`` (matching the
    ``projects/<slug>`` directory layout)."""
    base = cwd if cwd is not None else os.getcwd()
    return str(base).replace("/", "-").replace(".", "-")


def _state_dir() -> Path:
    """``<claude_dir>/state/ultraclear`` (created on demand)."""
    directory = get_claude_dir() / "state" / "ultraclear"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _safe_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Crash-safe write: serialise to a tmp file in the same dir, then atomically
    ``os.replace`` it over the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        _safe_unlink(tmp)
        raise


def _read_json(path: Path) -> Optional[dict]:
    """Read a JSON object, or None on missing/unreadable/non-object."""
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# 2. write_checkpoint
# --------------------------------------------------------------------------

def _write_pointer(state_dir: Path, session_id: str, cp_path: Path,
                   now_iso: str, checkpoint_dict: dict, slug: str) -> None:
    """Update the per-project ``latest-<slug>.json`` pointer. ``slug`` MUST be the
    same project slug the reading side (``load_latest_checkpoint``) will use —
    derived from the hook PAYLOAD cwd, never ``os.getcwd()``. A Stop hook whose
    process cwd differs from the session cwd (see project-cwd-hint.sh) would
    otherwise write the pointer under the wrong slug and the reload would find
    nothing. (Regression: ``test_cwd_mismatch_pointer_keyed_by_payload_cwd``.)"""
    pointer = {
        "session_id": session_id,
        "path": str(cp_path),
        "written_at": now_iso,
        "project": slug,
        "hot_turns": len(checkpoint_dict.get("hot", [])),
        "manifest_tokens": checkpoint_dict.get("econ", {}).get("manifest_tokens", 0),
    }
    _atomic_write_json(state_dir / f"latest-{slug}.json", pointer)


def write_checkpoint(turns, kernel_tokens, session_id, now_iso,
                     slug: Optional[str] = None) -> Optional[Path]:
    """Serialise `turns` via `ultraclear_session.checkpoint`, write the dict
    atomically to ``state/ultraclear/<session_id>.json``, and refresh the
    per-project pointer. ``slug`` keys that pointer: pass the slug derived from the
    hook PAYLOAD cwd (``project_slug(payload_cwd)``) so the writing and reading
    sides agree even when the hook process cwd differs from the session cwd.
    Defaults to ``project_slug()`` (live cwd) for direct callers/tests where the
    two coincide. Returns the checkpoint path, or None when there is nothing to
    persist (no turns, or an empty HOT manifest -> no-op). FAIL-SAFE: any error
    returns None without raising."""
    try:
        turns = list(turns)
        if not turns:
            return None
        checkpoint_dict = _uc_checkpoint(turns, kernel_tokens, now=now_iso)
        if not checkpoint_dict.get("hot"):
            return None
        state_dir = _state_dir()
        cp_path = state_dir / f"{session_id}.json"
        _atomic_write_json(cp_path, checkpoint_dict)
        _write_pointer(state_dir, session_id, cp_path, now_iso, checkpoint_dict,
                       slug if slug is not None else project_slug())
        return cp_path
    except Exception:
        return None


# --------------------------------------------------------------------------
# 3. load_latest_checkpoint
# --------------------------------------------------------------------------

def _parse_iso(value: str) -> Optional[datetime]:
    """ISO-8601 -> datetime (``Z`` normalised), or None on bad input."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_seconds(start_iso: str, end_iso: str) -> Optional[float]:
    """Seconds from start to end, or None if either fails to parse. Mixed
    aware/naive inputs are normalised to naive rather than raising."""
    start = _parse_iso(start_iso)
    end = _parse_iso(end_iso)
    if start is None or end is None:
        return None
    try:
        return (end - start).total_seconds()
    except TypeError:
        return (end.replace(tzinfo=None) - start.replace(tzinfo=None)).total_seconds()


def _read_pointer(slug: str) -> Optional[dict]:
    """The per-project pointer dict, or None if absent/corrupt."""
    return _read_json(get_claude_dir() / "state" / "ultraclear" / f"latest-{slug}.json")


def _pointer_is_loadable(pointer: dict, exclude_session: str,
                         ttl_seconds: float, now_iso: str) -> bool:
    """A pointer is loadable iff it is for a DIFFERENT session and was written
    within ``ttl_seconds`` of ``now_iso``."""
    if pointer.get("session_id") == exclude_session:
        return False
    elapsed = _elapsed_seconds(pointer.get("written_at", ""), now_iso)
    return elapsed is not None and 0 <= elapsed <= ttl_seconds


def load_latest_checkpoint(project_slug, *, exclude_session, ttl_seconds,
                           now_iso) -> Optional[dict]:
    """Return the latest checkpoint dict for ``project_slug`` ONLY when it was
    written within ``ttl_seconds``, belongs to a session other than
    ``exclude_session``, and carries a non-empty HOT manifest; else None.
    FAIL-SAFE: any error returns None without raising."""
    try:
        pointer = _read_pointer(project_slug)
        if pointer is None:
            return None
        if not _pointer_is_loadable(pointer, exclude_session, ttl_seconds, now_iso):
            return None
        checkpoint_dict = _read_json(Path(pointer.get("path", "")))
        if not (isinstance(checkpoint_dict, dict) and checkpoint_dict.get("hot")):
            return None
        return checkpoint_dict
    except Exception:
        return None


# --------------------------------------------------------------------------
# 4. should_checkpoint
# --------------------------------------------------------------------------

def should_checkpoint(last_iso, last_turns, last_tokens, cur_turns, cur_tokens,
                      now_iso) -> bool:
    """True iff a fresh checkpoint is due: at least ``_TURN_DELTA`` (8) new turns,
    OR ``_TOKEN_DELTA`` (15000) new tokens, OR ``_TIME_DELTA_S`` (600) seconds
    elapsed since ``last_iso``. An unparseable timestamp simply disables the time
    arm (turn/token arms still apply) — never raises."""
    if cur_turns - last_turns >= _TURN_DELTA:
        return True
    if cur_tokens - last_tokens >= _TOKEN_DELTA:
        return True
    elapsed = _elapsed_seconds(last_iso, now_iso)
    return elapsed is not None and elapsed >= _TIME_DELTA_S


# --------------------------------------------------------------------------
# 5. reap_stale_checkpoints — dead-record GC (rules/lifecycle-closed-or-expired.md)
# --------------------------------------------------------------------------

# 24h — deliberately MUCH longer than any reload TTL a caller passes to
# load_latest_checkpoint (the live one is 4h). A checkpoint older than this can
# therefore NEVER be reloaded — it is provably dead, not "pending". The reload TTL
# defines liveness; this reaps only what has fallen outside it (the lifecycle rule's
# "guaranteed closer OR a TTL" — this is the TTL + fail-closed-scope reaper).
_REAP_TTL_S = 86_400


def _is_checkpoint_file(path: Path) -> bool:
    """True for a session checkpoint ``<uuid>.json`` — NOT a ``latest-*`` pointer and
    NOT a ``.throttle-*`` / dotfile (both are live control state, never reaped)."""
    name = path.name
    return (
        name.endswith(".json")
        and not name.startswith("latest-")
        and not name.startswith(".")
    )


def _pointer_protected_names(state_dir: Path) -> set[str]:
    """Basenames every live ``latest-<slug>`` pointer still references — the current
    per-project checkpoints. NEVER reaped, regardless of age (fail-closed scope: only
    a provably-dead, unreferenced file is eligible). Basename compare avoids any
    absolute/relative path-normalisation mismatch."""
    protected: set[str] = set()
    for pointer in state_dir.glob("latest-*.json"):
        data = _read_json(pointer)
        target = data.get("path") if isinstance(data, dict) else None
        if target:
            protected.add(Path(str(target)).name)
    return protected


def _checkpoint_age_s(path: Path, now_iso: str) -> Optional[float]:
    """Seconds since the checkpoint's OWN ``created_at`` (deterministic — matches the
    injected write clock, unlike filesystem mtime which a touch could reset), or None
    when the file is unreadable / undated. An undatable file is NEVER reaped
    (conservative: we only delete what we can prove is old)."""
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    return _elapsed_seconds(str(data.get("created_at") or ""), now_iso)


def _log_reap_error(now_iso: str, exc: Exception) -> None:
    """Fail-open telemetry so a reap failure is visible, not silent (AGORA #3920
    DEFER). Never raises — a logging failure must not defeat the reaper's own
    never-crash contract."""
    try:
        log = get_claude_dir() / "logs" / "ultraclear-reap.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ts": now_iso, "error": str(exc)}) + "\n")
    except Exception:
        pass


def reap_stale_checkpoints(now_iso: str, ttl_seconds: float = _REAP_TTL_S) -> int:
    """Delete ultraclear checkpoints older than ``ttl_seconds`` EXCEPT any file a live
    ``latest-<slug>`` pointer still references. Returns the number reaped.

    Closes the accretion class (rules/lifecycle-closed-or-expired.md): auto-checkpoints
    are born open with no closer, so without this they accumulate forever — and 91/93
    were is_noop no-ops kept indefinitely under a 4h reload window. Age is read from
    each checkpoint's own ``created_at`` (deterministic via the injected ``now_iso``),
    NOT mtime. FAIL-SAFE: any per-file or top-level error is swallowed; a lost reap
    only means the next Stop retries — it can NEVER crash the hook or delete a live
    target."""
    reaped = 0
    try:
        state_dir = _state_dir()
        protected = _pointer_protected_names(state_dir)
        for path in state_dir.glob("*.json"):
            if not _is_checkpoint_file(path) or path.name in protected:
                continue
            age = _checkpoint_age_s(path, now_iso)
            if age is None or age <= ttl_seconds:
                continue
            try:
                path.unlink()
                reaped += 1
            except OSError:
                pass
    except Exception as exc:
        _log_reap_error(now_iso, exc)
    return reaped
