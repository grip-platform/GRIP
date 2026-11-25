#!/usr/bin/env python3
"""Claude Code release feature scanner for GRIP auto-harnessing potential.

Parses structured release notes from JSON files and scores each feature
for GRIP relevance. Tracks harnessed vs unharnessed features to guide
RSI prioritisation of new Claude Code capabilities.

Every ``mark`` and ``defer`` also seals the decision into the IDR forest and
the memory chain through ``lib/harness_ledger_seal.py`` (GRIP#7969), so the
ledger line is backed by a signed leaf. Run it under the GRIP venv: the
``__main__`` block re-executes into a capable interpreter first.

Usage:
    bin/grip-py lib/cc_feature_scanner.py scan            # Score all features on disk (no network)
    bin/grip-py lib/cc_feature_scanner.py unharnessed      # Undecided features scoring >= HIGH_VALUE_CUTOFF
    bin/grip-py lib/cc_feature_scanner.py report           # Harnessed / deferred / undecided report
    bin/grip-py lib/cc_feature_scanner.py mark <ver> <title> <how>      # Record as harnessed, sealed
    bin/grip-py lib/cc_feature_scanner.py defer <ver> <title> <reason>  # Record as deferred, with a reason, sealed
    bin/grip-py lib/cc_feature_scanner.py reconcile [--apply]           # Re-point keys that match no feature
"""
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

# GRIP's canonical file lock, rather than an eleventh hand-rolled one — see
# `lib/file_lock.py`, which exists precisely to replace the patterns that kept
# being reinvented. Resolved relative to THIS file so a checkout under test
# loads its own copy instead of the shared tree's.
_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

# Bare sibling imports are this module's form: _LIB_DIR above puts lib/ itself
# on sys.path, where rea_auto_harness.py puts the repo root and writes `lib.X`.
from atomic_write import atomic_write  # noqa: E402
from file_lock import FileLock  # noqa: E402
from harness_ledger_seal import seal_harness_decision  # noqa: E402

# Portable base directory — respects CLAUDE_DIR env var
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
RELEASES_DIR = CLAUDE_DIR / "data" / "cc-releases"
HARNESS_STATE_FILE = RELEASES_DIR / "harness-state.json"

# ── Scoring heuristics ────────────────────────────────────────────────
# Each keyword group maps to a relevance score for GRIP auto-harnessing.
# Higher score = more directly useful to GRIP's hook/agent/memory systems.

KEYWORD_SCORES: list[tuple[list[str], int, str]] = [
    # (keywords, score, rationale)
    (
        ["hook", "lifecycle", "pretooluse", "posttooluse", "prehook",
         "posthook", "event", "callback", "interceptor"],
        30,
        "Hook/lifecycle: directly extensible by GRIP gates",
    ),
    (
        ["agent", "subagent", "task", "orchestrat", "spawn", "worker",
         "isolation", "worktree"],
        25,
        "Agent/subagent: GRIP dispatches its own agents",
    ),
    (
        ["memory", "persist", "session", "state", "serializ", "resurrect",
         "continuity", "checkpoint"],
        25,
        "Memory/persistence: session continuity is core GRIP",
    ),
    (
        ["mcp", "tool", "server", "protocol", "provider", "transport"],
        20,
        "MCP/tool: GRIP's MCP integration layer",
    ),
    (
        # GRIP#7974: 2.1.296's CLAUDE_CODE_OVERLOADED_RETRY_MAX_DELAY_MS scored 0
        # and never reached the undecided list. "retry" is a prefix, so retries
        # and retrying match; "rate" alone is not listed (it is too common).
        ["retry", "backoff", "overloaded", "529", "rate limit", "rate-limit",
         "ratelimit"],
        20,
        "Retry/backoff/overload: lib/cc_rate_limiter.py is the consumer",
    ),
    (
        ["context", "model", "token", "window", "extend", "compact",
         "1m", "200k"],
        15,
        "Context/model: GRIP context management",
    ),
    (
        ["ui", "display", "render", "markdown", "terminal", "output",
         "format"],
        10,
        "UI/display: lower priority for headless GRIP",
    ),
]

BUG_FIX_SCORE = 5

# One definition of "high value", shared by this scanner, cc_auto_harness and the
# skill page. A feature at or above it should end up harnessed, or deferred with
# a reason; it was 15 here and 20 everywhere else until 2026-10-09.
HIGH_VALUE_CUTOFF = 20


def _keyword_pattern(keywords: list[str]) -> re.Pattern:
    """Match a keyword only where a word starts.

    Plain substring tests scored "build" as UI (it contains "ui"), "prevent" as a
    hook event ("event") and "11ms" as context ("1m"). Anchoring at a word start
    keeps stems such as "orchestrat" and "serializ" working as prefixes.
    """
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + ")")


_KEYWORD_PATTERNS = [
    (_keyword_pattern(keywords), score, rationale)
    for keywords, score, rationale in KEYWORD_SCORES
]

# Leading scope tags on a release-note bullet: "[VSCode] ", "Windows: ",
# "Self-hosted runner: ", "Security: ". Stripped before reading the verb
# ("Added", "Changed", "Fixed") that decides how the bullet is scored.
_SCOPE_TAG_RE = re.compile(
    r"^(?:\[[^\]]{1,30}\]\s*"
    r"|(?!(?:added|changed|fixed|improved|removed)\b)[a-z][a-z \-]{0,24}:\s+)+"
)

VALIDATION_PASS_BOOST = 10
VALIDATION_FAIL_PENALTY = -15
VALIDATION_ADJUSTMENTS_FILE = RELEASES_DIR / "validation-adjustments.json"
COVERAGE_BOOST = 10
COVERAGE_JSON_FILE = CLAUDE_DIR / "data" / "coverage.json"

# Bookkeeping files that live alongside the release JSONs but are not releases.
# Kept as a module constant so the session pruner can import one shared source
# of truth instead of maintaining its own drifting copy of the same list.
NON_RELEASE_FILES = frozenset({
    "harness-state.json",
    "validation-adjustments.json",
    "harness-backlog.md",
})


def _ensure_dirs() -> None:
    """Create the releases directory structure if it does not exist."""
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)


def _load_harness_state() -> dict:
    """Load the harness tracking state file."""
    if HARNESS_STATE_FILE.exists():
        return json.loads(HARNESS_STATE_FILE.read_text(encoding="utf-8"))
    return {"harnessed": {}}


def _save_harness_state(state: dict) -> None:
    """Persist the harness tracking state.

    The write is atomic (temp file + ``os.replace``), so the two lock-free
    readers — `scan_releases` and `get_harness_report` — can never observe a
    half-written ledger. They see the whole old file or the whole new one.

    This is the UNLOCKED primitive. Anything that loads the ledger, changes it,
    and saves it back must go through `_harness_state_txn()` instead, or it
    will drop a concurrent writer's entry. Callers that merely stage a file
    outright, with nothing to preserve, can use this directly.
    """
    _ensure_dirs()
    atomic_write(
        HARNESS_STATE_FILE,
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
    )


# How long a writer waits for the ledger before giving up. Long enough to
# outlast any normal load-mutate-save (milliseconds), short enough that a
# wedged holder surfaces as a raised error rather than an indefinite hang.
HARNESS_LOCK_TIMEOUT_SEC = 10.0


@contextmanager
def _harness_state_txn(timeout: float = HARNESS_LOCK_TIMEOUT_SEC) -> Iterator[dict]:
    """Load, yield, then save the harness ledger under one exclusive lock.

    The ledger is a single shared file, and GRIP routinely runs several
    sessions against one working tree — so two writers arriving together is
    normal operation, not an edge case. Unlocked, the second writer's load
    happens before the first writer's save, and its own save then drops the
    first entry. Nothing raises and nothing looks wrong; the ledger is simply
    short a row, which is why this went unnoticed.

    Yields the mutable state. On a clean exit the state is written back only if
    the block actually changed it, so a preview-mode caller never touches the
    file. An exception propagates with nothing written.

    The lock is a SIDECAR (`<file>.lock`) because `_save_harness_state` ends in
    `os.replace`, which swaps the inode. A lock held on the ledger's own inode
    would, after that replace, guard a file no one can reach any more, and the
    next process to open the path would lock a different inode entirely — two
    writers each holding "the" lock, which is no lock at all.
    """
    _ensure_dirs()
    with FileLock(HARNESS_STATE_FILE, timeout=timeout, sidecar=True):
        state = _load_harness_state()
        before = json.dumps(state, sort_keys=True)
        yield state
        if json.dumps(state, sort_keys=True) != before:
            _save_harness_state(state)


def _harness_key(version: str, title: str) -> str:
    """Deterministic key for a feature in the harness state."""
    return f"{version}::{title}"


def _load_validation_adjustments() -> dict[str, int]:
    """Load validation score adjustments from disk."""
    if VALIDATION_ADJUSTMENTS_FILE.exists():
        try:
            return json.loads(
                VALIDATION_ADJUSTMENTS_FILE.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_validation_adjustments(adjustments: dict[str, int]) -> None:
    """Persist validation score adjustments."""
    _ensure_dirs()
    VALIDATION_ADJUSTMENTS_FILE.write_text(
        json.dumps(adjustments, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _get_uncovered_modules(threshold: float = 80.0) -> set[str]:
    """Return set of module stems with coverage below threshold.

    Tries two sources (first match wins):
    1. pytest-cov JSON report (data/coverage.json) — precise line coverage
    2. File heuristic — lib/*.py files without a matching tests/test_*.py
    """
    if COVERAGE_JSON_FILE.exists():
        try:
            data = json.loads(COVERAGE_JSON_FILE.read_text(encoding="utf-8"))
            files = data.get("files", {})
            uncovered = set()
            for fpath, info in files.items():
                pct = info.get("summary", {}).get("percent_covered", 100)
                if pct < threshold:
                    uncovered.add(Path(fpath).stem)
            return uncovered
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: file heuristic
    lib_dir = CLAUDE_DIR / "lib"
    test_dir = CLAUDE_DIR / "tests"
    uncovered = set()
    for py_file in lib_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        stem = py_file.stem
        test_file = test_dir / f"test_{stem}.py"
        if not test_file.exists():
            uncovered.add(stem)
    return uncovered


def adjust_score_from_validation(feature_id: str, passed: bool) -> None:
    """Update stored score adjustment for a feature based on validation outcome.

    Passing validations add a +10 boost; failing validations apply a -15
    penalty. The most recent validation result wins (not cumulative).

    Args:
        feature_id: The feature key in "version::title" format.
        passed: Whether the validation passed.
    """
    adjustments = _load_validation_adjustments()
    adjustments[feature_id] = (
        VALIDATION_PASS_BOOST if passed else VALIDATION_FAIL_PENALTY
    )
    _save_validation_adjustments(adjustments)


def _decided_result(
    feature: dict, version: str, key: str,
    harnessed_keys: set[str], deferred: dict[str, str],
) -> dict | None:
    """The scored row for a feature the ledger has already decided, else None.

    Harnessed and deferred features both score 0, because neither needs action;
    a deferred one carries its reason so the decision stays visible.
    """
    if key in harnessed_keys:
        return {**feature, "version": version, "score": 0,
                "rationale": "Already harnessed", "harnessed": True, "deferred": False}
    if key in deferred:
        return {**feature, "version": version, "score": 0,
                "rationale": f"Deferred: {deferred[key]}", "harnessed": False, "deferred": True}
    return None


def _score_feature(
    feature: dict,
    harnessed_keys: set[str],
    version: str,
    validation_adj: dict[str, int] | None = None,
    uncovered_modules: set[str] | None = None,
    deferred: dict[str, str] | None = None,
) -> dict:
    """Score a single feature for GRIP relevance.

    Returns the feature dict augmented with 'score', 'rationale', 'harnessed'
    and 'deferred' fields. Applies validation feedback adjustments when present.
    """
    title = feature.get("title", "")
    description = feature.get("description", "")
    category = feature.get("category", "feature").lower()
    key = _harness_key(version, title)

    decided = _decided_result(feature, version, key, harnessed_keys, deferred or {})
    if decided is not None:
        return decided

    # Detect implicit bug fixes from title prefix (many CC releases use flat lists).
    # The verb is read after any scope tag, so "[VSCode] Fixed ..." and
    # "Security: Fixed ..." are fixes too; they used to score 30 as hook features.
    title_lower = _SCOPE_TAG_RE.sub("", title.lower().strip())
    is_fix = category in ("fix", "bugfix") or title_lower.startswith("fixed ")

    if is_fix:
        return {
            **feature,
            "version": version,
            "score": BUG_FIX_SCORE,
            "rationale": "Bug fix: low harnessing priority",
            "harnessed": False,
            "deferred": False,
        }

    # Detect "Changed" items — improvements but generally lower priority
    is_change = title_lower.startswith("changed ")
    is_added = title_lower.startswith("added ")

    # Keyword matching — take the highest matching score
    combined = f"{title} {description}".lower()
    best_score = 0
    best_rationale = "No keyword match"

    for pattern, score, rationale in _KEYWORD_PATTERNS:  # O(1): 7 constant groups
        if score > best_score and pattern.search(combined):
            best_score = score
            best_rationale = rationale

    # Boost "Added" features (new capabilities), reduce "Changed" slightly
    if is_added and best_score > 0:
        best_score = min(best_score + 5, 35)
        best_rationale += " (+5 new capability)"
    elif is_change and best_score > 0:
        best_score = max(best_score - 5, 5)
        best_rationale += " (-5 behavioural change)"

    # Breaking changes get a +5 urgency bonus — they may require adaptation
    if category == "breaking":
        best_score += 5
        best_rationale += " (+5 breaking change urgency)"

    # Apply validation feedback adjustment
    if validation_adj and key in validation_adj:
        adj = validation_adj[key]
        best_score = max(0, best_score + adj)
        label = "pass boost" if adj > 0 else "fail penalty"
        best_rationale += f" ({adj:+d} validation {label})"

    # Coverage-driven boost: features matching uncovered modules score higher
    if uncovered_modules and best_score > 0:
        for module in uncovered_modules:  # O(1): bounded by lib/ file count
            if module.replace("_", " ") in combined or module in combined:
                best_score += COVERAGE_BOOST
                best_rationale += f" (+{COVERAGE_BOOST} uncovered: {module})"
                break

    return {
        **feature,
        "version": version,
        "score": best_score,
        "rationale": best_rationale,
        "harnessed": False,
        "deferred": False,
    }


def _release_files() -> list[Path]:
    """Return every release JSON, including any the session pruner archived.

    session_cleanup.py moves all but the most recent handful of release files
    into RELEASES_DIR/archive to keep the directory tidy. Those releases are
    still real history and must keep contributing to feature scoring, so the
    archive is read too. A live file always wins over an archived file of the
    same name, so a re-fetched release supersedes its archived copy.

    The live directory is listed BEFORE the archive, and that order is
    load-bearing rather than cosmetic. The pruner runs backgrounded from
    hooks/session-start.sh (the line ends in `&`) while the heartbeat scans,
    so a release genuinely can move live -> archive between these two listings.
    Reading the archive first loses such a file from both listings at once: it
    was not in the archive when the archive was read, and it is no longer live
    when the live directory is read. Nothing raises — the release simply is not
    in the corpus. Live-first makes that impossible, because the mover's only
    destination is the archive, so a file that escapes the live listing has
    already arrived in the archive that the later listing reads.
    """
    by_name: dict[str, Path] = {}
    for path in sorted(RELEASES_DIR.glob("*.json")):
        by_name[path.name] = path
    for path in sorted((RELEASES_DIR / "archive").glob("*.json")):
        by_name.setdefault(path.name, path)  # live still wins; archive fills gaps
    return [p for name, p in sorted(by_name.items()) if name not in NON_RELEASE_FILES]


def _read_release(path: Path) -> dict | None:
    """Load one release JSON, following a file the pruner moved mid-scan.

    shutil.move within a single filesystem is os.rename, and the archive is a
    subdirectory of the release directory, so a release being archived is at
    exactly one of two paths and is never observed half-written. If the listed
    path has already gone, the archive copy is the same file — read it rather
    than dropping the release from the corpus.

    Returns None only when the release is genuinely unreadable, which the caller
    reports rather than swallows.
    """
    candidates = [path]
    recovered = RELEASES_DIR / "archive" / path.name
    if recovered != path:
        candidates.append(recovered)

    for candidate in candidates:
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _incomplete_corpus_line(unreadable: list[str]) -> str:
    """One unmissable line stating that the scored corpus has holes in it."""
    return (
        f"CORPUS INCOMPLETE: {len(unreadable)} release(s) unreadable — "
        f"{', '.join(unreadable)}"
    )


_LEGACY_SKIP_RE = re.compile(r"^\s*SKIP:\s*", re.I)


def is_deferred(record: dict) -> bool:
    """Whether a ledger record decides NOT to harness its feature.

    `status: "deferred"` is the explicit form, written by `mark_deferred`.
    Records from before it existed carried the decision as a `how` beginning
    "SKIP:", and still count, so no historical decision is lost.
    """
    if record.get("status") == "deferred":
        return True
    return bool(_LEGACY_SKIP_RE.match(str(record.get("how", ""))))


def _deferral_reason(record: dict) -> str:
    """The stated reason for a deferral, without the legacy "SKIP:" prefix."""
    reason = str(record.get("reason") or record.get("how") or "")
    return _LEGACY_SKIP_RE.sub("", reason) or "no reason recorded"


def _split_ledger(ledger: dict) -> tuple[set[str], dict[str, str]]:
    """Harnessed keys, and deferred keys mapped to their reasons."""
    harnessed = {key for key, rec in ledger.items() if not is_deferred(rec)}
    deferred = {key: _deferral_reason(rec) for key, rec in ledger.items() if is_deferred(rec)}
    return harnessed, deferred


def scan_releases_report() -> tuple[list[dict], list[str]]:
    """Scored features, plus the names of release files that could not be read.

    The second element is the honest half. A release that vanished mid-scan is
    recovered by _read_release; anything still unreadable after that is a real
    hole in the corpus, and it is NAMED here rather than dropped. A silently
    shorter list of features is indistinguishable from a complete one, which is
    the whole failure this reporting exists to prevent.
    """
    _ensure_dirs()
    state = _load_harness_state()
    harnessed_keys, deferred = _split_ledger(state.get("harnessed", {}))
    validation_adj = _load_validation_adjustments()
    uncovered = _get_uncovered_modules()
    scored: list[dict] = []
    unreadable: list[str] = []

    for path in _release_files():
        data = _read_release(path)
        if data is None:
            unreadable.append(path.name)
            continue

        version = data.get("version", path.stem)
        features = data.get("features", [])

        for feature in features:  # O(1): bounded features per release (~30)
            scored.append(
                _score_feature(
                    feature, harnessed_keys, version, validation_adj, uncovered,
                    deferred,
                )
            )

    scored.sort(key=lambda f: f["score"], reverse=True)
    return scored, unreadable


def scan_releases() -> list[dict]:
    """Scan all release JSON files and return scored features.

    Returns a list of feature dicts sorted by score descending, each
    augmented with 'version', 'score', 'rationale', and 'harnessed'.
    Validation feedback adjustments are applied when available.

    An incomplete corpus is announced on stderr instead of being passed off as
    a complete one. Callers that need to act on the gap rather than merely hear
    about it use scan_releases_report.
    """
    scored, unreadable = scan_releases_report()
    if unreadable:
        print(f"[cc-scanner] {_incomplete_corpus_line(unreadable)}", file=sys.stderr)
    return scored


def get_unharnessed(min_score: int = HIGH_VALUE_CUTOFF) -> list[dict]:
    """Return undecided features scoring at or above the threshold.

    Deferred features are excluded even at min_score=0: they are decided, so
    listing them as work to do would re-open a closed decision.
    """
    return [
        f for f in scan_releases()
        if not f["harnessed"] and not f.get("deferred") and f["score"] >= min_score
    ]


# Filler words that carry no identity in a release-note title. Dropping them
# lets a human shorthand ("ConfigChange hook") match the bullet it refers to
# ("Added `ConfigChange` hook event that fires when ...").
_TITLE_STOPWORDS = frozenset({
    "the", "a", "an", "to", "for", "of", "in", "on", "and", "or", "is", "are",
    "added", "new", "support", "now",
})

# A shorthand must share at least this fraction of its words with a candidate
# before the last-resort overlap match will consider it the same feature.
_OVERLAP_THRESHOLD = 0.6

# Below this many identifying words, a shorthand is too generic to pin to one
# feature by anything other than an exact match.
_MIN_DISTINGUISHING_TOKENS = 2


def _title_tokens(title: str) -> set[str]:
    """Reduce a title to its identifying words, ignoring markup and filler."""
    lowered = title.lower().replace("`", " ")
    return {
        t for t in re.findall(r"[a-z0-9_.\-/]+", lowered)
        if t not in _TITLE_STOPWORDS
    }


def _resolve_by_overlap(
    tokens: set[str], candidates: list[str]
) -> tuple[str | None, str]:
    """Last resort: the one candidate sharing most of the shorthand's words."""
    ranked = sorted(
        ((len(tokens & _title_tokens(c)) / len(tokens), c) for c in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < _OVERLAP_THRESHOLD:
        return None, "unmatched"
    best = [c for score, c in ranked if score == ranked[0][0]]
    if len(best) != 1:
        return None, "ambiguous:{}".format(len(best))
    return best[0], "overlap:{:.2f}".format(ranked[0][0])


def resolve_feature_title(
    title: str, candidates: list[str]
) -> tuple[str | None, str]:
    """Map a possibly-shorthand title onto the exact release-note bullet.

    The harness key is the title string itself, so an entry recorded with a
    human shorthand can never match a scanned feature again. Resolving here
    means what gets stored is always the canonical title.

    Returns (canonical_title, how_matched). canonical_title is None when the
    shorthand cannot be pinned to exactly one feature — ambiguity is reported,
    never guessed, because a wrong match would mark the wrong feature done.
    """
    if title in candidates:
        return title, "exact"
    tokens = _title_tokens(title)
    if not tokens:
        return None, "empty"
    if len(tokens) < _MIN_DISTINGUISHING_TOKENS:
        # One generic word carries no identifying power. "hook" is a subset of
        # every hook bullet, so whichever happens to phrase it in the singular
        # wins by luck — and a wrong match marks the wrong feature done.
        return None, "too-generic"
    subset = [c for c in candidates if tokens <= _title_tokens(c)]
    if len(subset) == 1:
        return subset[0], "subset"
    if len(subset) > 1:
        return None, "ambiguous:{}".format(len(subset))
    return _resolve_by_overlap(tokens, candidates)


def _titles_by_version() -> dict[str, list[str]]:
    """Release-note titles grouped by version, for title resolution."""
    grouped: dict[str, list[str]] = {}
    for feature in scan_releases():
        grouped.setdefault(feature["version"], []).append(feature["title"])
    return grouped


def mark_harnessed(
    version: str,
    feature_title: str,
    how: str,
    titles_by_version: dict[str, list[str]] | None = None,
) -> dict:
    """Mark a feature as harnessed, keyed on its canonical release-note title.

    The title passed in may be a human shorthand. It is resolved against the
    release corpus first, so the key that gets stored is the same one
    scan_releases() computes. Without that step an entry recorded as
    "ConfigChange hook" could never match the bullet it referred to, and the
    feature stayed on the unharnessed list forever.

    An unresolvable title is still recorded — losing the fact that GRIP
    harnessed something is worse than storing an imperfect note — but it is
    flagged rather than filed as a key that can never match.

    Args:
        version: The Claude Code version string (e.g. "2.1.74").
        feature_title: Title of the feature; shorthand is resolved.
        how: Brief description of how GRIP harnessed this feature.
        titles_by_version: Precomputed corpus titles. Batch callers pass this
            so a bulk reconcile does not rescan the corpus once per entry.

    Returns:
        The outcome, so a caller can see whether the entry will match.
    """
    return _record_decision(version, feature_title, {"how": how}, titles_by_version)


def mark_deferred(
    version: str,
    feature_title: str,
    reason: str,
    titles_by_version: dict[str, list[str]] | None = None,
) -> dict:
    """Record a decision NOT to harness a feature, with the reason.

    A deferral is a closed decision, not a silent skip: the reason is required,
    is shown in the report, and keeps the feature off the unharnessed list. It
    replaces the old convention of a `how` that began "SKIP:".
    """
    if not reason.strip():
        raise ValueError("a deferral needs a reason")
    fields = {"status": "deferred", "reason": reason.strip()}
    return _record_decision(version, feature_title, fields, titles_by_version)


def _record_decision(
    version: str,
    feature_title: str,
    fields: dict,
    titles_by_version: dict[str, list[str]] | None,
) -> dict:
    """Write one ledger decision under the feature's canonical title."""
    grouped = _titles_by_version() if titles_by_version is None else titles_by_version
    canonical, matched_as = resolve_feature_title(
        feature_title, grouped.get(version, [])
    )
    stored_title = canonical or feature_title
    record = {
        "version": version,
        "title": stored_title,
        **fields,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "matched_as": matched_as,
    }
    if canonical is None:
        record["unresolved"] = True
        print(
            "warning: '{}' ({}) did not resolve to a release-note feature "
            "({}); recorded, but it will not match a scan".format(
                feature_title, version, matched_as
            ),
            file=sys.stderr,
        )
    elif canonical != feature_title:
        record["recorded_as"] = feature_title

    key = _harness_key(version, stored_title)
    with _harness_state_txn() as state:
        state["harnessed"][key] = record
    # The ledger line is a claim; the leaf and the memory record make it a
    # signed decision. Sealing raises if either cannot be read back, so a
    # decision never sits in the ledger silently unsealed (GRIP#7969).
    seal = seal_harness_decision("cc", key, record)
    return {"canonical": canonical, "matched_as": matched_as, "record": record, "seal": seal}


def _migrate_entry(
    harnessed: dict, key: str, record: dict, canonical: str, matched_as: str
) -> str:
    """Move one entry onto its canonical key, merging rather than clobbering.

    Two shorthand entries can resolve to the same feature — "Elicitation and
    ElicitationResult hooks" and "Added new Elicitation and ElicitationResult
    hooks" both name the 2.1.76 bullet, and they carry different notes, one of
    which cites its PR. Overwriting would silently drop that, so a collision
    keeps one record and files the other's note alongside it under
    `also_recorded`. No note is ever lost either way.

    WHICH record survives is decided by a stated rule. A harnessed decision
    outranks a deferral whatever the dates: if the work was done, an older "not
    now" is history, not the current state. Between two decisions of the same
    kind the EARLIEST date wins, ties going to the one already in place. It
    previously fell out of whichever entry the loop happened to reach first,
    which is stable for a given file (dicts preserve insertion order, and
    json.load preserves document order) but is not a *reason*; two ledgers
    holding the same pair in a different order would disagree, and neither
    answer would be defensible. Earliest-date is defensible: the first recording
    of a piece of work is its primary account, and later ones are corroboration.
    The displaced record's decision -- its `how`, or a deferral's status and
    reason -- is kept under `also_recorded`, so no decision is lost.

    Returns "moved" or "merged".
    """
    target = _harness_key(record.get("version", ""), canonical)
    existing = harnessed.get(target)
    harnessed.pop(key, None)
    if existing is None:
        migrated = dict(record)
        migrated["recorded_as"] = record.get("title", "")
        migrated["title"] = canonical
        migrated["matched_as"] = matched_as
        harnessed[target] = migrated
        return "moved"

    # Order the pair so the outcome does not depend on ledger layout: harnessed
    # before deferred, then by date. A missing date sorts last, because a dated
    # record is better evidence than one whose provenance was never written down.
    incoming = dict(record)
    incoming["title"] = canonical
    incoming["recorded_as"] = record.get("title", "")
    incoming["matched_as"] = matched_as
    primary, secondary = sorted(
        (existing, incoming),
        key=lambda r: (is_deferred(r), r.get("date") or "9999-99-99"),
    )
    if primary is not existing:
        # Carry forward anything the displaced record had already absorbed.
        primary.setdefault("also_recorded", []).extend(
            existing.get("also_recorded", [])
        )
        harnessed[target] = primary
    note = _decision_note(secondary)
    if any(note.values()) and note != _decision_note(primary):
        primary.setdefault("also_recorded", []).append({
            **note,
            "date": secondary.get("date"),
            "as": secondary.get("recorded_as") or secondary.get("title"),
        })
    return "merged"


def _decision_note(record: dict) -> dict:
    """The decision a ledger record carries: a deferral's reason, or its `how`."""
    if is_deferred(record):
        return {"status": "deferred", "reason": _deferral_reason(record)}
    return {"how": record.get("how")}


def _plan_reconciliation(
    harnessed: dict, grouped: dict, valid: set, apply: bool
) -> tuple[list[dict], list[dict]]:
    """Classify every unmatchable key, and migrate it in place when applying.

    Split out of `reconcile_harness_state` so that function stays a thin
    load-mutate-save inside one lock, with the per-entry decisions here.
    """
    repaired: list[dict] = []
    unresolved: list[dict] = []
    claimed: set[str] = set()

    for key, record in list(harnessed.items()):
        if key in valid:
            continue
        version = record.get("version", "")
        canonical, matched_as = resolve_feature_title(
            record.get("title", ""), grouped.get(version, [])
        )
        if canonical is None:
            unresolved.append({**record, "matched_as": matched_as})
            continue
        target = _harness_key(version, canonical)
        # A collision is another ENTRY already filed under this key, not merely
        # the key being a real feature — `valid` holds every corpus key, so
        # testing against it marks every repair as a merge.
        collides = target in harnessed or target in claimed
        claimed.add(target)
        repaired.append({
            "from": key,
            "to": target,
            "matched_as": matched_as,
            "outcome": "merged" if collides else "moved",
        })
        if apply:
            _migrate_entry(harnessed, key, record, canonical, matched_as)

    return repaired, unresolved


def reconcile_harness_state(apply: bool = False) -> dict:
    """Re-point harness entries whose key can no longer match any feature.

    Entries recorded before titles were resolved hold human shorthand, so
    their key matches nothing and the feature is reported as unharnessed for
    good. This resolves each one and rewrites its key, keeping the original
    `how` and `date` and recording what it used to be called.

    Defaults to a preview; pass apply=True to write.
    """
    grouped = _titles_by_version()
    valid = {
        _harness_key(version, title)
        for version, titles in grouped.items()
        for title in titles
    }
    # Same lock as `mark_harnessed`. A reconcile rewrites the very keys a
    # concurrent mark is adding, so locking one writer and leaving the other
    # unlocked would keep the lost update alive between the two of them. The
    # transaction writes only when something changed, which is what stops a
    # preview (`apply=False`) from touching the file at all.
    with _harness_state_txn() as state:
        harnessed = state.get("harnessed", {})
        repaired, unresolved = _plan_reconciliation(
            harnessed, grouped, valid, apply
        )

    return {
        "entries": len(harnessed),
        "repaired": repaired,
        "unresolved": unresolved,
        "applied": apply,
    }


def suggest_max_depth(feature: dict) -> int:
    """Suggest convergence depth for harnessing a feature.

    Simple heuristic based on score and category:
    - Score >= 25 or breaking: depth 4 (complex integration)
    - Score >= 15: depth 3 (moderate integration)
    - Score >= 10: depth 2 (light integration)
    - Otherwise: depth 1 (trivial or no integration)
    """
    score = feature.get("score", 0)
    category = feature.get("category", "").lower()

    if category == "breaking":
        return 4
    if score >= 25:
        return 4
    if score >= 15:
        return 3
    if score >= 10:
        return 2
    return 1


def _unharnessed_block(unharnessed: list[dict]) -> list[str]:
    """Report lines for the top unharnessed features, highest score first."""
    lines = ["", "Top Unharnessed (by score)", "-" * 50]
    for f in unharnessed[:15]:
        depth = suggest_max_depth(f)
        lines.append(
            f"  [{f['score']:>3}] {f['version']} - {f['title']}"
            f"  (depth: {depth}, {f['rationale']})"
        )
    return lines


def _harnessed_block(harnessed: list[dict]) -> list[str]:
    """Report lines for features already harnessed, with how and when."""
    lines = ["", "Harnessed Features", "-" * 50]
    state = _load_harness_state()
    for f in harnessed:
        info = state["harnessed"].get(_harness_key(f["version"], f["title"]), {})
        lines.append(
            f"  {f['version']} - {f['title']}  "
            f"({info.get('how', 'unknown')}, {info.get('date', 'unknown')})"
        )
    return lines


def _deferred_block(deferred: list[dict]) -> list[str]:
    """Report lines for features decided against, each with its reason."""
    lines = ["", "Deferred Features (decided not to harness)", "-" * 50]
    for f in deferred:
        lines.append(f"  {f['version']} - {f['title']}  ({f['rationale']})")
    return lines


def get_harness_report() -> str:
    """Generate a formatted report of harnessed, deferred and undecided features."""
    all_features, unreadable = scan_releases_report()
    if not all_features:
        empty = "No release notes found in {}.".format(RELEASES_DIR)
        return f"{empty}\n{_incomplete_corpus_line(unreadable)}" if unreadable else empty

    harnessed = [f for f in all_features if f["harnessed"]]
    deferred = [f for f in all_features if f.get("deferred")]
    unharnessed = [f for f in all_features if not f["harnessed"] and not f.get("deferred")]
    total = len(all_features)

    lines = [
        "CC Feature Harness Report",
        "=" * 50,
        f"Total: {total} | Harnessed: {len(harnessed)} | Deferred: {len(deferred)} | "
        f"Undecided: {len(unharnessed)} | Coverage: "
        f"{len(harnessed) / total * 100:.0f}%",
    ]
    if unreadable:
        # Immediately under the totals, so that percentage can never be read as
        # a complete picture when it is not one.
        lines.append(_incomplete_corpus_line(unreadable))
    if unharnessed:
        lines += _unharnessed_block(unharnessed)
    if deferred:
        lines += _deferred_block(deferred)
    if harnessed:
        lines += _harnessed_block(harnessed)

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────

def _cli_coverage() -> None:
    """Print modules lacking test coverage."""
    uncovered = sorted(_get_uncovered_modules())
    tested = sorted(
        p.stem for p in (CLAUDE_DIR / "lib").glob("*.py")
        if not p.name.startswith("_")
        and (CLAUDE_DIR / "tests" / f"test_{p.stem}.py").exists()
    )
    total = len(uncovered) + len(tested)
    pct = len(tested) / total * 100 if total else 0
    print(f"Test Coverage: {len(tested)}/{total} modules ({pct:.0f}%)")
    print(f"\nUncovered ({len(uncovered)}):")
    for m in uncovered:
        print(f"  {m}")
    print(f"\nCovered ({len(tested)}):")
    for m in tested:
        print(f"  {m}")


def _cli_scan() -> None:
    """Print all scored features."""
    features = scan_releases()
    if not features:
        print("No release notes found in {}".format(RELEASES_DIR))
        return
    for f in features:
        status = "H" if f["harnessed"] else " "
        print(
            f"[{status}] [{f['score']:>3}] {f['version']} - "
            f"{f['title']}  ({f['rationale']})"
        )


def _cli_unharnessed() -> None:
    """Print unharnessed features above default threshold."""
    features = get_unharnessed()
    if not features:
        print("All features harnessed (or no releases found).")
        return
    for f in features:
        depth = suggest_max_depth(f)
        print(
            f"[{f['score']:>3}] {f['version']} - {f['title']}"
            f"  (depth: {depth})"
        )


def _cli_report() -> None:
    """Print the full harness report."""
    print(get_harness_report())


def _cli_mark() -> None:
    """Mark a feature as harnessed from CLI arguments."""
    if len(sys.argv) < 5:
        print("Usage: cc_feature_scanner.py mark <version> <title> <how>")
        sys.exit(1)
    version = sys.argv[2]
    title = sys.argv[3]
    how = " ".join(sys.argv[4:])
    outcome = mark_harnessed(version, title, how)
    canonical = outcome["canonical"]
    if canonical is None:
        print(f"Recorded (UNRESOLVED, will not match a scan): {version} - {title}")
    elif canonical != title:
        print(f"Marked as harnessed: {version} - {canonical}")
        print(f"  (resolved from {title!r} by {outcome['matched_as']})")
    else:
        print(f"Marked as harnessed: {version} - {title}")
    _print_seal(outcome)


def _print_seal(outcome: dict) -> None:
    seal = outcome["seal"]
    print(f"  sealed: {seal['idr_id']} ({seal['custody']})")


def _cli_defer() -> None:
    """Record a decision not to harness a feature, with its reason."""
    if len(sys.argv) < 5:
        print("Usage: cc_feature_scanner.py defer <version> <title> <reason>")
        sys.exit(1)
    version, title, reason = sys.argv[2], sys.argv[3], " ".join(sys.argv[4:])
    outcome = mark_deferred(version, title, reason)
    shown = outcome["canonical"] or f"{title} (UNRESOLVED, will not match a scan)"
    print(f"Deferred: {version} - {shown}\n  reason: {reason}")
    _print_seal(outcome)


def _cli_reconcile() -> None:
    """Preview or apply repairs to harness entries whose key cannot match."""
    apply = "--apply" in sys.argv[2:]
    result = reconcile_harness_state(apply=apply)
    verb = "Repaired" if apply else "Would repair"
    print(f"{verb} {len(result['repaired'])} entr(ies); "
          f"{len(result['unresolved'])} could not be resolved.")
    for item in result["repaired"]:
        print(f"  [{item['matched_as']}] {item['from']}")
        print(f"      -> {item['to']}")
    for item in result["unresolved"]:
        print(f"  [UNRESOLVED:{item.get('matched_as')}] "
              f"{item.get('version')} - {item.get('title')}")
    if not apply and result["repaired"]:
        print("\nRe-run with --apply to write these changes.")


def main() -> None:
    """CLI entry point."""
    commands = {
        "scan": _cli_scan,
        "unharnessed": _cli_unharnessed,
        "report": _cli_report,
        "mark": _cli_mark,
        "defer": _cli_defer,
        "reconcile": _cli_reconcile,
        "coverage": _cli_coverage,
    }

    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"Usage: cc_feature_scanner.py {{{','.join(commands)}}}")
        sys.exit(1)

    commands[sys.argv[1]]()


if __name__ == "__main__":
    # `mark` and `defer` write signed records. Imported as top-level
    # `pq_reexec` off THIS checkout (lib/ is on sys.path above), the same form
    # lib/grip_save.py uses, so a system interpreter re-executes into one that
    # can dual-sign before any write.
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(Path(__file__))
    main()
