"""Congruent Continuity — deterministic trajectory features (L1 substrate).

Extracts a fixed vector of trajectory features from a session transcript
(``projects/<cwd-slug>/<session-uuid>.jsonl``) plus, optionally, a continuity
chain file. Every feature is computed from ARTEFACTS AND OUTCOMES — record
types, ``is_error`` flags, tool inputs, timestamps, chain state — never from
the agent's prose or self-assessed confidence (the signal class the prove-it
doctrine's binding council ranks least reliable; CONGRUENCE.md "WHY" section).
Zero LLM calls; zero network; zero wall-clock reads — the same inputs always
yield the same dict.

Transcript shape (verified 2026-08-30 against a real transcript under
``~/.grip/projects/-Users-void--grip/``; record-type census and field lists in
the fleet pool round-0 learnings):

- ``type == "assistant"`` records carry ``message.content`` blocks including
  ``{"type": "tool_use", "id", "name", "input"}``.
- ``type == "user"`` records carry either an operator prompt (string content)
  or tool outcomes: ``message.content`` blocks
  ``{"type": "tool_result", "tool_use_id", "is_error"}`` plus a top-level
  ``toolUseResult`` (object with stdout/stderr on Bash success; a STRING —
  often ``"Error: Exit code N..."`` or ``"User rejected tool use"`` — on
  failure; object with ``interrupted: true`` on interruption).
- Noise kinds (``attachment``, ``last-prompt``, ``mode``, ``permission-mode``,
  ``atis-latch``, ``queue-operation``, ``ai-title``, ``pr-link``,
  ``file-history-snapshot``) are re-appended throughout the file, so dispatch
  is on the ``type`` field, never on line position.

An EVENT is any record with ``type`` in ``("user", "assistant")``. The
analysis window is the last ``window`` events in FILE ORDER (append order is
the artefact; timestamps may interleave).

FEATURES — name, wrong-track rationale, falsifier
=================================================

``malformed_lines``   Lines that were not a JSON object. Rationale: a corrupt
    tail marks crash/kill churn. Falsifier: clean synthetic transcripts must
    score 0.0 (``test_malformed_lines_counted_and_skipped``).
``events_total``      All user/assistant records in the file (context scale).
    Falsifier: adding a noise record must NOT change it.
``window_events``     Events actually inside the window (< ``window`` on short
    transcripts) — the denominator disambiguator.
``tool_calls``        tool_use blocks in the window. Rationale: near-zero
    tool activity late in a task = stalled prose loop.
``tool_results``      tool_result blocks in the window (rate denominators).
``error_streak``      TRAILING run of consecutive failed tool_results
    (``is_error is True``; for Bash a failure corresponds to a non-zero exit —
    the observed failure string is ``"Error: Exit code N"``). Rationale: the
    canonical hammering-a-broken-approach signal. Falsifier: a green result
    must reset it to 0 (``test_error_streak_trailing_not_total``).
``max_error_streak``  Longest such run anywhere in the window ("was failing
    earlier" vs "failing now").
``error_rate``        Failed / total tool_results in the window (0.0 when no
    results). Falsifier: must differ from streaks on alternating pass/fail.
``tool_entropy``      Shannon entropy (bits) over tool names in the window.
    Rationale: entropy ~0 = one tool hammered in a loop; a spike after a long
    low plateau = flailing. AMBIGUOUS ALONE (focused work is also
    low-entropy) — a ranking input for the statistical layer, never a verdict.
    Falsifier: single-tool window must yield exactly 0.0; a 50/50 two-tool
    window exactly 1.0.
``repeat_call_ratio`` Fraction of tool_use events whose (name, input) is an
    EXACT repeat of an earlier call in the window (canonical-JSON digest).
    Rationale: identical retries without any input change = the purest loop
    artefact. Falsifier: all-distinct calls must score 0.0.
``revisit_ratio``     Fraction of path-targeted tool_uses (input key
    ``file_path``/``notebook_path``/``path``) whose target was already
    targeted earlier in the window. Rationale: re-reading the same artefact
    repeatedly marks a mental-model failure. Falsifier: distinct targets
    score 0.0.
``edit_retry_max``    Max number of Edit/Write/NotebookEdit/MultiEdit calls
    aimed at ONE file in the window. Rationale: the same file edited N times
    in M events is the classic fix-fail-fix churn loop. Falsifier: N distinct
    files once each must yield 1.0, not N.
``edit_files_touched``Distinct files edited in the window (churn breadth).
``test_runs``         Bash tool_results in the window whose PAIRED tool_use
    command matches a test-runner pattern (pytest/vitest/jest/rspec/unittest/
    ``go|cargo|npm|pnpm|yarn|rake|make test``). Pairing is by
    ``tool_use_id`` against a whole-file index, so a use just outside the
    window still classifies its in-window result.
``test_red_streak``   Trailing consecutive FAILED test runs. Rationale:
    red-red-red while editing = churn without progress; one green resets it.
    Falsifier: a trailing green run must force 0.0.
``operator_interrupts`` Window events showing an operator brake:
    ``toolUseResult.interrupted is True``, ``toolUseResult`` string starting
    ``"User rejected tool use"``, or message text starting
    ``"[Request interrupted by user"``. Rationale: the human slamming the
    brakes is the strongest exogenous wrong-track outcome available in the
    artefact. Falsifier: transcripts without those markers score 0.0.
``events_per_minute`` (window ts count - 1) / minutes spanned (0.0 when < 2
    parseable timestamps or zero span). Rationale: tempo collapse marks hung
    tools; robotic hyper-tempo marks degenerate loops. Direction is
    context-dependent — statistical-layer input, not a verdict.
``chain_present``     1.0 iff ``chain_path`` was given AND the file exists
    and is readable. Disambiguates the two 0.0s below from "no chain" (a
    signal that cannot disagree with reality is banned — 0.0 meaning both
    "no chain" and "all settled" would be exactly that).
``unsettled_count``   Open commitments via ``lib.continuity_chain.unsettled``
    (lib/continuity_chain.py:240-252). Rationale: acting while many
    pre-registered premises are unverified = flying on unchecked assumptions.
``unsettled_age_s``   Age (seconds) of the OLDEST unsettled commitment,
    measured against the NEWEST transcript event timestamp (never wall
    clock — determinism), falling back to the newest unsettled commit's own
    ``ts`` when the transcript has no parseable timestamps. Rationale: an old
    unsettled irreversible-class premise is exactly the "wrong premise makes
    downstream coherently wrong" window (CONGRUENCE.md "WHY").

Fail-soft: a malformed line is skipped and counted; an unreadable chain file
yields ``chain_present == 0.0``. Loud: a missing TRANSCRIPT raises
``FileNotFoundError`` (the declared subject is absent — that is not a
malformed line), and ``window < 1`` raises ``ValueError`` (a silently ignored
window would be a declared input that never reaches the actor;
rules/declared-input-must-reach-the-actor.md).

Chain reads use explicit ``path=`` only, which bypasses session namespacing
entirely (lib/continuity_chain.py:81-88) — this module never touches the live
``state/precog/continuity.jsonl``.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Sequence

from lib.continuity_chain import unsettled

__all__ = ["FEATURE_NAMES", "extract_features", "extract_features_many", "events_until"]

#: Stable vector order — the statistical lane may zip this against values.
FEATURE_NAMES: tuple[str, ...] = (
    "malformed_lines",
    "events_total",
    "window_events",
    "tool_calls",
    "tool_results",
    "error_streak",
    "max_error_streak",
    "error_rate",
    "tool_entropy",
    "repeat_call_ratio",
    "revisit_ratio",
    "edit_retry_max",
    "edit_files_touched",
    "test_runs",
    "test_red_streak",
    "operator_interrupts",
    "events_per_minute",
    "chain_present",
    "unsettled_count",
    "unsettled_age_s",
)

_EVENT_KINDS = ("user", "assistant")
_EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
_TARGET_KEYS = ("file_path", "notebook_path", "path")
_INTERRUPT_TEXT = "[Request interrupted by user"
_REJECTED_TEXT = "User rejected tool use"
# Test-runner commands: bare runner names, or "<tool> test" invocations.
_TEST_RE = re.compile(
    r"\b(?:pytest|vitest|jest|rspec|unittest)\b"
    r"|\b(?:go|cargo|npm|pnpm|yarn|rake|make)\s+test\b"
)


@dataclass(frozen=True)
class _ToolUse:
    name: str
    use_id: str | None
    digest: str
    target: str | None
    command: str | None


@dataclass(frozen=True)
class _ToolResult:
    use_id: str | None
    failed: bool


@dataclass
class _Event:
    kind: str
    epoch: float | None
    tool_uses: list[_ToolUse] = field(default_factory=list)
    tool_results: list[_ToolResult] = field(default_factory=list)
    interrupted: bool = False


def _flat_uses(events: Sequence[_Event]) -> list[_ToolUse]:
    return list(chain.from_iterable(ev.tool_uses for ev in events))


def _flat_results(events: Sequence[_Event]) -> list[_ToolResult]:
    return list(chain.from_iterable(ev.tool_results for ev in events))


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def _epoch(ts: Any) -> float | None:
    """ISO-8601 (``2026-08-12T01:19:15.711Z``) -> epoch seconds, else None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def _digest(name: str, payload: Any) -> str:
    """Canonical-JSON identity of one call — exact-repeat detection key."""
    body = json.dumps(
        {"input": payload, "name": name},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _use_from(block: dict) -> _ToolUse | None:
    name = block.get("name")
    if block.get("type") != "tool_use" or not isinstance(name, str) or not name:
        return None
    payload = block.get("input")
    payload = payload if isinstance(payload, dict) else {}
    target = next(
        (payload[k] for k in _TARGET_KEYS if isinstance(payload.get(k), str)),
        None,
    )
    command = payload.get("command")
    return _ToolUse(
        name=name,
        use_id=block.get("id") if isinstance(block.get("id"), str) else None,
        digest=_digest(name, payload),
        target=target,
        command=command if isinstance(command, str) else None,
    )


def _result_from(block: dict) -> _ToolResult | None:
    if block.get("type") != "tool_result":
        return None
    use_id = block.get("tool_use_id")
    return _ToolResult(
        use_id=use_id if isinstance(use_id, str) else None,
        failed=block.get("is_error") is True,  # absent is_error == success
    )


def _is_interrupted(rec: dict, blocks: list[dict], text: Any) -> bool:
    tur = rec.get("toolUseResult")
    if isinstance(tur, dict) and tur.get("interrupted") is True:
        return True
    if isinstance(tur, str) and tur.startswith(_REJECTED_TEXT):
        return True
    if isinstance(text, str) and text.startswith(_INTERRUPT_TEXT):
        return True
    return any(
        b.get("type") == "text"
        and str(b.get("text", "")).startswith(_INTERRUPT_TEXT)
        for b in blocks
    )


def _event_from(rec: dict) -> _Event | None:
    kind = rec.get("type")
    if kind not in _EVENT_KINDS:
        return None
    message = rec.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    event = _Event(kind=kind, epoch=_epoch(rec.get("timestamp")))
    for block in blocks:
        use = _use_from(block)
        if use is not None:
            event.tool_uses.append(use)
        result = _result_from(block)
        if result is not None:
            event.tool_results.append(result)
    event.interrupted = _is_interrupted(rec, blocks, content)
    return event


def _load_events(transcript_path: Path) -> tuple[list[_Event], int]:
    """Stream the JSONL once. Returns (events, malformed_line_count).

    A malformed line is one that is not a JSON OBJECT (parse failure or a
    non-dict value) — skipped and counted, never fatal. ``errors="replace"``
    keeps a bad-encoding line readable enough to be counted rather than
    crashing the whole extraction.
    """
    events: list[_Event] = []
    malformed = 0
    with open(transcript_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(rec, dict):
                malformed += 1
                continue
            event = _event_from(rec)
            if event is not None:
                events.append(event)
    return events, malformed


# --------------------------------------------------------------------------
# feature computation
# --------------------------------------------------------------------------

def _trailing_run(flags: Sequence[bool]) -> int:
    run = 0
    for flag in reversed(flags):
        if not flag:
            break
        run += 1
    return run


def _max_run(flags: Iterable[bool]) -> int:
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def _entropy_bits(counts: Iterable[int]) -> float:
    total = sum(counts)
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counts:
        if count:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy


def _error_features(results: Sequence[_ToolResult]) -> dict[str, float]:
    flags = [r.failed for r in results]
    rate = (sum(flags) / len(flags)) if flags else 0.0
    return {
        "tool_results": float(len(flags)),
        "error_streak": float(_trailing_run(flags)),
        "max_error_streak": float(_max_run(flags)),
        "error_rate": rate,
    }


def _first_seen_repeat_ratio(items: Sequence[str]) -> float:
    if not items:
        return 0.0
    seen: set[str] = set()
    repeats = 0
    for item in items:
        if item in seen:
            repeats += 1
        seen.add(item)
    return repeats / len(items)


def _tool_use_features(uses: Sequence[_ToolUse]) -> dict[str, float]:
    names = Counter(u.name for u in uses)
    targets = [u.target for u in uses if u.target is not None]
    edit_counts = Counter(
        u.target for u in uses if u.name in _EDIT_TOOLS and u.target is not None
    )
    return {
        "tool_calls": float(len(uses)),
        "tool_entropy": _entropy_bits(names.values()),
        "repeat_call_ratio": _first_seen_repeat_ratio([u.digest for u in uses]),
        "revisit_ratio": _first_seen_repeat_ratio(targets),
        "edit_retry_max": float(max(edit_counts.values(), default=0)),
        "edit_files_touched": float(len(edit_counts)),
    }


def _is_test_use(use: _ToolUse | None) -> bool:
    return (
        use is not None
        and use.name == "Bash"
        and use.command is not None
        and _TEST_RE.search(use.command) is not None
    )


def _test_features(
    results: Sequence[_ToolResult], use_index: dict[str, _ToolUse]
) -> dict[str, float]:
    """Red/green flags of in-window test-runner results, paired by tool_use_id
    against the WHOLE-file use index (a use just before the window boundary
    still classifies its in-window result)."""
    flags = [
        r.failed
        for r in results
        if r.use_id is not None and _is_test_use(use_index.get(r.use_id))
    ]
    return {
        "test_runs": float(len(flags)),
        "test_red_streak": float(_trailing_run(flags)),
    }


def _tempo(window_events: Sequence[_Event]) -> float:
    epochs = [ev.epoch for ev in window_events if ev.epoch is not None]
    if len(epochs) < 2:
        return 0.0
    span = max(epochs) - min(epochs)
    if span <= 0:
        return 0.0
    return (len(epochs) - 1) / (span / 60.0)


def _chain_features(
    chain_path: Path | None, ref_epoch: float | None
) -> dict[str, float]:
    absent = {"chain_present": 0.0, "unsettled_count": 0.0, "unsettled_age_s": 0.0}
    if chain_path is None or not chain_path.is_file():
        return absent
    try:
        open_commits = unsettled(path=chain_path)
    except OSError:  # unreadable file — fail-soft, disambiguated by chain_present
        return absent
    epochs = [e for node in open_commits if (e := _epoch(node.ts)) is not None]
    reference = ref_epoch if ref_epoch is not None else (max(epochs) if epochs else None)
    age = 0.0
    if epochs and reference is not None:
        age = max(0.0, reference - min(epochs))
    return {
        "chain_present": 1.0,
        "unsettled_count": float(len(open_commits)),
        "unsettled_age_s": age,
    }


def extract_features(
    transcript_path: str | Path,
    *,
    chain_path: str | Path | None = None,
    window: int = 50,
    until: float | None = None,
) -> dict[str, float]:
    """Deterministic trajectory features for one session transcript.

    Returns a dict whose keys are exactly ``FEATURE_NAMES`` in that order,
    every value a float. Same inputs -> same output (no wall clock, no
    randomness, no LLM). Raises ``FileNotFoundError`` for a missing
    transcript and ``ValueError`` for ``window < 1``; per-line damage is
    fail-soft (skipped, counted in ``malformed_lines``).

    ``until`` (epoch seconds) makes the vector AS-OF a decision instant: only
    events up to and including the last one timestamped ``<= until`` are
    seen (``events_until``), so a label settled later can be paired with the
    trajectory that PRECEDED the decision rather than with the whole session
    (Congruent Continuity pairing, ``lib/continuity_pairing.py``).
    ``until=None`` is byte-identical to the pre-cutoff behaviour.
    ``malformed_lines`` stays a whole-file count: a damaged line carries no
    timestamp to cut on.
    """
    events, malformed = _load_transcript(transcript_path, window)
    return _compute(events_until(events, until), malformed,
                    chain_path=chain_path, window=window)


def extract_features_many(
    transcript_path: str | Path,
    untils: Sequence[float | None],
    *,
    chain_path: str | Path | None = None,
    window: int = 50,
) -> list[dict[str, float]]:
    """One vector per cutoff in ``untils``, loading the transcript ONCE.

    Equal to ``[extract_features(path, until=u, ...) for u in untils]``
    (pinned by test) but O(file) instead of O(file * len(untils)): a
    multi-gigabyte transcript that created twenty labelled commits is read
    a single time.
    """
    events, malformed = _load_transcript(transcript_path, window)
    return [
        _compute(events_until(events, u), malformed, chain_path=chain_path, window=window)
        for u in untils
    ]


def events_until(events: Sequence[_Event], until: float | None) -> list[_Event]:
    """Prefix of ``events`` (file order) up to the last event whose
    ``epoch <= until``. An untimestamped event keeps its file position: it is
    included while the cut has not been reached and excluded once an event
    overruns it. ``until=None`` returns every event."""
    if until is None:
        return list(events)
    cut = 0
    for index, event in enumerate(events):
        if event.epoch is not None and event.epoch > until:
            break
        cut = index + 1
    return list(events[:cut])


def _load_transcript(transcript_path: str | Path, window: int) -> tuple[list[_Event], int]:
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    path = Path(transcript_path)
    if not path.is_file():
        raise FileNotFoundError(f"transcript not found: {path}")
    return _load_events(path)


def _compute(
    events: Sequence[_Event],
    malformed: int,
    *,
    chain_path: str | Path | None,
    window: int,
) -> dict[str, float]:
    window_events = list(events[-window:])
    window_uses = _flat_uses(window_events)
    window_results = _flat_results(window_events)
    use_index = {u.use_id: u for u in _flat_uses(events) if u.use_id is not None}
    all_epochs = [ev.epoch for ev in events if ev.epoch is not None]
    features: dict[str, float] = {
        "malformed_lines": float(malformed),
        "events_total": float(len(events)),
        "window_events": float(len(window_events)),
    }
    features.update(_tool_use_features(window_uses))
    features.update(_error_features(window_results))
    features.update(_test_features(window_results, use_index))
    features["operator_interrupts"] = float(
        sum(1 for ev in window_events if ev.interrupted)
    )
    features["events_per_minute"] = _tempo(window_events)
    features.update(
        _chain_features(
            Path(chain_path) if chain_path is not None else None,
            max(all_epochs) if all_epochs else None,
        )
    )
    return {name: features[name] for name in FEATURE_NAMES}
