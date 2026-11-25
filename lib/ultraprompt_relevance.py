#!/usr/bin/env python3
"""lib/ultraprompt_relevance.py -- Jev's "still relevant?" reading for pending briefs.

Phase 5.3 of the Jev integration plan (``~/.grip/plans/snug-questing-quill.md``):
for each pending ultraprompt continuation brief (``lib.ultraprompt_registry``),
ask Jev (TypeSafe's decision model, through HAL ``decide``) one Noul question --
is this brief still worth executing, given what has happened in the repository
since it was written? Every brief that passes the privacy floor goes out as a
question sharing ONE combined ``state`` blob (title, tldr and a bounded list of
commit subjects since the brief's own written date, per brief), batched into as
few Jev calls as the token limits allow. The result is written to a small cache
file; nothing else changes.

Two halves, two different budgets
----------------------------------
- :func:`read_cache` is a PURE FILE READ: stdlib only, no network, no import of
  ``hal_llm_adapter`` or ``decision_privacy``. The SessionStart picker hook
  (``hooks/ultraprompt-picker-sessionstart.py``) calls only this, so importing
  this module there can never pull the decide path into that hot path's
  process -- its own docstring promises "No network, no heavy work".
- :func:`refresh` does the asking and the writing. It is never called from the
  hook in-process; the hook spawns :func:`spawn_refresh`, a DETACHED child that
  runs this module's own CLI (``refresh``) and is not waited on.

Privacy: a brief's own text -- never its full body, only title + tldr + commit
subjects -- goes through the decision-model privacy floor
(``lib.decision_privacy.may_send``) on its own, before it is added to any
outgoing call. A brief the floor refuses is left unranked (no cache entry with
a probability) and is never sent.

Never hides a brief: the cache only ever changes ORDER and adds a label. A
brief with no reading -- refused by the floor, or never scored -- is still
listed by the picker hook, just without a relevance line.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_LIB = Path(__file__).resolve().parent
# Both lib/ (bare-module imports: claude_dir, ultraprompt_registry below)
# AND its parent (the package-qualified `from lib.X import Y` imports the
# deferred _default_decide/_floor use, e.g. lib.hal_llm_adapter,
# lib.decision_privacy) go on sys.path. The latter resolves ONLY when the
# repo root is on sys.path; without it, run as a script from any cwd, it
# fails with `No module named 'lib'` (lib/grip_save.py carries the same
# fix, with the fuller incident write-up: AGORA #5070).
for _p in (str(_LIB), str(_LIB.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from claude_dir import get_claude_dir  # noqa: E402
import ultraprompt_registry as registry  # noqa: E402

__all__ = ["Reading", "cache_path", "read_cache", "refresh", "spawn_refresh", "report"]

#: Bounds on the per-brief "state" text, so one call's combined state and
#: question set stay well inside Jev's 32k-token state / 64k-token total
#: budget (docs/decide.md, HAL). Conservative CHARACTER counts, not a real
#: token count -- there is no tokenizer here, so the budget errs small.
_TITLE_CHARS = 140
_SUMMARY_CHARS = 200  # the registry's own tldr cap (lib.ultraprompt_registry._TLDR_MAX)
_COMMIT_SUBJECT_CHARS = 72
_COMMITS_PER_BRIEF = 10
_STATE_BUDGET_CHARS = 20_000  # one call's combined state
_BRIEFS_PER_CALL = 40  # vendor measured latency flat to ~64 questions; stay under it
#: A cache older than this is treated as stale -- the picker hook then
#: behaves exactly as it did before this module existed.
_CACHE_MAX_AGE_S = 24 * 3600
#: Either switch turns it off: the operator's, and the test suite's fence for
#: every decision-model call (tests/conftest.py).
_OFF = (("GRIP_ULTRAPROMPT_RELEVANCE", "0"), ("GRIP_CLASSIFY_NO_DECIDE", "1"))
_WRITTEN_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

Decide = Callable[[str, dict], Optional[dict]]


@dataclass(frozen=True)
class Reading:
    """One brief's relevance reading, or why there is none. Uncalibrated and advisory."""
    sha256: str
    probability: Optional[float] = None
    model: str = ""
    computed_at: float = 0.0
    skipped: str = ""


def enabled() -> bool:
    return not any(os.environ.get(name, "") == value for name, value in _OFF)


# --- cache path + read (pure; no decide-path import) ------------------------
def cache_path() -> Path:
    env = os.environ.get("GRIP_ULTRAPROMPT_RELEVANCE_CACHE", "").strip()
    if env:
        return Path(env).expanduser()
    return get_claude_dir() / "state" / "ultraprompt-relevance-cache.json"


def _read_raw_cache(path: Optional[Path] = None) -> dict:
    """The cache file's raw dict, or ``{}`` on any missing/corrupt/wrong-shape input."""
    try:
        data = json.loads((path or cache_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _row_to_reading(sha: str, row: object) -> Optional[Reading]:
    if not isinstance(row, dict):
        return None
    computed_at = row.get("computed_at")
    if not isinstance(computed_at, (int, float)) or isinstance(computed_at, bool):
        return None
    prob = row.get("probability")
    prob = float(prob) if isinstance(prob, (int, float)) and not isinstance(prob, bool) else None
    return Reading(sha, prob, str(row.get("model") or ""), float(computed_at), str(row.get("skipped") or ""))


def read_cache(*, max_age_s: float = _CACHE_MAX_AGE_S, path: Optional[Path] = None) -> Optional[Dict[str, Reading]]:
    """Every brief's last reading, keyed by sha256 -- or ``None`` when there is
    no cache, it cannot be read, or its most recent refresh is older than
    ``max_age_s``. Pure file read: never imports anything that can reach a
    model, so a caller on a hot path (the SessionStart hook) is safe to use
    this without pulling the decide path into its own process.
    """
    raw = _read_raw_cache(path)
    if not raw:
        return None
    rows: Dict[str, Reading] = {}
    newest = 0.0
    for sha, row in raw.items():
        reading = _row_to_reading(sha, row)
        if reading is None:
            continue
        rows[sha] = reading
        newest = max(newest, reading.computed_at)
    if not rows or (time.time() - newest) > max_age_s:
        return None
    return rows


def _write_cache(readings: Dict[str, Reading], *, path: Optional[Path] = None) -> None:
    """Merge ``readings`` into the on-disk cache (never dropping a brief that
    fell out of ``pending`` -- e.g. one just executed -- because
    :func:`report` needs its last reading for the picked-vs-skipped
    measurement). Best-effort; never raises into the caller.
    """
    target = path or cache_path()
    existing = _read_raw_cache(target)
    for sha, reading in readings.items():
        existing[sha] = {"probability": reading.probability, "model": reading.model,
                         "computed_at": reading.computed_at, "skipped": reading.skipped}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


# --- building each brief's state block --------------------------------------
def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _written_date(brief) -> Optional[str]:
    match = _WRITTEN_DATE_RE.search(brief.written or "")
    return match.group(1) if match else None


def _repo_root_for(path: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=os.path.dirname(os.path.abspath(path)),
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top = proc.stdout.strip()
    return top if proc.returncode == 0 and top else None


def _commit_subjects_since(repo_dir: Optional[str], since: Optional[str]) -> List[str]:
    """Commit subjects in ``repo_dir``'s history since ``since`` (an ISO date),
    newest first, bounded in both count and length. Never raises; a git
    failure of any kind is "no commits found", not an error."""
    if not repo_dir or not since:
        return []
    try:
        proc = subprocess.run(
            ["git", "log", f"--since={since}", f"-n{_COMMITS_PER_BRIEF}", "--pretty=format:%s"],
            cwd=repo_dir, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return [_clip(line, _COMMIT_SUBJECT_CHARS) for line in lines[:_COMMITS_PER_BRIEF]]


def _brief_block(brief, idx: int) -> str:
    """This brief's "state" text: title, summary and commits since written --
    NEVER the brief's full body.

    ``_repo_root_for`` is skipped entirely when there is no written date --
    ``_commit_subjects_since`` would discard its result anyway, but it is a
    real subprocess spawn, and a brief with no "Written:" line is not rare.
    Measured on the 10k-brief falsifier test below: 9.51s before this guard,
    0.03s after -- 10k wasted `git rev-parse` spawns were the whole cost.
    """
    since = _written_date(brief)
    commits = _commit_subjects_since(_repo_root_for(brief.path), since) if since else []
    lines = [
        f"=== BRIEF {idx} ===",
        f"Title: {_clip(brief.title, _TITLE_CHARS)}",
        f"Summary: {_clip(brief.tldr, _SUMMARY_CHARS) or '(no summary)'}",
        f"Written: {brief.written or 'unknown'}",
    ]
    if commits:
        lines.append("Commits in this repository since written:")
        lines.extend(f"- {c}" for c in commits)
    else:
        lines.append("Commits in this repository since written: (none found, or the date is unknown)")
    return "\n".join(lines)


def _question(brief, idx: int) -> dict:
    return {"type": "noul", "instructions": (
        f"Looking at BRIEF {idx} in the state above, titled {brief.title!r}: "
        "given what has happened in this repository since it was written, "
        "is it still worth executing?")}


def _probability(answer) -> Optional[float]:
    p = answer.get("noul") if isinstance(answer, dict) else None
    ok = isinstance(p, (int, float)) and not isinstance(p, bool) and 0 <= p <= 1
    return float(p) if ok else None


# --- sending + scoring --------------------------------------------------------
def _floor(text: str, cwd: Optional[str]) -> Tuple[bool, str]:
    try:
        from lib.decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from lib.hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=20.0)


def _ask_chunk(briefs: Sequence, state: str, questions: dict, decide: Decide,
               ts: float, readings: Dict[str, Reading]) -> None:
    """One Jev call for this chunk's briefs; fills ``readings`` for every one
    of them, "no_answer" on any transport or shape failure. Never raises."""
    try:
        result = decide(state, questions)
    except Exception:  # noqa: BLE001 -- advisory; never raises
        result = None
    ok = isinstance(result, dict) and bool(result.get("ok"))
    model = str(result.get("model") or "") if isinstance(result, dict) else ""
    answers = (result.get("answers") or {}) if ok else {}
    for idx, brief in enumerate(briefs):
        p = _probability(answers.get(f"b{idx}"))
        readings[brief.sha256] = (
            Reading(brief.sha256, p, model, ts) if p is not None
            else Reading(brief.sha256, None, model, ts, "no_answer")
        )


def refresh(*, decide: Decide = _default_decide, now: Optional[float] = None,
            briefs: Optional[Sequence] = None) -> Dict[str, Reading]:
    """Score every pending brief's relevance and persist the cache.

    Never raises: a brief the privacy floor refuses gets an unranked
    ("privacy_floor:<why>") Reading and is never added to an outgoing call; a
    chunk's transport failure yields "no_answer" for every brief in it. Call
    via this module's own CLI (``refresh``), spawned detached by the
    SessionStart hook -- never in-process on that hot path.
    """
    if not enabled():
        return {}
    ts = now if now is not None else time.time()
    pending = list(briefs) if briefs is not None else registry.list_pending(limit=None)
    readings: Dict[str, Reading] = {}

    chunk_state: List[str] = []
    chunk_questions: Dict[str, dict] = {}
    chunk_briefs: List = []

    def flush() -> None:
        if chunk_briefs:
            _ask_chunk(chunk_briefs, "\n\n".join(chunk_state), chunk_questions, decide, ts, readings)

    for brief in pending:
        idx_in_chunk = len(chunk_briefs)
        block = _brief_block(brief, idx_in_chunk)
        allowed, why = _floor(block, os.path.dirname(brief.path))
        if not allowed:
            readings[brief.sha256] = Reading(brief.sha256, None, "", ts, f"privacy_floor:{why}")
            continue
        state_len = sum(len(s) for s in chunk_state) + len(block)
        if chunk_briefs and (idx_in_chunk >= _BRIEFS_PER_CALL or state_len > _STATE_BUDGET_CHARS):
            flush()
            chunk_state, chunk_questions, chunk_briefs = [], {}, []
            idx_in_chunk = 0
            block = _brief_block(brief, idx_in_chunk)  # re-labelled BRIEF 0 of the new chunk
        chunk_state.append(block)
        chunk_questions[f"b{idx_in_chunk}"] = _question(brief, idx_in_chunk)
        chunk_briefs.append(brief)
    flush()

    _write_cache(readings)
    return readings


def spawn_refresh() -> None:
    """Start a detached refresh child and return at once. Best-effort, never
    raises. The hook calls this and moves on -- it must never wait on a
    network call. Off with GRIP_ULTRAPROMPT_RELEVANCE=0 or the global
    GRIP_CLASSIFY_NO_DECIDE=1."""
    if not enabled():
        return
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "refresh"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:  # noqa: BLE001 -- refresh is best-effort, never a blocker
        return


# --- measurement: picked vs skipped, against Jev's cached reading -----------
def report() -> dict:
    """Picked (executed) vs skipped (still pending) brief counts, grouped by
    Jev's cached reading -- the measurement for the pre-registered hypothesis
    "briefs Jev reads as still relevant are the ones the operator actually
    picks". Reads the relevance cache's historical rows (kept even for a
    brief no longer pending, see :func:`_write_cache`) against the registry's
    executed ledger. Grades, never gates: this is read-only.
    """
    cache = _read_raw_cache()
    executed = registry.executed_shas()
    picked, skipped = [], []
    for sha, row in cache.items():
        if not isinstance(row, dict):
            continue
        p = row.get("probability")
        if not isinstance(p, (int, float)) or isinstance(p, bool):
            continue
        (picked if sha in executed else skipped).append(float(p))

    def avg(xs: List[float]) -> Optional[float]:
        return sum(xs) / len(xs) if xs else None

    return {
        "picked_count": len(picked), "picked_avg_probability": avg(picked),
        "skipped_count": len(skipped), "skipped_avg_probability": avg(skipped),
    }


# --- CLI -----------------------------------------------------------------
def _refresh_breakdown(readings: Dict[str, Reading]) -> Dict[str, int]:
    """Counts by outcome, computed from what ``refresh()`` already returns --
    every :class:`Reading` already carries its own ``skipped`` reason, so no
    change to ``refresh()``'s return shape is needed to report this (AGORA
    suggestion, GRIP#8096, xAI Grok 4.3)."""
    refused = sum(1 for r in readings.values() if r.skipped.startswith("privacy_floor:"))
    no_answer = sum(1 for r in readings.values() if r.skipped == "no_answer")
    scored = sum(1 for r in readings.values() if r.probability is not None)
    return {"total": len(readings), "sent": len(readings) - refused,
            "refused_by_floor": refused, "no_answer": no_answer, "scored": scored}


def _cli_refresh(_rest: List[str]) -> int:
    counts = _refresh_breakdown(refresh())
    print("scored {scored} of {total} brief(s) ({sent} sent, {refused_by_floor} refused "
         "by the privacy floor, {no_answer} sent but with no usable answer)".format(**counts))
    return 0


def _cli_report(_rest: List[str]) -> int:
    print(json.dumps(report(), indent=2))
    return 0


_CLI_HANDLERS = {"refresh": _cli_refresh, "report": _cli_report}


def _cli(argv: List[str]) -> int:
    if not argv or argv[0] not in _CLI_HANDLERS:
        print("usage: ultraprompt_relevance.py {refresh | report}", file=sys.stderr)
        return 2
    return _CLI_HANDLERS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
