#!/usr/bin/env python3
"""Hypothesis Engine — turns every GRIP PR into a scientific experiment.

The core insight: software changes are implicit hypotheses.
"Adding feature X will improve metric Y" is a testable prediction.
This engine makes those predictions EXPLICIT and AUTO-VERIFIES them.

Lifecycle:
1. PR merged -> hypothesis registered (prediction + metric + deadline)
2. Deadline arrives -> engine measures the metric
3. Comparison: prediction vs reality -> CONFIRMED / REFUTED / INCONCLUSIVE
4. Results feed into /evo genome and DELIGHT v2

This is the GRIP equivalent of a clinical trial protocol:
- Pre-registration prevents post-hoc rationalisation
- Automated verification prevents confirmation bias
- Historical record prevents revisionist history

Usage:
    python3 lib/hypothesis_engine.py register --pr 1381 --claim "DELIGHT v2 shadow metrics reduce Goodhart gaming" --metric "velocity_shadow_correlation" --prediction "negative correlation" --deadline 2026-04-06
    python3 lib/hypothesis_engine.py verify [--all]
    python3 lib/hypothesis_engine.py status
    python3 lib/hypothesis_engine.py falsify --id H001
"""

import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# --- Linked-worktree ledger guard (GRIP#9642) -------------------------------
#
# The live ledger is the shared tree's own `learning/hypotheses.jsonl`
# (rules/shared-tree-is-not-canonical.md); a LINKED git worktree's copy is
# whatever snapshot `main` carried when the worktree was created, and it
# drifts further from the live file with every commit on either side.
# `_next_id()` takes the highest id in whichever file `HYPOTHESIS_FILE`
# names, so a registration made with CLAUDE_DIR pointing at a worktree reads
# the stale snapshot. On 2026-11-25 two background agents each registered
# from a worktree, both took H844, and both committed it, overwriting one
# another's row on main and freezing the shared tree's sync.
#
# Resolution by git itself, not a guess: a linked worktree's own
# `--git-dir` is `.git/worktrees/<name>` inside the MAIN checkout's git
# directory, while `--git-common-dir` always names that shared git
# directory. The two are equal in the main worktree (and in any ordinary,
# non-worktree clone) and differ ONLY inside a linked one.
_GIT_RUN_TIMEOUT_S = 5

# `_git_rev_parse` must answer for the folder it was told to probe, not for
# whatever repository the CALLING process was already in the middle of
# (GRIP#9686). A git hook sets these on every process it spawns; when one
# is inherited, git's own repository discovery overrides `-C` regardless of
# which directory `-C` names, so `--git-dir`/`--git-common-dir` can come
# back describing a wholly different repository instead of `folder` --
# equal when they should differ (a false "main worktree" reading that lets
# a linked-worktree registration through) or different when they should be
# equal (a false "linked worktree" reading that refuses a legitimate one).
# lib/grip-worktree.sh's `_worktree_git_dir` clears GIT_DIR/GIT_WORK_TREE
# for the identical reason; GIT_COMMON_DIR and GIT_INDEX_FILE join it here
# because this function also asks `--git-common-dir`, which GIT_COMMON_DIR
# alone is enough to hijack.
_INHERITED_GIT_ENV_VARS = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
})


def _git_rev_parse(folder: Path, *args: str) -> str | None:
    """``git -C folder rev-parse <args>``'s stripped stdout, or None.

    None covers every way the answer is unavailable: no git binary on PATH,
    ``folder`` is not inside a git repository at all (the ordinary case in
    tests, which point HYPOTHESIS_FILE at a bare temporary directory), or
    the call times out. A hypothesis ledger is not a safety control, so an
    unknown answer here must mean "allow", never "refuse" — see
    LinkedWorktreeLedgerError and rules/gate-resolution-escape-hatch.md.

    Runs with a COPY of the environment that has `_INHERITED_GIT_ENV_VARS`
    removed -- `os.environ` itself is never touched -- so an inherited
    GIT_DIR/GIT_WORK_TREE/GIT_COMMON_DIR/GIT_INDEX_FILE can never make this
    answer about some other repository instead of ``folder``.
    """
    child_env = {
        k: v for k, v in os.environ.items() if k not in _INHERITED_GIT_ENV_VARS
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", *args],
            capture_output=True, text=True, timeout=_GIT_RUN_TIMEOUT_S,
            env=child_env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _nearest_existing_ancestor(folder: Path) -> Path:
    """The closest existing DIRECTORY at or above ``folder``.

    ``HYPOTHESIS_FILE``'s own parent may not exist yet -- a freshly created
    worktree checkout has no ``learning/`` directory until something writes
    into it. ``git -C`` requires an existing directory, so without this an
    absent folder would be indistinguishable from "not a git repository at
    all" and silently ALLOW inside the very worktree this guard exists to
    refuse. ``folder.parents`` always reaches an existing directory (``.``
    or ``/``), so this never raises. Checked with ``is_dir()``, not
    ``exists()``: a FILE-valued ancestor (e.g. ``folder``'s grandparent is
    itself a plain file) would otherwise stop the walk one level too early
    and hand git a non-directory, rather than continuing up to the nearest
    real directory.
    """
    for candidate in (folder, *folder.parents):
        if candidate.is_dir():
            return candidate
    return folder


def _is_linked_worktree(folder: Path) -> bool:
    """True iff ``folder`` belongs to a LINKED git worktree, never the main one.

    Paths are resolved against ``folder`` before comparing because git may
    print either answer relative to the working directory it was run in;
    joining a Path with an already-absolute string keeps the absolute form.
    Unresolvable (see `_git_rev_parse`) means False — not a worktree, allow.
    """
    folder = _nearest_existing_ancestor(folder)
    git_dir = _git_rev_parse(folder, "--git-dir")
    common_dir = _git_rev_parse(folder, "--git-common-dir")
    if git_dir is None or common_dir is None:
        return False
    return (folder / git_dir).resolve() != (folder / common_dir).resolve()


# Ensure lib/ is importable when this module is executed as a script
# (python3 lib/hypothesis_engine.py ...). As an imported module the
# path is already established and this is a no-op.
_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from file_lock import FileLock  # noqa: E402

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
HYPOTHESIS_FILE = CLAUDE_DIR / "learning" / "hypotheses.jsonl"
VERIFICATION_FILE = CLAUDE_DIR / "learning" / "hypothesis_verifications.jsonl"
# One row per experiment the always-on tick ran for a hypothesis (record_experiment); never a verdict.
EXPERIMENT_FILE = CLAUDE_DIR / "learning" / "hypothesis_experiments.jsonl"
INSIGHTS_FILE = CLAUDE_DIR / "learning" / "insights.jsonl"
# Who registered each hypothesis: one row per registration, kept beside the
# ledger (provenance_file). A ledger row says WHAT was predicted and never who
# predicted it, so no reader could trace a hypothesis to its session.
PROVENANCE_NAME = "hypothesis_provenance.jsonl"

# Reconcile policy constants
RECONCILE_INSIGHT_TAIL = 100  # most recent insights to scan for H<id>: tags
RECONCILE_NOTE_PREFIX = "auto-reconciled"  # idempotency marker

# Status token for a row the writer refused to touch. Recorded in the results
# list so a sweep never drops a row silently.
SKIPPED_STATUS = "skipped_ambiguous"


class HypothesisStoreError(Exception):
    """A write to the hypothesis store could not be performed safely.

    Callers that sweep many rows catch this base so one unwritable row cannot
    abandon the rest of the sweep.
    """


class HypothesisNotFound(HypothesisStoreError):
    """No row in the store carries the requested id."""


class AmbiguousHypothesisId(HypothesisStoreError):
    """More than one genuinely different row carries the requested id.

    The verdict cannot be attributed to one record, so nothing is written.
    """


class LinkedWorktreeLedgerError(HypothesisStoreError):
    """HYPOTHESIS_FILE resolves inside a linked git worktree.

    Raised before any id or lock is taken, so a refused call leaves the
    ledger and its id sequence untouched. The fix lies entirely OUTSIDE what
    this refuses, so it can never deadlock
    (rules/gate-resolution-escape-hatch.md Invariant 1): re-run the same
    command with CLAUDE_DIR unset, so HYPOTHESIS_FILE resolves to the shared
    tree's live ledger instead of the worktree's stale snapshot.
    """


def _refuse_if_linked_worktree(caller: str) -> None:
    """Raise LinkedWorktreeLedgerError when HYPOTHESIS_FILE sits in a linked
    git worktree. Call this FIRST, before taking any id or lock.
    """
    if not _is_linked_worktree(HYPOTHESIS_FILE.parent):
        return
    raise LinkedWorktreeLedgerError(
        f"{caller}: HYPOTHESIS_FILE ({HYPOTHESIS_FILE}) is inside a linked "
        "git worktree, so it holds a stale snapshot of the ledger, not the "
        "live one. Fix: re-run this command with CLAUDE_DIR unset (or "
        "pointed at the shared tree) so learning/hypotheses.jsonl resolves "
        "to the shared tree's live ledger instead."
    )


@dataclass
class Hypothesis:
    """A testable prediction about a GRIP change."""

    id: str = ""                    # H001, H002, ...
    pr_number: int = 0              # source PR
    registered: str = ""            # ISO timestamp
    claim: str = ""                 # human-readable claim
    metric: str = ""                # what to measure
    prediction: str = ""            # expected direction/value
    baseline: float = 0.0           # metric value at registration
    deadline: str = ""              # when to verify (ISO date)
    status: str = "pending"         # pending / confirmed / refuted / inconclusive / expired
    verification_date: str = ""     # when verified
    actual_value: float = 0.0       # metric value at verification
    effect_size: float = 0.0        # Cohen's d or similar
    notes: str = ""
    externally_validated: bool = False
    # HOW this hypothesis will be graded, for a metric with no collector.
    #
    # 479 of the store's 493 records name a metric nothing can resolve
    # (measured against origin/main, 2026-08-15), so they were born
    # ungradeable and 92 are now permanently so. `metric` is free text and
    # _resolve_metric knows five names; nothing ever reconciled the two.
    # This field is the honest alternative to pretending: either a collector
    # exists, or the record says in words what must be run to get the value.
    #
    # Defaults to "" so the 493 existing rows keep loading unchanged --
    # from_dict filters to known fields, so an absent key is just the default.
    # Enforcement is at REGISTRATION only. Nothing re-grades history.
    measure_plan: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def _to_float(v, default: float = 0.0) -> float:
        """Coerce an untrusted JSON value to float.

        hypotheses.jsonl is edited out-of-band and predates strict
        typing, so baseline/actual_value/effect_size can arrive as
        strings ("0.5", "", "n/a") or null. Dataclasses do NOT enforce
        annotations at runtime, so without this `actual - h.baseline`
        raises TypeError (GRIP#1826) and the "increase"/"decrease" path
        of _evaluate_prediction fails the same way. Garbage -> default
        keeps `verify --all` resilient instead of aborting the sweep.
        """
        if isinstance(v, bool):  # bool is an int subclass — not a metric
            return default
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            return default

    @classmethod
    def from_dict(cls, d: dict) -> "Hypothesis":
        clean = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for nk in ("baseline", "actual_value", "effect_size"):
            if nk in clean:
                clean[nk] = cls._to_float(clean[nk])
        return cls(**clean)


def _next_id() -> str:
    """Generate next hypothesis ID (H001, H002, ...)."""
    if not HYPOTHESIS_FILE.exists():
        return "H001"
    try:
        lines = HYPOTHESIS_FILE.read_text().strip().splitlines()
        max_num = 0
        for line in lines:
            try:
                rec = json.loads(line)
                hid = rec.get("id", "H000")
                num = int(hid[1:])
                max_num = max(max_num, num)
            except (json.JSONDecodeError, ValueError):
                continue
        return f"H{max_num + 1:03d}"
    except OSError:
        return "H001"


def _load_hypotheses() -> list:
    """Load all hypotheses from JSONL."""
    if not HYPOTHESIS_FILE.exists():
        return []
    result = []
    try:
        for line in HYPOTHESIS_FILE.read_text().splitlines():
            if line.strip():
                try:
                    result.append(Hypothesis.from_dict(json.loads(line)))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        pass
    return result


def _save_hypothesis(h: Hypothesis) -> None:
    """Append hypothesis to JSONL. Caller must hold the hypothesis file lock."""
    HYPOTHESIS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HYPOTHESIS_FILE, "a") as f:
        f.write(json.dumps(h.to_dict(), separators=(",", ":")) + "\n")


def _read_raw_lines() -> list:
    """Every line of the store, verbatim, including its line terminator.

    Terminators are kept so an untargeted line can be written back
    byte-for-byte — including the last line of a file that does not end in a
    newline, and a line that arrived with CRLF. Read errors are NOT swallowed:
    on the write path, treating an unreadable store as empty would be
    indistinguishable from an empty one.
    """
    if not HYPOTHESIS_FILE.exists():
        return []
    return HYPOTHESIS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)


def _content(raw: str) -> str:
    """A raw line without its trailing newline characters."""
    return raw.rstrip("\r\n")


def _terminator(raw: str) -> str:
    """The trailing newline characters of a raw line, or "" if it has none."""
    return raw[len(_content(raw)):]


def _row_id(raw: str):
    """The ``id`` carried by a raw JSONL line, or None if it carries none.

    A blank or unparseable line yields None, so it counts as untargeted and is
    copied through verbatim rather than dropped — this store is edited
    out-of-band and a write must not silently prune what it cannot parse.
    """
    try:
        rec = json.loads(_content(raw))
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(rec, dict) and isinstance(rec.get("id"), str):
        return rec["id"]
    return None


def _find_row_indices(lines: list, hid: str) -> list:
    """Indices into ``lines`` of every row whose id is ``hid``."""
    return [i for i, raw in enumerate(lines) if _row_id(raw) == hid]


def _rows_diverge(lines: list, idxs: list) -> bool:
    """True when the matched rows are not all byte-identical.

    Byte comparison is exact rather than heuristic here: measured across all 66
    duplicate ids in the live store, the number of distinct raw lines equals the
    number of distinct canonical JSON objects, so no two copies differ only in
    key order or whitespace.
    """
    return len({_content(lines[i]) for i in idxs}) > 1


def divergent_ids(lines: list) -> set:
    """Every id whose rows in ``lines`` are not all byte-identical, by the test
    ``_rows_diverge`` applies: an id that no verdict and no experiment run can
    be attributed to. Pure and one pass, so a reader that already holds the
    store's lines, such as the always-on tick choosing an experiment, asks
    before it acts rather than after (Codex on GRIP#8856).
    """
    rows: dict = {}
    for raw in lines:
        hid = _row_id(raw)
        if hid is not None:
            rows.setdefault(hid, set()).add(_content(raw))
    return {hid for hid, seen in rows.items() if len(seen) > 1}


def _ambiguity_report(lines: list, idxs: list, hid: str) -> str:
    """A refusal the operator can act on without opening the file."""
    rows = []
    for i in idxs:
        try:
            rec = json.loads(_content(lines[i]))
        except (json.JSONDecodeError, ValueError):
            rec = {}
        rows.append(
            f"line {i + 1}: status={rec.get('status', '?')!r} "
            f"claim={str(rec.get('claim', ''))[:60]!r}"
        )
    distinct = len({_content(lines[i]) for i in idxs})
    return (
        f"{hid} is carried by {len(idxs)} rows holding {distinct} distinct records, "
        f"so this verdict cannot be attributed to one record; nothing was written. "
        + " | ".join(rows)
        + ". Resolution: split the colliding id — give the row that is a different "
        "hypothesis a fresh id above the store's current maximum — then retry."
    )


def _merge_row(original: dict, h: Hypothesis) -> dict:
    """Overlay only what the caller changed; absent stays absent.

    Writing ``h.to_dict()`` over the line would drop every key the dataclass
    does not declare and rewrite every unparseable number as 0.0. Starting from
    the original parsed dict and overlaying only genuinely-changed fields keeps
    an out-of-band ``evidence`` field, and keeps a string ``baseline`` date,
    on the very row being verified.
    """
    loaded = Hypothesis.from_dict(original).to_dict()   # what the caller was handed
    updated = h.to_dict()                               # what the caller is returning
    out = dict(original)                                # unknown keys survive verbatim
    for k, v in updated.items():
        if loaded[k] != v:
            out[k] = v
    return out


# Flushing a folder needs os.open on the folder, which works on POSIX only:
# Windows refuses it with PermissionError.
_CAN_FLUSH_DIRECTORY = os.name != "nt"


def _flush_directory(directory: Path) -> None:
    """Flush ``directory`` so a rename inside it survives a power loss, where the OS allows it.

    On Windows ``os.open`` cannot open a folder, so there is no flush to make and
    this returns at once. Before this check, the refusal was raised after
    ``os.replace`` had already written the store: every update on Windows was
    saved and then reported as a failure, and ``reconcile`` stopped after its
    first change.
    """
    if not _CAN_FLUSH_DIRECTORY:
        return
    dfd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _atomic_replace(content: str) -> None:
    """Replace the store's bytes atomically and durably. Caller holds the lock.

    Deliberately NOT delegated to ``file_lock.locked_write``: that helper takes
    its own ``FileLock(path, sidecar=True)``, and FileLock does not re-enter —
    nesting it inside the lock this module already holds blocks until the
    timeout and then raises.
    """
    directory = HYPOTHESIS_FILE.parent
    # mkstemp creates 0600; the store is 0644. Preserve its mode so a rewrite
    # does not silently narrow access the way the old open(..., "w") never did.
    mode = HYPOTHESIS_FILE.stat().st_mode & 0o777 if HYPOTHESIS_FILE.exists() else None
    fd, tmp = tempfile.mkstemp(
        dir=str(directory), prefix=f".{HYPOTHESIS_FILE.name}.", suffix=".tmp"
    )
    try:
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, HYPOTHESIS_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # os.replace is atomic with respect to the namespace, so no reader ever sees
    # a half-written store — but the rename itself is not durable across a power
    # loss until the containing directory is flushed too.
    _flush_directory(directory)


def _update_hypothesis(h: Hypothesis) -> dict:
    """Substitute exactly the one line carrying ``h.id``; copy every other verbatim.

    The invariant: **every line the caller did not target is copied through
    byte-for-byte, and the store is never re-serialised.** The previous version
    rewrote all 661 rows from lossily-parsed dataclass objects, so one call
    measurably changed fifteen rows — twelve of them unrelated to the id being
    written, and one destroyed outright because its id was shared.

    Multiplicity policy, since no identity scheme can make this store
    unambiguous on its own:

    * 0 matches   -> ``HypothesisNotFound``. Never a silent no-op: ``_verify_one``
      logs a verification immediately after this call, so a quiet no-op would
      assert a verdict the store does not carry.
    * 1 match     -> substitute that line.
    * n identical -> substitute the first and drop the rest, reporting how many.
      Leaving n-1 stale copies would manufacture the very divergence being fixed.
    * n divergent -> ``AmbiguousHypothesisId``, and write nothing.

    Returns ``{"id", "line", "collapsed"}``; ``line`` is 1-based.
    """
    # Refuse BEFORE the lock, same guard as register() -- see
    # _refuse_if_linked_worktree and LinkedWorktreeLedgerError.
    _refuse_if_linked_worktree("_update_hypothesis")
    HYPOTHESIS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Locked on the RESOLVED path: ~/.claude symlinks to ~/.grip, and the
    # sidecar lock is named from the path string, so both aliases must match.
    with FileLock(HYPOTHESIS_FILE.resolve(), sidecar=True):
        return _rewrite_hypothesis_row(h)


def _rewrite_hypothesis_row(h: Hypothesis) -> dict:
    """The locked rewrite behind ``_update_hypothesis``.

    Split out so the public function (long docstring; the multiplicity
    policy lives there) stays within the file's line-count ceiling. Caller
    already holds the sidecar FileLock.
    """
    lines = _read_raw_lines()
    idxs = _find_row_indices(lines, h.id)
    if not idxs:
        raise HypothesisNotFound(f"{h.id} is not in the store; nothing was written.")
    if len(idxs) > 1 and _rows_diverge(lines, idxs):
        raise AmbiguousHypothesisId(_ambiguity_report(lines, idxs, h.id))

    target, duplicates = idxs[0], set(idxs[1:])
    merged = _merge_row(json.loads(_content(lines[target])), h)
    rewritten = json.dumps(merged, separators=(",", ":")) + (
        _terminator(lines[target]) or "\n"
    )
    out = [
        rewritten if i == target else raw
        for i, raw in enumerate(lines)
        if i not in duplicates
    ]
    _atomic_replace("".join(out))
    return {"id": h.id, "line": target + 1, "collapsed": len(duplicates)}


def _assert_unambiguous(hid: str) -> None:
    """Raise before anything is mutated if ``hid`` cannot address one record.

    ``_update_hypothesis`` checks this again under the lock and THAT check is
    the authoritative one. This exists so an operator-facing caller reports the
    collision from its own frame rather than three frames down inside the writer.
    """
    lines = _read_raw_lines()
    idxs = _find_row_indices(lines, hid)
    if len(idxs) > 1 and _rows_diverge(lines, idxs):
        raise AmbiguousHypothesisId(_ambiguity_report(lines, idxs, hid))


def _skip_record(hid: str, exc: Exception) -> dict:
    """A row the writer refused to touch — recorded, never silently dropped."""
    return {"id": hid, "status": SKIPPED_STATUS, "reason": str(exc)}


# Rejected as a measure_plan. Each is a shrug wearing a plan's clothes, and
# accepting one would make the gate a formality. This is a FLOOR, not a proof:
# it cannot tell a real plan from a plausible sentence, and is not claimed to.
# What it does stop is the empty gesture, which is what 479 records needed.
_NON_PLANS = frozenset({
    "tbd", "todo", "n/a", "na", "none", "?", "-", "manual", "later",
    "unknown", "see above", "as discussed", "measure it", "somehow",
})


def _reject_unmeasurable(metric: str, measure_plan: str) -> None:
    """Raise unless this hypothesis can actually be graded one day.

    The single systemic cause behind #690: `register()` performed no
    validation at all, so a metric no collector knows was indistinguishable
    at write time from one that resolves. 97% of the store took the former
    path. A record that cannot be graded is not a falsifiable claim, and a
    falsification engine full of them is decoration.

    Two ways to pass, and they are the only two honest ones:
      1. the metric resolves -- a collector exists and will produce a number;
      2. a measure_plan says what must be run to obtain the value by hand.
    """
    if _metric_is_resolvable(metric):
        return
    plan = (measure_plan or "").strip()
    if plan and plan.lower().rstrip(".") not in _NON_PLANS and len(plan) >= 12:
        return
    known = ", ".join(sorted(_METRIC_RESOLVERS)) or "(none registered)"
    raise ValueError(
        f"metric {metric!r} has no collector, so this hypothesis could never "
        f"be graded.\n"
        f"  Either use a resolvable metric: {known}\n"
        f"  or pass measure_plan=... naming the command or procedure that "
        f"yields the value.\n"
        f"  A hypothesis nothing can measure is not falsifiable. See task "
        f"#690: 479 of 493 stored records took this path and 92 are now "
        f"permanently ungradeable."
    )


def register(
    claim: str,
    metric: str,
    prediction: str,
    pr_number: int = 0,
    baseline: float = 0.0,
    deadline: str = "",
    measure_plan: str = "",
) -> Hypothesis:
    """Register a new hypothesis.

    The read (`_next_id`) and write (`_save_hypothesis`) happen inside a
    sidecar FileLock so two concurrent registrations cannot observe the
    same max id and collide on H00N. Out-of-band JSONL editors are also
    serialised by the same lock because the next `register()` call
    re-scans the file under the lock.

    Args:
        claim: Human-readable claim about the change's impact
        metric: What metric to measure (must be observable)
        prediction: Expected direction/value ("increase", "decrease", "> 0.5", etc.)
        pr_number: The PR that introduces the change (0 if pre-PR or working-tree)
        baseline: Current metric value (for comparison)
        deadline: When to verify (ISO date). Empty means TODAY, so the
            hypothesis is due at once; pass a date.
    """
    # Refuse BEFORE the lock and before any id is consumed, so a rejected
    # registration leaves the store and the id sequence untouched.
    _refuse_if_linked_worktree("register")
    _reject_unmeasurable(metric, measure_plan)
    # Warn, never refuse: 32 of 113 stored free-text predictions were still
    # settled by a review or by hand, so free text is legal, just ungradable.
    ungradable = not prediction_is_gradable(prediction)
    if ungradable:
        warnings.warn(f"{UNGRADABLE_NOTE} Prediction: {prediction!r}.",
                      UngradablePredictionWarning, stacklevel=2)

    HYPOTHESIS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Resolved path: every writer must name the same lock file, or an append
    # here and a rewrite reached via the ~/.claude alias will not exclude
    # each other. See _update_hypothesis.
    with FileLock(HYPOTHESIS_FILE.resolve(), sidecar=True):
        h = Hypothesis(
            id=_next_id(),
            pr_number=pr_number,
            registered=datetime.now(timezone.utc).isoformat(),
            claim=claim,
            metric=metric,
            prediction=prediction,
            baseline=baseline,
            deadline=deadline or (datetime.now(timezone.utc).isoformat()[:10]),
            measure_plan=measure_plan,
            notes=UNGRADABLE_NOTE if ungradable else "",
        )
        _save_hypothesis(h)
        _log_registration(h.id)
    return h


def provenance_file() -> Path:
    """Beside the ledger, so a test that moves the ledger moves this file too."""
    return HYPOTHESIS_FILE.with_name(PROVENANCE_NAME)


def _log_registration(hid: str) -> None:
    """Append the session, model and branch that registered ``hid``.

    Best effort by design: lib/session_provenance.py never raises and returns
    empty fields when it cannot tell, so a registration never fails because its
    author is unknown (V>> 2026-11-16, the Reference Desk's provenance).
    """
    try:
        from session_provenance import current
        row = {"id": hid, "at": datetime.now(timezone.utc).isoformat(), **current()}
        with provenance_file().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    except (ImportError, OSError) as exc:
        logger.debug("provenance for %s not recorded: %s", hid, exc)



# The reviewer gives its score on the first line and its reasons after it
# (GRIP#9231), and a full reply runs to about 4,000 characters. A 500-character
# cut kept the score and lost the reasons: on 2026-11-16 the row for H952's
# disagreement stopped mid-word, so the objection could not be checked. The cap
# keeps a whole reply and still bounds the row.
_REVIEW_REASONING_CHARS = 8000


def _log_external_review(h, result, proposed_status: str, agreed) -> None:
    """Append which outside reviewer judged ``h``, and how, to VERIFICATION_FILE.

    Without this row the ledger keeps only the outcome, so it could not show
    that Groq, the fallback, judged H920 instead of Gemini (GRIP#9171).
    ``agreed`` is None when the reviewer gave no score. The row is provenance,
    never a gate: a write that fails is logged and the verdict is unchanged.
    """
    try:
        record = {
            "id": h.id,
            "kind": "external_review",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewer": f"{getattr(result, 'provider', '?')}/{getattr(result, 'model', '?')}",
            "score": getattr(result, "score", None),
            "risk": getattr(result, "hallucination_risk", None),
            "proposed": proposed_status,
            "agreed": agreed,
            "reasoning": str(getattr(result, "reasoning", "") or "")[:_REVIEW_REASONING_CHARS],
        }
        VERIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(VERIFICATION_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    except Exception as exc:
        logger.warning("[hypothesis] Could not record the outside review of %s: %s", h.id, exc)


def _external_validate_verification(h, actual: float, proposed_status: str):
    """Send verification to external reviewer for independent assessment.

    Returns ``(status_override, available)``:
      * ``(None, True)``  — external ran and AGREED with the proposed status.
      * ``(str,  True)``  — external ran and OVERRODE the status (e.g.
        marked ``inconclusive`` on low confidence / hallucination risk).
      * ``(None, False)`` — external could NOT run (import failed, network
        error, missing dependency). The caller should NOT stamp
        ``externally_validated=True`` in this branch.

    The two-value return replaces the older single-``Optional[str]`` shape
    that conflated "agreed" with "unavailable", which #3128 traced as the
    root cause of H668-H671 false-refutes: the engine stamped
    ``externally_validated=True`` even when validation didn't actually run.
    """
    try:
        from external_reviewer import review_output_sync, ReviewType
        text = (
            f"Hypothesis: {h.claim}\n"
            f"Metric: {h.metric}, Prediction: {h.prediction}\n"
            f"Baseline: {h.baseline}, Actual: {actual}\n"
            f"Proposed verdict: {proposed_status}"
        )
        result = review_output_sync(text, f"PR #{h.pr_number} hypothesis", ReviewType.CLAIM)
        if result.score is None:
            # No reviewer rated this. Reporting it as validated is the exact
            # conflation #3128 removed from this function's return shape, and
            # the old 0.5 placeholder re-introduced it downstream: 0.5 < 0.5 is
            # False, so an unreachable reviewer read as silent agreement AND
            # stamped externally_validated=True (#4502).
            logger.info("[hypothesis] External review returned no score for %s", h.id)
            _log_external_review(h, result, proposed_status, agreed=None)
            return None, False
        disagreed = result.score < 0.5 and result.hallucination_risk in ("medium", "high")
        _log_external_review(h, result, proposed_status, agreed=not disagreed)
        if disagreed:
            logger.info("[hypothesis] External disagreement: %s for %s", proposed_status, h.id)
            return "inconclusive", True
        return None, True
    except Exception as e:
        logger.warning("[hypothesis] External validation unavailable: %s", e)
        return None, False


def _apply_external_review(h, actual: float, status: str) -> tuple:
    """Run external validation and return (status, externally_validated)."""
    if status not in ("confirmed", "refuted"):
        return status, False
    ext_status, ext_available = _external_validate_verification(h, actual, status)
    if ext_status is not None:
        status = ext_status
    return status, ext_available


# The two causes a verdict can be downgraded for. Tokens, not prose: the
# guard needs to know THAT a cause exists without rendering a sentence, and
# only the recorder needs the words. Keeping them apart is what stops the
# guard constructing a message it discards -- an AGORA reviewer read that
# discarded string as a malformed note reaching the record (PR #6383). It
# could not, because the guard never returned it, but a design a careful
# reader misreads as a bug is worth changing anyway.
SENTINEL_NO_COLLECTOR = "no_collector"
SENTINEL_ZERO_VALUE = "zero_value"

_SENTINEL_TEXT = {
    SENTINEL_NO_COLLECTOR: (
        "inconclusive: no collector exists for metric {metric!r}, so the value "
        "graded was the 0.0 sentinel and not a measurement"
    ),
    SENTINEL_ZERO_VALUE: (
        "inconclusive: the registered collector for metric {metric!r} returned "
        "0.0, which an absent data file also returns"
    ),
}


def _sentinel_cause(status: str, actual: float, measured_externally_provided: bool,
                    *, metric_resolvable: bool = True) -> str:
    """Return WHICH cause forces a downgrade, or "" when the verdict stands.

    The single copy of the guard's branch logic. Both `_apply_sentinel_guard`
    and `_sentinel_downgrade_reason` derive their answers from this one, so a
    verdict and the reason recorded for it cannot drift apart — the same drift
    `test_registry_is_the_single_source_of_truth` exists to prevent one level
    down.
    """
    if status not in ("confirmed", "refuted") or measured_externally_provided:
        return ""
    if not metric_resolvable:
        # No collector exists for this metric name, so `actual` is the sentinel
        # regardless of its value. Keyed on the FACT so that a future change to
        # the sentinel (a non-zero default, or a resolver legitimately
        # returning 0.0) cannot silently disable the guard.
        return SENTINEL_NO_COLLECTOR
    if actual == 0.0:
        # Retained as defence-in-depth: a REGISTERED collector that returns 0.0
        # is often itself an unmeasured zero -- _metric_delight_composite
        # returns 0.0 when its data file is simply absent.
        return SENTINEL_ZERO_VALUE
    return ""


def _sentinel_downgrade_reason(status: str, actual: float,
                               measured_externally_provided: bool,
                               *, metric_resolvable: bool = True,
                               metric: str = "") -> str:
    """Render the downgrade cause as prose for the record, or "" if none.

    Written because the cause was computed and then discarded: measured on
    origin/main 2026-09-22, 48 of the 75 inconclusive rows in
    learning/hypotheses.jsonl (64%) carry an empty `notes`, so a later reader
    cannot tell an unmeasurable metric from one that genuinely measured zero.

    Scope, stated so it is not mistaken for more: this covers the SENTINEL
    downgrade only. A verdict `_evaluate_prediction` itself returns as
    "inconclusive" — an unparseable prediction string, say — is not a
    downgrade and gets no note from here.
    """
    cause = _sentinel_cause(
        status, actual, measured_externally_provided,
        metric_resolvable=metric_resolvable,
    )
    return _SENTINEL_TEXT[cause].format(metric=metric) if cause else ""


def _apply_sentinel_guard(status: str, actual: float, measured_externally_provided: bool,
                          *, metric_resolvable: bool = True) -> str:
    """Downgrade a provisional verdict to inconclusive when the metric was never resolved.

    Fires when the default resolver returned the 0.0 sentinel (metric unknown),
    regardless of whether external review ran (#3825 — extends #3128 to the
    use_external_review=False path). A caller-supplied measure_fn that genuinely
    measures 0.0 is unaffected (measured_externally_provided=True).
    """
    cause = _sentinel_cause(
        status, actual, measured_externally_provided,
        metric_resolvable=metric_resolvable,
    )
    return "inconclusive" if cause else status


def _append_note(existing: str, addition: str) -> str:
    """Append one note without clobbering what is already recorded.

    A verdict's reason is added to the record, never written over it: the
    existing `notes` on a row is often the operator's own account of the
    hypothesis and outranks anything the engine computes.
    """
    existing = (existing or "").strip()
    addition = (addition or "").strip()
    if not addition or addition in existing:
        return existing
    return f"{existing} | {addition}" if existing else addition


def _verify_one(h, measure_fn, use_external_review: bool) -> dict:
    """Measure, evaluate, and persist a single pending hypothesis."""
    measured_externally_provided = measure_fn is not None
    actual = measure_fn(h.metric) if measure_fn else _resolve_metric(h.metric)
    h.actual_value = actual
    h.verification_date = datetime.now(timezone.utc).isoformat()

    status = _evaluate_prediction(h.prediction, h.baseline, actual)
    if use_external_review:
        status, ext_validated = _apply_external_review(h, actual, status)
        if ext_validated:
            h.externally_validated = True
    resolvable = _metric_is_resolvable(h.metric)
    # The reason is computed from the same predicate the guard derives its
    # verdict from, so a downgrade can never be recorded without its cause,
    # nor a cause without the downgrade.
    h.notes = _append_note(h.notes, _sentinel_downgrade_reason(
        status, actual, measured_externally_provided,
        metric_resolvable=resolvable, metric=h.metric,
    ))
    status = _apply_sentinel_guard(
        status, actual, measured_externally_provided,
        metric_resolvable=resolvable,
    )
    h.status = status

    h.effect_size = (actual - h.baseline) / abs(h.baseline) if h.baseline != 0 else actual
    _update_hypothesis(h)
    _log_verification(h)

    return {
        "id": h.id,
        "claim": h.claim,
        "status": h.status,
        "baseline": h.baseline,
        "actual": actual,
        "effect_size": round(h.effect_size, 3),
    }


def verify(hypothesis_id: str = "", measure_fn=None, use_external_review: bool = True) -> dict:
    """Verify a hypothesis by measuring the predicted metric.

    Args:
        hypothesis_id: Specific hypothesis to verify (or "" for all due)
        measure_fn: Optional function that takes metric name and returns float.
                    If None, uses built-in metric resolvers.

    Returns:
        Dict with verification results.
    """
    hypotheses = _load_hypotheses()
    today = datetime.now(timezone.utc).isoformat()[:10]
    results = []

    for h in hypotheses:
        if h.status != "pending":
            continue
        if hypothesis_id and h.id != hypothesis_id:
            continue
        if not hypothesis_id and h.deadline > today:
            continue  # not yet due
        try:
            results.append(_verify_one(h, measure_fn, use_external_review))
        except HypothesisStoreError as e:
            # One unwritable row must not abandon the sweep: this runs
            # unattended from hooks/post-compact-hook.py on every compaction,
            # inside a broad `except Exception` that would flatten a raised
            # error into a one-line string and drop every remaining row.
            results.append(_skip_record(h.id, e))

    verified = [r for r in results if r.get("status") != SKIPPED_STATUS]
    return {
        "verified": len(verified),
        "skipped": len(results) - len(verified),
        "results": results,
    }


_METRIC_RESOLVERS: dict = {}  # populated below, after the collectors are defined


def _resolve_metric(metric_name: str) -> float:
    """Resolve a metric name to a current value.

    Built-in metrics -- this list must match `_METRIC_RESOLVERS` below, which
    is the single source of truth; `test_count` was registered there and went
    undocumented here, so a reader taking this docstring as the roster would
    have believed a resolvable metric was unresolvable:
    - delight_composite: latest DELIGHT v2 composite
    - genome_fitness: current genome fitness
    - hook_health: fraction of hooks firing
    - sentinel_coverage: events with sentinel records
    - test_count: number of test_*.py files on disk
    """
    resolver = _METRIC_RESOLVERS.get(metric_name)
    if resolver:
        return resolver()
    # Sentinel. NOTE this value is indistinguishable from a genuine measurement
    # of zero -- which is exactly why callers must ask _metric_is_resolvable()
    # rather than inferring "unmeasured" from the number.
    return 0.0  # unknown metric


def _metric_is_resolvable(metric_name: str) -> bool:
    """True iff a collector exists for this metric name.

    The fact, not a proxy for it. _resolve_metric returns 0.0 for an unknown
    metric, so "did we measure it?" cannot be answered from the value: a
    metric with no collector and a metric that genuinely measured zero return
    the same float.

    Measured 2026-08-14 on learning/hypotheses.jsonl: 493 records use metric
    names of which only 14 are resolvable, and the 124 past-deadline pending
    records use 121 distinct names of which ZERO are. Grading that cohort from
    the sentinel computes 76 REFUTED and 35 CONFIRMED before any guard runs --
    verdicts derived entirely from a number that means "no collector".
    """
    return metric_name in _METRIC_RESOLVERS


def _metric_delight_composite() -> float:
    delight_file = CLAUDE_DIR / "learning" / "delight_v2.jsonl"
    if not delight_file.exists():
        # Fall back to v1
        delight_file = CLAUDE_DIR / "learning" / "delight_snapshots.jsonl"
    if not delight_file.exists():
        return 0.0
    try:
        lines = delight_file.read_text().strip().splitlines()
        if lines:
            return json.loads(lines[-1]).get("composite", 0.0)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return 0.0


def _metric_genome_fitness() -> float:
    try:
        data = json.loads((CLAUDE_DIR / "cache" / "genome.json").read_text())
        history = data.get("fitness_history", [])
        return history[-1] if history else 0.0
    except (OSError, json.JSONDecodeError, IndexError):
        return 0.0


def _metric_hook_health() -> float:
    """Fraction of expected hook events that have sentinel records."""
    sentinel_log = CLAUDE_DIR / "cache" / "sentinel.jsonl"
    if not sentinel_log.exists():
        return 0.0
    expected = {
        "SessionStart", "PreToolUse", "PostToolUse",
        "UserPromptSubmit", "SessionEnd",
    }
    seen = set()
    try:
        for line in sentinel_log.read_text().splitlines()[-100:]:
            try:
                rec = json.loads(line)
                seen.add(rec.get("event", ""))
            except json.JSONDecodeError:
                continue
    except (OSError, UnicodeDecodeError):
        pass
    return len(seen & expected) / len(expected)


def _metric_sentinel_coverage() -> float:
    return _metric_hook_health()


def _metric_test_count() -> float:
    """Count test files in tests/ directory."""
    test_dir = CLAUDE_DIR / "tests"
    if not test_dir.exists():
        return 0.0
    return float(len(list(test_dir.glob("test_*.py"))))


# The metric registry. Single source of truth for BOTH "resolve this name" and
# "is this name resolvable at all" -- previously the dict was rebuilt inside
# _resolve_metric on every call, so any resolvability check would have had to
# duplicate the key list and could drift out of step with it.
_METRIC_RESOLVERS.update({
    "delight_composite": _metric_delight_composite,
    "genome_fitness": _metric_genome_fitness,
    "hook_health": _metric_hook_health,
    "sentinel_coverage": _metric_sentinel_coverage,
    "test_count": _metric_test_count,
})


# Unicode comparison operators (#3128 — H668-H671 were false-refuted because
# the regex matched `≤60` as bare-number (default `==`), inverting the verdict).
# Canonical ASCII operators are kept first; unicode aliases come after so the
# longest-match alternation picks them when present.
_UNICODE_OP_MAP = {"≤": "<=", "≥": ">=", "≠": "!="}
_NUMBER = r'(-?[0-9]+(?:\.[0-9]+)?)'
# A comparison with its number on the right: "> 0.8", "r < -0.3", "== 0". It
# outranks any number before it: "r^2 >= 0.7" means ">= 0.7" (H393 parsed as
# == 2.0). A lone "=" glued to a word is an assignment, not a comparison:
# "0 conflicts at n=3" predicts 0 (H026), not 3.
_RIGHT_COMPARISON_RE = re.compile(r'(>=|<=|==|≤|≥|≠|>|<|(?<!\w)=)\s*' + _NUMBER)
# A bound with its number on the left: "200<=count" bounds count from below.
_LEFT_COMPARISON_RE = re.compile(_NUMBER + r'\s*(>=|<=|≤|≥|>|<)\s*[a-z_]')
_FLIPPED = {">=": "<=", "<=": ">=", ">": "<", "<": ">"}
# A comparison written in words: "at least 20" (H956) means >= 20. Longer
# phrases first, so "no more than" is not read as "more than".
_WORD_OPS = {"no fewer than": ">=", "no less than": ">=", "no more than": "<=",
             "at least": ">=", "at most": "<=", "fewer than": "<",
             "less than": "<", "more than": ">", "greater than": ">", "exactly": "=="}
_WORD_COMPARISON_RE = re.compile(
    r'(?<![a-z])(' + "|".join(sorted(_WORD_OPS, key=len, reverse=True)) + r')[\s_]+' + _NUMBER)
# GRIP's compact forms: "le:0.10" (H093), "lte_3" (H398), "eq:1" (H250).
_CODE_OPS = {"lte": "<=", "le": "<=", "lt": "<", "gte": ">=", "ge": ">=", "gt": ">",
             "eq": "==", "ne": "!="}
_CODE_COMPARISON_RE = re.compile(r'(?<![a-z])(lte|gte|le|lt|ge|gt|eq|ne)[:_]\s*' + _NUMBER)
# A bare number is the predicted value only when it leads ("0 false
# positives"); in "criterion met within 2 waves" the 2 predicts nothing.
_LEADING_NUMBER_RE = re.compile(r'\s*' + _NUMBER)
# "false positives" and "true negatives" are counts, not a predicted sign:
# "0 false positives" graded as "actual > 0" before this (H050, H051).
_SIGN_NOUN_RE = re.compile(r'(?<![a-z])(?:false|true)[\s_-]*(?:positive|negative)s?(?![a-z])')
# A predicted sign. "non-negative" means >= 0; it graded as < 0 before this.
_SIGN_RE = re.compile(r'(?<![a-z])(non[\s_-]?)?(negative|positive)')
_SIGN_TESTS = {
    "negative": lambda a: a < 0,
    "positive": lambda a: a > 0,
    "non-negative": lambda a: a >= 0,
    "non-positive": lambda a: a <= 0,
}


def _parse_threshold(pred: str):
    """Extract (operator, threshold) from a prediction string.

    Handles '>= 90%', '> 75', 'SOLID score > 75% after decomp',
    '>=2 of 3: both prs merged', bare numbers, negatives. Also handles
    unicode operators ``≤``, ``≥``, ``≠`` (#3128) — H668-H671 were
    false-refuted because the original regex didn't match ``≤60`` and
    fell back to ``==`` default, inverting the verdict for a sentinel
    ``actual=0.0``.

    Returns (None, None) if no numeric threshold extractable.
    The first comparison wins, a left-hand bound read with its operator
    flipped. Failing that, a LEADING bare number defaults to ``==`` to avoid
    tautological confirmation on predictions like "0"; a number later in free
    text is not a prediction.
    """
    if not pred:
        return None, None
    found = _comparisons(pred)
    if found:
        return found[0]
    m = _LEADING_NUMBER_RE.match(pred)
    return ("==", float(m.group(1))) if m else (None, None)


def _comparisons(pred: str) -> list:
    """Every comparison a prediction states, as (operator, value) on the
    measured number: a right-hand number as written, a left-hand bound with its
    operator flipped. Unicode aliases are normalised so the comparator dispatch
    only needs ASCII."""
    right = [(_UNICODE_OP_MAP.get(op, op), float(n)) for op, n in _RIGHT_COMPARISON_RE.findall(pred)]
    left = [(_FLIPPED[_UNICODE_OP_MAP.get(op, op)], float(n)) for n, op in _LEFT_COMPARISON_RE.findall(pred)]
    words = [(_WORD_OPS[w], float(n)) for w, n in _WORD_COMPARISON_RE.findall(pred)]
    code = [(_CODE_OPS[c], float(n)) for c, n in _CODE_COMPARISON_RE.findall(pred)]
    return right + left + words + code


class UngradablePredictionWarning(UserWarning):
    """register() was given a prediction that verify() can only grade 'inconclusive'."""


UNGRADABLE_NOTE = (
    "ungradable prediction: verify() grades exactly one condition, a comparison "
    "such as '>= 0.5', a leading number, 'increase', 'decrease', 'positive' or "
    "'negative', so it grades free text, or two comparisons joined together, "
    "'inconclusive' unless a review or a manual verdict settles it"
)


def prediction_is_gradable(prediction: str) -> bool:
    """True when verify() can grade the prediction from a measured number.

    Free text such as "at least half of the seats vote KEEP" always grades
    'inconclusive' (H1009-H1020 did), and so do two conditions joined together.
    The test reuses _evaluate_prediction, whose only 'inconclusive' paths are
    those two, so the two functions can never disagree. The probe values 0.0
    and 0.0 are arbitrary: no gradable form ever answers 'inconclusive'.
    """
    return _evaluate_prediction(prediction or "", 0.0, 0.0) != "inconclusive"


def _sign_condition(pred: str) -> str:
    """The sign a prediction names ('negative', 'non-negative', ...), or "".

    The counts "false positives" and "true negatives" are set aside first.
    """
    m = _SIGN_RE.search(_SIGN_NOUN_RE.sub(" ", pred))
    if not m:
        return ""
    return ("non-" if m.group(1) else "") + m.group(2)


def _evaluate_prediction(prediction: str, baseline: float, actual: float) -> str:
    """Evaluate whether the prediction held.

    Supports:
    - "increase" / "decrease": direction vs baseline
    - "negative" / "positive" / "non-negative" / "non-positive": sign of actual
    - "> N", ">= N", "< N", "<= N", "= N": numeric threshold
    - Natural-language wrappers: 'SOLID score > 75% after decomp'
    - Composite: '>=2 of 3: both prs merged'

    One condition only. Two or more comparisons grade 'inconclusive', because
    one measured number cannot grade them: in "hubs<=3 AND cycles==0" (H054)
    each clause is about its own quantity, and a range such as
    "200<=count<=400" is two bounds. A sign word beside a single comparison
    describes it ("negative correlation (r < -0.3)"), so the comparison
    grades. With no comparison, a leading bare number grades; a sign word
    grades only when there is no number at all.
    """
    pred = prediction.strip().lower()
    if pred == "increase":
        return "confirmed" if actual > baseline else "refuted"
    if pred == "decrease":
        return "confirmed" if actual < baseline else "refuted"
    if len(_comparisons(pred)) > 1:
        return "inconclusive"
    op, threshold = _parse_threshold(pred)
    if threshold is None:
        sign = _sign_condition(pred)
        if not sign:
            return "inconclusive"
        return "confirmed" if _SIGN_TESTS[sign](actual) else "refuted"
    comparators = {
        ">=": lambda a, t: a >= t,
        ">":  lambda a, t: a > t,
        "<=": lambda a, t: a <= t,
        "<":  lambda a, t: a < t,
        "==": lambda a, t: a == t,
        "=":  lambda a, t: a == t,
        # ``≠`` is normalised to ``!=`` above (_UNICODE_OP_MAP); without this
        # entry every not-equal prediction fell through to "refuted" regardless
        # of actual — the #3128 unicode support was only two-thirds wired.
        "!=": lambda a, t: a != t,
    }
    fn = comparators.get(op)
    return "confirmed" if (fn and fn(actual, threshold)) else "refuted"


def _log_verification(h: Hypothesis) -> None:
    """Log verification result to JSONL."""
    VERIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": h.id,
        "verified_at": h.verification_date,
        "status": h.status,
        "claim": h.claim,
        "metric": h.metric,
        "baseline": h.baseline,
        "actual": h.actual_value,
        "effect_size": round(h.effect_size, 3),
        "pr": h.pr_number,
    }
    with open(VERIFICATION_FILE, "a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _experiment_rows(path: Path) -> list:
    """Every row of an experiment log, in file order; an unreadable line is skipped."""
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def record_experiment(hid: str, row: dict, path: Path | None = None) -> bool:
    """Record one experiment run for a hypothesis: beside it, never in it.

    The always-on tick's experiment act (lib/tick_experiment.py) runs a
    measurement the operator approved for an overdue hypothesis and calls
    this with what the run printed. The row goes to its own log
    (EXPERIMENT_FILE): the hypothesis store is read and never written, so a
    run cannot settle, change or reopen a verdict, and grading stays the
    operator's. Raises HypothesisNotFound for an id the store does not
    hold, and AmbiguousHypothesisId for an id that names more than one
    distinct record, as the verdict paths do, because a run recorded under
    such an id could not be attributed to one claim (Codex on GRIP#8856).
    Idempotent on the row's ``run``: a run already recorded is not appended
    again, and the answer is then False.
    """
    if not any(h.id == hid for h in _load_hypotheses()):
        raise HypothesisNotFound(hid)
    _assert_unambiguous(hid)
    target = path or EXPERIMENT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    record = dict(row, id=hid)
    with FileLock(target.resolve(), sidecar=True):
        if any(r.get("run") == record.get("run") for r in _experiment_rows(target)):
            return False
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
    return True


def status() -> dict:
    """Get hypothesis registry status."""
    hypotheses = _load_hypotheses()
    by_status = {"pending": 0, "confirmed": 0, "refuted": 0, "inconclusive": 0, "expired": 0}
    for h in hypotheses:
        by_status[h.status] = by_status.get(h.status, 0) + 1

    today = datetime.now(timezone.utc).isoformat()[:10]
    due = [h for h in hypotheses if h.status == "pending" and h.deadline <= today]

    # Grounded subset: verdicts whose metric actually has a collector.
    #
    # by_status counts every verdict, including those graded from the sentinel
    # _resolve_metric returns for an unknown metric -- a 0.0 indistinguishable
    # from a genuine measurement of zero, which is why _metric_is_resolvable
    # exists and why callers must ask it rather than inspect the value.
    #
    # Reported separately rather than replacing by_status, because both numbers
    # are true and they answer different questions: how many verdicts exist, and
    # how many of them rest on a measurement. A surface that publishes the first
    # while describing it as the second is the defect this split closes
    # (2026-09-20 release-readiness audit, blocker B3).
    grounded = {"confirmed": 0, "refuted": 0}
    for h in hypotheses:
        if h.status in grounded and _metric_is_resolvable(h.metric):
            grounded[h.status] += 1

    return {
        "total": len(hypotheses),
        "by_status": by_status,
        "grounded_by_status": grounded,
        "due_for_verification": len(due),
        "due_ids": [h.id for h in due],
        "confirmation_rate": (
            by_status["confirmed"] / max(by_status["confirmed"] + by_status["refuted"], 1)
        ),
    }


# =========================================================================
# Reconciliation loop — closes the hypothesis-engine learning loop.
#
# Issue #2557: the engine wrote predictions but had no daily reader to
# walk past-deadline PENDING hypotheses or harvest insights.jsonl tags.
# Without this loop, hypotheses rotted at PENDING indefinitely.
# =========================================================================

# Match "H<digits>: CONFIRMED" or "H<digits>: REFUTED" (case-insensitive,
# anchored on word-ish boundaries). Matches inside larger sentences.
_INSIGHT_TAG_RE = re.compile(
    r"\bH(\d{3,4})\s*:\s*(CONFIRMED|REFUTED)\b", re.IGNORECASE
)


def _scan_insights_for_tags(tail: int = RECONCILE_INSIGHT_TAIL) -> dict:
    """Read the latest `tail` insights and extract any H<id>: <verdict> tags.

    Returns {hypothesis_id: "confirmed" | "refuted"}. Last-write-wins when
    the same id appears multiple times. Single-pass scan over raw JSONL text:
    tags appear verbatim in JSON values, so the regex matches without parsing.
    O(n) over the bounded tail — no nested iteration.
    """
    if not INSIGHTS_FILE.exists():
        return {}
    try:
        text = INSIGHTS_FILE.read_text()
    except (OSError, UnicodeDecodeError):
        return {}
    buf = "\n".join(text.splitlines()[-tail:])
    return {
        f"H{int(m.group(1)):03d}": m.group(2).lower()
        for m in _INSIGHT_TAG_RE.finditer(buf)
    }


def _is_already_reconciled(h: Hypothesis) -> bool:
    """Idempotency check: don't re-reconcile a hypothesis we already touched."""
    return RECONCILE_NOTE_PREFIX in (h.notes or "")


def _with_reconcile_note(prior: str | None, note: str) -> str:
    """Append the sweep's note after any evidence the row already carries.

    Assigning the note instead erased the PARTIAL and BLOCKED evidence on
    H721-H723 on 2026-09-23 (restored by GRIP#7283).
    """
    kept = (prior or "").strip()
    return f"{kept} | {note}" if kept else note


def _reconcile_one(h: Hypothesis, tags: dict, today: str) -> dict | None:
    """Apply the reconcile policy to a single PENDING hypothesis.

    Order: explicit insight tag wins; otherwise PENDING past-deadline
    transitions to INCONCLUSIVE with a structured note, appended after any
    note the row already carries.
    """
    if h.status != "pending":
        return None
    if _is_already_reconciled(h):
        return None
    tag = tags.get(h.id)
    if tag in ("confirmed", "refuted"):
        h.status = tag
        h.notes = _with_reconcile_note(
            h.notes, f"{RECONCILE_NOTE_PREFIX}: insight tag H{h.id[1:]}: {tag.upper()}"
        )
        h.verification_date = datetime.now(timezone.utc).isoformat()
        _update_hypothesis(h)
        return {"id": h.id, "status": tag, "source": "insight"}
    if h.deadline and h.deadline <= today:
        h.status = "inconclusive"
        h.notes = _with_reconcile_note(
            h.notes,
            f"{RECONCILE_NOTE_PREFIX}: deadline elapsed ({h.deadline}); "
            "cannot infer outcome from elapsed time alone",
        )
        h.verification_date = datetime.now(timezone.utc).isoformat()
        _update_hypothesis(h)
        return {"id": h.id, "status": "inconclusive", "source": "deadline"}
    return None


def reconcile() -> dict:
    """Walk every PENDING hypothesis once and apply the reconcile policy.

    Idempotent: re-running without new evidence is a no-op (already-reconciled
    rows are skipped via the structured note prefix).
    """
    tags = _scan_insights_for_tags()
    today = datetime.now(timezone.utc).isoformat()[:10]
    transitions: list = []
    for h in _load_hypotheses():
        try:
            change = _reconcile_one(h, tags, today)
        except HypothesisStoreError as e:
            transitions.append(_skip_record(h.id, e))
            continue
        if change is not None:
            transitions.append(change)
    reconciled = [t for t in transitions if t.get("status") != SKIPPED_STATUS]
    return {
        "reconciled": len(reconciled),
        "skipped": len(transitions) - len(reconciled),
        "transitions": transitions,
    }


def save_status_line() -> str:
    """Single-line hypothesis status for /save digest output.

    Format: "Hypotheses: <due> due / <verified> verified / <pending> pending"
    """
    s = status()
    by = s["by_status"]
    verified = by.get("confirmed", 0) + by.get("refuted", 0) + by.get("inconclusive", 0)
    due = s["due_for_verification"]
    pending = by.get("pending", 0)
    return f"Hypotheses: {due} due / {verified} verified / {pending} pending"


# =========================================================================
# CLI
# =========================================================================

def _parse_flag(flag: str) -> str:
    """Return the value after --flag in sys.argv, or ''."""
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return ""


def _converted(flag: str, text: str, cast):
    """`cast(text)`, or exit 2 naming the flag. A bad number used to end in a
    raw ValueError traceback that did not say which argument was wrong. "inf"
    and "nan" are refused too: float() reads them, but no number flag means
    them, and _looks_like_a_flag already refuses "-inf" (GRIP#9692)."""
    try:
        value = cast(text)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(text)
        return value
    except ValueError:
        print(f"{flag} needs a number, not {text!r}; nothing was registered",
              file=sys.stderr)
        sys.exit(2)


#: register's options, read here and also by main()'s argument check, so the
#: two can never disagree about which flags register accepts.
_REGISTER_NUMBER_FLAGS = {
    "--pr": ("pr_number", int),
    "--baseline": ("baseline", float),
}
_REGISTER_TEXT_FLAGS = {"--claim": "claim", "--metric": "metric",
                        "--prediction": "prediction", "--deadline": "deadline",
                        "--measure-plan": "measure_plan"}


def _cmd_register() -> None:
    converters = _REGISTER_NUMBER_FLAGS
    str_flags = _REGISTER_TEXT_FLAGS
    kwargs: dict = {}
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        flag = args[i]
        if flag in converters and i + 1 < len(args):
            key, cast = converters[flag]
            kwargs[key] = _converted(flag, args[i + 1], cast); i += 2
        elif flag in str_flags and i + 1 < len(args):
            kwargs[str_flags[flag]] = args[i + 1]; i += 2
        else:
            i += 1
    h = register(**kwargs)
    print(f"Registered {h.id}: {h.claim}")
    print(f"  Metric: {h.metric} | Prediction: {h.prediction} | Deadline: {h.deadline}")
    _show_falsifiability_card(h)


def _show_falsifiability_card(h: Hypothesis) -> None:
    """Jev's advisory falsifiability reading for ``h`` (Phase 5.2). ``register()``
    above has already saved the row, so nothing here can affect it -- this is the
    only place in this module that may reach a network call, and only when the
    card's own ``enabled()`` says yes.

    Two import spellings, because this CLI is run both ways (see the module
    docstring's usage block, and README.md/commit-standards.md's
    PYTHONPATH=$HOME/.claude form): bare ``python3 lib/hypothesis_engine.py``
    puts only ``lib/`` on sys.path, where the bare name resolves; a caller that
    has already put the repo root on sys.path needs the ``lib.``-qualified
    name instead. Same two-step shape as ``hal_llm_adapter._shield_decision``.
    """
    try:
        try:
            from hypothesis_falsifiability import show_card
        except ImportError:
            from lib.hypothesis_falsifiability import show_card
        show_card(h)
    except Exception:  # noqa: BLE001 -- advisory only; registration already completed
        pass


def _cmd_verify() -> None:
    hid = _parse_flag("--id")
    result = verify(hid)
    print(f"Verified {result['verified']} hypotheses:")
    labels = {"confirmed": "OK", "refuted": "XX", "inconclusive": "??"}
    for r in result["results"]:
        # A skip record carries no measurements — printing 0.000 for a row that
        # was never measured would fabricate exactly the kind of number this
        # module exists to keep honest.
        if r["status"] == SKIPPED_STATUS:
            print(f"  [--] {r['id']}: SKIPPED — {r['reason']}")
            continue
        print(f"  [{labels.get(r['status'], '??')}] {r['id']}: {r['claim']}")
        print(f"       baseline={r['baseline']:.3f} actual={r['actual']:.3f} effect={r['effect_size']:.3f}")
    if result.get("skipped"):
        print(f"Skipped {result['skipped']}: an id that cannot address one record.")


def _cmd_status() -> None:
    s = status()
    print(f"Hypothesis Registry: {s['total']} total")
    for st, count in s["by_status"].items():
        if count > 0:
            print(f"  {st}: {count}")
    if s["due_for_verification"] > 0:
        print(f"  Due for verification: {s['due_ids']}")
    print(f"  Confirmation rate: {s['confirmation_rate']:.1%}")


def _cmd_falsify() -> None:
    hid = _parse_flag("--id")
    if not hid:
        print("Usage: hypothesis_engine.py falsify --id H001")
        return
    for r in verify(hid)["results"]:
        if r["status"] == SKIPPED_STATUS:
            print(f"{r['id']}: SKIPPED — {r['reason']}")
            continue
        print(f"{r['id']}: {r['status']} — {r['claim']}")


def _cmd_reconcile() -> None:
    """Daily reconcile: insight-tag harvest + past-deadline transitions."""
    result = reconcile()
    n = result["reconciled"]
    print(f"Reconciled {n} hypotheses:")
    for t in result["transitions"]:
        if t["status"] == SKIPPED_STATUS:
            print(f"  {t['id']}: SKIPPED — {t['reason']}")
            continue
        print(f"  {t['id']}: -> {t['status']} (source: {t['source']})")
    print(save_status_line())


_OVERRIDE_STATUSES = {"confirmed", "refuted", "inconclusive", "expired"}


def _append_override_audit(hid: str, status: str, evidence: str) -> dict:
    """Append one override record to VERIFICATION_FILE and return it.

    Extracted verbatim from override_verdict so that function fits the
    30-line CC ceiling. Pure move: same dict, same separators, same append
    mode, same mkdir. The returned dict is what the caller merges into its
    result, so nothing about the public shape changes.
    """
    audit = {
        "id": hid,
        "override_status": status,
        "override_evidence": evidence,
        "override_at": datetime.now(timezone.utc).isoformat(),
    }
    VERIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VERIFICATION_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(audit, separators=(",", ":")) + "\n")
    return audit


def override_verdict(hid: str, status: str, evidence: str = "") -> dict | None:
    """Manually override a hypothesis verdict (#3128 fix #3).

    Operator escape hatch for cases where the engine got it wrong — e.g.
    H668-H671 were mechanically refuted by the unicode-``≤`` + sentinel-0.0
    bug; the actual outcomes were confirmed. This lets the operator
    reconcile without hand-editing the JSONL.

    Behaviour:
      * Locates the hypothesis by id; returns ``None`` if unknown.
      * Sets ``status`` to the new verdict (must be in
        ``_OVERRIDE_STATUSES``) and ``externally_validated=True`` when
        evidence is provided.
      * Persists via the canonical ``_update_hypothesis`` (file-locked
        rewrite) — no schema widening on the ``Hypothesis`` dataclass.
      * Writes an audit entry to ``VERIFICATION_FILE`` so the override
        is traceable next session.

    Returns the post-override dict, or ``None`` when the hypothesis is
    unknown.
    """
    if status not in _OVERRIDE_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_OVERRIDE_STATUSES)}; got {status!r}"
        )
    for h in _load_hypotheses():
        if h.id != hid:
            continue
        # Refuse from THIS frame, before anything is mutated, so the operator
        # sees the colliding rows rather than an error from inside the writer.
        _assert_unambiguous(hid)
        h.status = status
        if evidence:
            h.externally_validated = True
        write = _update_hypothesis(h)
        audit = _append_override_audit(h.id, status, evidence)
        result = h.to_dict()
        result.update(audit)
        result["collapsed"] = write["collapsed"]
        return result
    return None


def _cmd_override() -> None:
    hid = _parse_flag("--id")
    status_val = _parse_flag("--status")
    evidence = _parse_flag("--evidence")
    if not hid or not status_val:
        print("Usage: hypothesis_engine.py override --id H001 --status confirmed "
              "--evidence \"PR #X merged in 25 min\"")
        sys.exit(1)
    try:
        record = override_verdict(hid, status_val, evidence)
    except (HypothesisStoreError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    if record is None:
        print(f"Hypothesis {hid} not found.")
        sys.exit(1)
    print(f"Overridden {hid}: {record.get('claim', '')[:80]}")
    print(f"  status: {record.get('override_status')}")
    if evidence:
        print(f"  evidence: {evidence}")


USAGE = "Usage: hypothesis_engine.py <register|verify|status|falsify|reconcile|override>"

_HELP_TOKENS = {"-h", "--help", "help"}


def _is_help_request(argv: list) -> bool:
    """True when argv asks for usage rather than for a command to run.

    Only the command slot (argv[1]) and the slot immediately after a command
    (argv[2]) are considered. A deeper `--help` is an option VALUE — e.g.
    `register --claim --help` — and must not be mistaken for a help request.
    """
    if len(argv) > 1 and argv[1] in _HELP_TOKENS:
        return True
    return len(argv) > 2 and argv[2] in {"-h", "--help"}


#: The flags each command reads, and whether each takes a value. Anything else
#: used to be ignored (2026-10-17): a misspelt `--deadline` registered a
#: hypothesis due TODAY, the default, and a misspelt `--evidence` recorded an
#: override with none. `verify --all` is documented and reads as "every due one".
_COMMAND_FLAGS = {
    "register": dict.fromkeys([*_REGISTER_NUMBER_FLAGS, *_REGISTER_TEXT_FLAGS], True),
    "verify": {"--id": True, "--all": False},
    "status": {},
    "falsify": {"--id": True},
    "reconcile": {},
    "override": {"--id": True, "--status": True, "--evidence": True},
}


#: Flags whose value is free text, so a value that starts with "-" is still a
#: value (`register --claim --help` registers that claim). Every other value
#: flag takes an id, a status, a number or a date, and only a negative number
#: among those starts with "-": there any other "-word" means the value was
#: left out, as in `falsify --id --reason "..."`, which used to run with the id
#: "--reason" and exit 0 (GRIP#9297), and `falsify --id -h` (GRIP#9684).
_FREE_TEXT_FLAGS = frozenset({"--claim", "--metric", "--prediction", "--measure-plan", "--evidence"})


#: A plain negative decimal, the only "-" value a number flag needs: "-5",
#: "-.5", "-5." or "-1.5e-3".
_NEGATIVE_DECIMAL = re.compile(r"-(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _looks_like_a_flag(word: str) -> bool:
    """True for a "-" word that is not a plain negative decimal: "-h",
    "--reason", "-.h", "-5x", "-inf", "-nan", "-1_000" or a bare "-". False for
    "-5", "-.5" or "-1e3", which --baseline may take. A pattern, not float():
    float() also reads "-inf", "-nan" and "-1_000" as numbers, so an id such as
    "-inf" was taken as a value (GRIP#9692)."""
    return word.startswith("-") and not _NEGATIVE_DECIMAL.fullmatch(word)


def _missing_value(flag: str, following: list) -> bool:
    """True when a value flag got no value: nothing follows it, or a flag that
    is not free text is followed by a word that looks like a flag."""
    return not following or (flag not in _FREE_TEXT_FLAGS and _looks_like_a_flag(following[0]))


def _argument_errors(cmd: str, args: list) -> list:
    """What in `args` the command would not read: an unknown token, or a flag
    given no value. A free-text flag consumes the next word whatever it looks
    like; any other value flag followed by a "--" word has no value, and that
    word is then checked as a flag of its own."""
    known = _COMMAND_FLAGS.get(cmd, {})
    errors, i = [], 0
    while i < len(args):
        token = args[i]
        if token not in known:
            errors.append(f"unknown argument {token!r}")
        elif known[token] and _missing_value(token, args[i + 1:i + 2]):
            errors.append(f"{token} needs a value")
            i += 1
            continue
        i += 2 if known.get(token) else 1
    return errors


def main():
    if len(sys.argv) < 2 or _is_help_request(sys.argv):
        print(USAGE)
        sys.exit(0 if _is_help_request(sys.argv) else 1)
    handlers = {
        "register": _cmd_register,
        "verify": _cmd_verify,
        "status": _cmd_status,
        "falsify": _cmd_falsify,
        "reconcile": _cmd_reconcile,
        "override": _cmd_override,
    }
    cmd = sys.argv[1]
    handler = handlers.get(cmd)
    if not handler:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
    errors = _argument_errors(cmd, sys.argv[2:])
    if errors:
        print(f"hypothesis_engine.py {cmd}: {'; '.join(errors)}; nothing was run\n{USAGE}",
              file=sys.stderr)
        sys.exit(2)
    handler()


if __name__ == "__main__":
    main()
