"""Ultraprompt brief registry — discovery, parsing, and an executed-ledger.

Discovers next-session continuation briefs ("ultraprompts") written to
``<root>/plans/*.md`` and ``<root>/drafts/**/*.md`` across the current git
repository AND ``CLAUDE_DIR``, parses a compact summary out of each, and
tracks which have already been executed in a small local ledger.

Both the ultraprompt picker hook and the integration code code against the
public contract below; this module is the single source of truth.

Overridability (hermetic tests, containerised deploys)
------------------------------------------------------
Two environment variables override the on-disk locations at call time, so a
test can point discovery and the ledger at ``tmp_path`` without monkeypatching
module internals:

- ``GRIP_ULTRAPROMPT_ROOTS`` — ``os.pathsep``-separated list of search roots
  (replaces the default ``[repo_root, CLAUDE_DIR]``).
- ``GRIP_ULTRAPROMPT_LEDGER`` — absolute path to the executed-ledger JSON
  (replaces the default ``CLAUDE_DIR/state/ultraprompt-ledger.json``).

Pure standard library plus ``lib.claude_dir`` only.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

__all__ = [
    "UltrapromptBrief",
    "discover",
    "list_pending",
    "list_pending_with_total",
    "count_pending",
    "executed_shas",
    "is_executed",
    "mark_executed",
    "unmark",
]

# --- Parsing patterns (module-level: compiled once) -----------------------
_TITLE_PREFIX = "# "
# Match a byline the way a human writes one, not the way the parser wished.
# The old pattern accepted ONLY "**Written:** x". Three briefs authored by the
# session that was reading this file all used "*Written 2026-09-10 by ...*" and
# rendered as "written unknown". Emphasis is stripped before matching, so bold,
# italic, plain, with or without a colon, all work.
# The leading class keeps blockquote and list-item bylines working. The previous
# pattern was an unanchored search, so "> **Written:** x" matched; anchoring without
# this class silently regressed those briefs to "written unknown" (Codex, PR #5483).
_WRITTEN_RE = re.compile(r"^[\s>\-+]*Written\b\s*[:,-]?\s*(.+?)\s*$", re.IGNORECASE)
# A byline sits in the header. Bounding the scan stops prose deep in the document
# ("this was written by hand") from being read as one.
_WRITTEN_SCAN_LINES = 40
_TASK_RE = re.compile(r"^\s{0,3}\d+\.\s")
_EMPHASIS_RE = re.compile(r"[*_`]+")
_WHITESPACE_RE = re.compile(r"\s+")
_NAME_FILTER = "ultraprompt"  # case-insensitive basename substring
_TLDR_MARKER = "tldr"  # case-insensitive heading substring
_TASKS_MARKER = "numbered tasks"  # case-insensitive heading substring
_TLDR_MAX = 200
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class UltrapromptBrief:
    """A parsed ultraprompt continuation brief."""

    path: str
    rel: str
    title: str
    tldr: str
    written: Optional[str]
    task_count: int
    mtime: float
    sha256: str
    executed: bool
    git_tracked: bool


# --- Location resolution (call-time, env-overridable) ---------------------
def _repo_root() -> str:
    """Top level of the git repo containing this module.

    Falls back to ``CLAUDE_DIR`` then ``os.getcwd()`` on any git error, so
    discovery still works outside a checkout.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(_LIB),
            capture_output=True,
            text=True,
            timeout=5,
        )
        top = proc.stdout.strip()
        if proc.returncode == 0 and top:
            return top
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return str(get_claude_dir())
    except OSError:
        return os.getcwd()


def _search_roots() -> List[str]:
    """Resolved search roots, deduplicated by ``os.path.realpath``."""
    env = os.environ.get("GRIP_ULTRAPROMPT_ROOTS", "").strip()
    if env:
        raw = [p for p in env.split(os.pathsep) if p.strip()]
    else:
        raw = [_repo_root(), str(get_claude_dir())]
    seen: set = set()
    roots: List[str] = []
    for candidate in raw:
        real = os.path.realpath(os.path.expanduser(candidate))
        if real not in seen:
            seen.add(real)
            roots.append(real)
    return roots


def _ledger_path() -> Path:
    """Executed-ledger location (env override wins)."""
    env = os.environ.get("GRIP_ULTRAPROMPT_LEDGER", "").strip()
    if env:
        return Path(env).expanduser()
    return get_claude_dir() / "state" / "ultraprompt-ledger.json"


# --- Ledger read / atomic write (fail-open) -------------------------------
def _empty_ledger() -> dict:
    return {"version": 1, "executed": {}}


def _read_ledger() -> dict:
    """Load the ledger, returning an empty one on missing/corrupt input."""
    try:
        with open(_ledger_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):  # ValueError covers JSONDecodeError
        return _empty_ledger()
    if not isinstance(data, dict) or not isinstance(data.get("executed"), dict):
        return _empty_ledger()
    data.setdefault("version", 1)
    return data


def _write_ledger(ledger: dict) -> None:
    """Write the ledger atomically (temp file in the same dir + os.replace)."""
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".ultraprompt-ledger-", suffix=".tmp"
    )
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle, indent=2)
        os.replace(tmp, str(path))
        replaced = True
    finally:
        # Clean up the temp file ONLY when os.replace never moved it — after a
        # successful replace `tmp` no longer exists, so unconditionally
        # unlinking would double-delete (AGORA #4159 concern 2). Exceptions
        # propagate naturally; the caller (hook path) is fail-open.
        if not replaced:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# --- Field extractors -----------------------------------------------------
def _is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def _extract_title(lines: List[str], fallback: str) -> str:
    for line in lines:
        if line.startswith(_TITLE_PREFIX):
            return line[len(_TITLE_PREFIX):].strip()
    return fallback


def _is_byline(line: str) -> bool:
    """True if ``line`` reads as a Written-by provenance line."""
    return bool(_WRITTEN_RE.match(_EMPHASIS_RE.sub("", line)))


def _extract_written(text: str) -> Optional[str]:
    for line in text.split("\n")[:_WRITTEN_SCAN_LINES]:
        match = _WRITTEN_RE.match(_EMPHASIS_RE.sub("", line))
        if match:
            return match.group(1).strip()
    return None


def _clean_text(value: str) -> str:
    """Collapse whitespace and strip markdown emphasis markers.

    Emphasis stripping removes ``*``, ``_`` and backticks; this is a cosmetic
    preview transform, so occasional snake_case flattening is acceptable.
    """
    value = _EMPHASIS_RE.sub("", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def _truncate(value: str) -> str:
    if len(value) <= _TLDR_MAX:
        return value
    return value[:_TLDR_MAX] + "…"


def _paragraph_from(lines: List[str], start: int) -> Optional[str]:
    """First non-heading, non-blank paragraph at/after ``start``.

    Leading blank and heading lines are skipped; collection stops at the first
    blank or heading line after content begins.
    """
    buffer: List[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or _is_heading(line):
            if buffer:
                break
            continue
        buffer.append(stripped)
    return " ".join(buffer) if buffer else None


def _tldr_after_heading(lines: List[str]) -> Optional[str]:
    for idx, line in enumerate(lines):
        if _is_heading(line) and _TLDR_MARKER in line.lower():
            return _paragraph_from(lines, idx + 1)
    return None


def _paragraphs(lines: List[str]):
    """Yield each non-heading, non-blank paragraph in order.

    Mirrors ``_paragraph_from``'s notion of a paragraph -- headings and blanks
    separate, headings are never content -- but walks the whole document. An
    earlier version of the caller advanced a raw line index by hand while this
    skipping happened elsewhere, so the two went out of step and a header with
    one extra heading in it re-found the same byline until it gave up (Codex,
    PR #5483).
    """
    buffer: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or _is_heading(line):
            if buffer:
                yield " ".join(buffer)
                buffer = []
            continue
        buffer.append(stripped)
    if buffer:
        yield " ".join(buffer)


def _first_paragraph_that_is_not_a_byline(lines: List[str]) -> Optional[str]:
    """First real paragraph, skipping a leading Written-by provenance block.

    A byline says WHO wrote the brief. The tldr slot is meant to say WHAT it
    does, so falling back to the first paragraph put the least useful available
    line in the most prominent place.
    """
    for index, paragraph in enumerate(_paragraphs(lines)):
        if index >= 3:  # a header carries a byline, not a stack of them
            return None
        if not _is_byline(paragraph):
            return paragraph
    return None


def _extract_tldr(lines: List[str]) -> str:
    paragraph = _tldr_after_heading(lines) or _first_paragraph_that_is_not_a_byline(lines)
    if not paragraph:
        return ""
    return _truncate(_clean_text(paragraph))


def _extract_task_count(lines: List[str]) -> int:
    start = 0
    for idx, line in enumerate(lines):
        if _is_heading(line) and _TASKS_MARKER in line.lower():
            start = idx + 1
            break
    return sum(1 for line in lines[start:] if _TASK_RE.match(line))


def _git_tracked(path: str) -> bool:
    """Best-effort ``git ls-files --error-unmatch`` (False on any error)."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", os.path.basename(path)],
            cwd=os.path.dirname(os.path.abspath(path)),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _rel_path(path: str, root: str) -> str:
    try:
        return os.path.relpath(os.path.abspath(path), root)
    except (ValueError, OSError):
        return os.path.basename(path)


# --- Discovery ------------------------------------------------------------
def _candidate_paths(root: str) -> List[Path]:
    """Ultraprompt-named ``.md`` files under ``<root>/plans`` + ``drafts``."""
    base = Path(root)
    found: List[Path] = []
    try:
        found.extend((base / "plans").glob("*.md"))
        found.extend((base / "drafts").rglob("*.md"))
    except OSError:
        return []
    return [p for p in found if _NAME_FILTER in p.name.lower()]


def _root_candidates(root: str) -> List[Tuple[str, Path]]:
    """(root, path) pairs for one root — single-pass, no nesting."""
    return [(root, path) for path in _candidate_paths(root)]


def _parse_brief(path: str, root: str, executed_shas: set) -> UltrapromptBrief:
    """Parse one brief. Raises on unreadable/binary input (caller skips)."""
    with open(path, "rb") as handle:
        raw = handle.read()
    sha = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")  # binary/non-utf8 raises -> skipped by caller
    lines = text.splitlines()
    return UltrapromptBrief(
        path=os.path.abspath(path),
        rel=_rel_path(path, root),
        title=_extract_title(lines, os.path.basename(path)),
        tldr=_extract_tldr(lines),
        written=_extract_written(text),
        task_count=_extract_task_count(lines),
        mtime=os.path.getmtime(path),
        sha256=sha,
        executed=sha in executed_shas,
        git_tracked=_git_tracked(path),
    )


def discover() -> List[UltrapromptBrief]:
    """All discoverable briefs, deduped by sha256 (newest mtime wins).

    Sorted by mtime descending. A file that fails to read/parse is silently
    skipped — a single bad or binary file never crashes discovery. The
    per-root candidate lists are flattened into one stream so discovery runs
    a single pass (no nested loop over roots x files).
    """
    ledger = _read_ledger()
    executed_shas = set(ledger.get("executed", {}).keys())
    candidates = itertools.chain.from_iterable(
        map(_root_candidates, _search_roots())
    )
    by_sha: dict = {}
    for root, path in candidates:
        brief = _safe_parse(str(path), root, executed_shas)
        if brief is None:
            continue
        existing = by_sha.get(brief.sha256)
        if existing is None or brief.mtime > existing.mtime:
            by_sha[brief.sha256] = brief
    briefs = list(by_sha.values())
    briefs.sort(key=lambda b: b.mtime, reverse=True)
    return briefs


def _safe_parse(path: str, root: str, executed_shas: set) -> Optional[UltrapromptBrief]:
    """Parse a brief, returning None on any failure (bad/binary file)."""
    try:
        return _parse_brief(path, root, executed_shas)
    except Exception:  # noqa: BLE001 — intentional skip of unreadable files
        return None


def _check_limit(limit: Optional[int]) -> None:
    """Reject a negative page size where the SLICE happens.

    `int("-2")` parses, and `pending[:-2]` silently drops the OLDEST two and
    reports "(9 of 11 shown)" as though it were a page -- a typo yielding a
    plausible-looking wrong answer, the defect class this module was fixed
    for. Round 2 guarded only the CLI handler, which left the exported
    functions open to any library caller: the guard sat one layer above the
    code that misbehaves (AGORA council, PR #5076).
    """
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be zero or greater, got {limit}")


def list_pending_with_total(
    limit: Optional[int] = None,
) -> "tuple[List[UltrapromptBrief], int]":
    """``(page, total)`` from ONE discovery pass.

    Callers that show a count AND a page must use this rather than pairing
    :func:`list_pending` with :func:`count_pending`. Two passes cost twice
    (each walks every root and shells out to ``git ls-files`` per file) and,
    worse, are not one snapshot: a brief marked executed between the two
    reads makes the total smaller than the page, and an overflow computed as
    ``total - shown`` then goes NEGATIVE. Caught by the AGORA council on
    PR #5076 -- the agent context would have read "…and -1 more pending".
    """
    _check_limit(limit)
    pending = [brief for brief in discover() if not brief.executed]
    total = len(pending)
    return (pending if limit is None else pending[:limit]), total


def count_pending() -> int:
    """How many briefs are unexecuted. No cap, ever.

    The number a human is shown must come from here -- reporting the length
    of a PAGE as though it were a total is the defect this exists to make
    hard to reach for by accident.

    Never pair this with :func:`list_pending`: that is two discovery
    passes and a window in which the total can come back smaller than the
    page, so an overflow computed as ``total - shown`` goes NEGATIVE.
    A caller that needs a page AND a count must use
    :func:`list_pending_with_total`, which reads both from ONE discovery
    pass. (An earlier draft of this docstring invited exactly that pairing;
    the AGORA council on PR #5076 caught the module contradicting itself.)
    """
    return sum(1 for brief in discover() if not brief.executed)


def executed_shas() -> "set[str]":
    """Every sha256 the executed-ledger holds, as a plain set.

    The public accessor for what was previously reached only through the
    private ``_read_ledger()`` -- callers outside this module (the
    relevance-vs-picked measurement in ``lib.ultraprompt_relevance.report``)
    should not depend on that function's shape.
    """
    return set(_read_ledger().get("executed", {}).keys())


def list_pending(limit: Optional[int] = 6) -> List[UltrapromptBrief]:
    """Not-yet-executed briefs, newest first.

    ``limit`` PAGES the result and defaults to 6, the historical default.
    A mid-2026 change made it default to ``None`` (unbounded) so that
    ``len(list_pending())`` could never silently read as a total -- but
    that silently changed behaviour for every external caller that relied
    on the cap, so the AGORA council on PR #5076 restored 6. Pass
    ``limit=None`` explicitly when you want every pending brief.

    A caller that needs a page AND a count must use
    :func:`list_pending_with_total`, never this paired with
    :func:`count_pending` -- that is two discovery passes and a window in
    which the total can come back smaller than the page. (An earlier draft
    of this docstring recommended exactly that pairing, which is the defect
    `list_pending_with_total` exists to forbid; the AGORA council on PR #5076
    caught the module contradicting itself.)
    """
    _check_limit(limit)
    pending = [brief for brief in discover() if not brief.executed]
    return pending if limit is None else pending[:limit]


# --- Single-path helpers (describe / mark) --------------------------------
def _root_for(path: str) -> str:
    absolute = os.path.abspath(path)
    for root in _search_roots():
        if absolute.startswith(root + os.sep):
            return root
    return os.path.dirname(absolute)


def _describe_one(path: str) -> UltrapromptBrief:
    ledger = _read_ledger()
    executed = set(ledger.get("executed", {}).keys())
    return _parse_brief(path, _root_for(path), executed)


def is_executed(path: str) -> bool:
    """True when the file's current content hash is in the ledger."""
    try:
        with open(path, "rb") as handle:
            sha = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return False
    return sha in _read_ledger().get("executed", {})


def mark_executed(path: str, session_id: Optional[str] = None) -> UltrapromptBrief:
    """Record ``path`` as executed and return its brief (executed=True)."""
    brief = _describe_one(path)
    ledger = _read_ledger()
    ledger.setdefault("version", 1)
    ledger.setdefault("executed", {})
    ledger["executed"][brief.sha256] = {
        "path": brief.path,
        "marked_at_epoch": time.time(),
        "session_id": session_id,
        "title": brief.title,
    }
    _write_ledger(ledger)
    return replace(brief, executed=True)


def _content_sha(path: str) -> Optional[str]:
    try:
        with open(os.path.abspath(os.path.expanduser(path)), "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def unmark(sha_or_path: str) -> bool:
    """Remove a ledger entry by sha256 hex OR by path. True if one was removed."""
    ledger = _read_ledger()
    executed = ledger.get("executed", {})
    if _SHA_RE.match(sha_or_path) and sha_or_path in executed:
        del executed[sha_or_path]
        _write_ledger(ledger)
        return True
    target_sha = _content_sha(sha_or_path)
    target_abs = os.path.abspath(os.path.expanduser(sha_or_path))
    doomed = [
        key
        for key, entry in executed.items()
        if key == target_sha or _entry_path(entry) == target_abs
    ]
    for key in doomed:
        del executed[key]
    if doomed:
        _write_ledger(ledger)
    return bool(doomed)


def _entry_path(entry: object) -> Optional[str]:
    return entry.get("path") if isinstance(entry, dict) else None


# --- CLI ------------------------------------------------------------------
def _brief_to_dict(brief: UltrapromptBrief) -> dict:
    return asdict(brief)


def _json_wrapper(
    briefs: List[UltrapromptBrief], total: int, shown: Optional[int] = None
) -> str:
    """The ONE ``--json`` contract shared by ``list`` and ``pending``.

    Both subcommands emit a dict ``{"total": <int>, "briefs": [...]}`` so a
    machine consumer can never count entries and believe a page IS the
    whole set (this module's defect class, relocated into the
    machine-readable contract). ``pending`` additionally carries ``shown``,
    the page length, because it is the subcommand that pages. Round 4:
    ``list --json`` used to emit a bare array while ``pending --json``
    emitted this dict -- two top-level shapes in one CLI, with no version
    marker; now there is one shape.
    """
    payload = {"total": total, "briefs": [_brief_to_dict(b) for b in briefs]}
    if shown is not None:
        payload["shown"] = shown
    return json.dumps(payload, indent=2)


def _emit_briefs(briefs: List[UltrapromptBrief], as_json: bool) -> None:
    if as_json:
        print(_json_wrapper(briefs, len(briefs)))
        return
    if not briefs:
        print("(none)")
        return
    for brief in briefs:
        flag = "x" if brief.executed else " "
        print(f"[{flag}] {brief.title}  ({brief.rel})")


def _cli_list(rest: List[str]) -> int:
    _emit_briefs(discover(), "--json" in rest)
    return 0


def _cli_pending(rest: List[str]) -> int:
    raw, error = _flag_value_or_error(rest, "--limit", "5")
    if error:
        print(error, file=sys.stderr)
        return 2
    try:
        limit = int(raw) if raw is not None else None
    except ValueError:
        print(f"--limit expects an integer, got {raw!r}", file=sys.stderr)
        return 2
    try:
        # The rule lives in _check_limit, beside the slice it protects. This
        # only turns it into a friendly CLI message instead of a traceback.
        _check_limit(limit)
    except ValueError:
        print(f"--limit must be zero or greater, got {limit}", file=sys.stderr)
        return 2

    briefs, total = list_pending_with_total(limit=limit)

    if "--json" in rest:
        # The payload carries the total. A bare list lets a machine consumer
        # count entries and believe the page IS the total -- this PR's own
        # defect class, relocated into the machine-readable contract. Same
        # wrapper shape as `list --json` (see _json_wrapper); "shown" is
        # carried because pending is the subcommand that pages.
        print(_json_wrapper(briefs, total, shown=len(briefs)))
    else:
        _emit_briefs(briefs, False)

    # stderr in BOTH modes: a truncated listing must always say so.
    if len(briefs) < total:
        print(
            f"({len(briefs)} of {total} shown — "
            f"re-run without --limit for all {total})",
            file=sys.stderr,
        )
    return 0


def _cli_describe(rest: List[str]) -> int:
    if not rest:
        print("usage: describe <path>", file=sys.stderr)
        return 2
    try:
        brief = _describe_one(rest[0])
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"cannot describe {rest[0]}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(_brief_to_dict(brief), indent=2))
    return 0


class _MissingFlagValue(str):
    """Sentinel str: the flag was typed but carries no value.

    A ``str`` SUBCLASS rather than a bare ``object()`` so ``_flag_value``'s
    ``Optional[str]`` annotation is honest: the sentinel IS a str, and a
    type-checked consumer is never handed an ``object`` where the
    annotation promised ``str``. ``is`` comparisons still distinguish it
    from a real value, and it stays distinct from ``None`` (flag absent),
    so a bare ``--limit`` can be rejected instead of silently reading as
    "no limit" -- an operator typo used to produce unbounded output with
    no error (AGORA council, PR #5076).
    """

    def __repr__(self) -> str:
        return "_FLAG_MISSING_VALUE"


_FLAG_MISSING_VALUE = _MissingFlagValue()
#: Sentinel: the flag was typed but carries no value (see _MissingFlagValue).


def _flag_value(rest: List[str], flag: str) -> Optional[str]:
    """Value for ``flag``, accepting both ``--flag V`` and ``--flag=V``.

    Returns ``None`` when the flag is absent and
    :data:`_FLAG_MISSING_VALUE` when it is present with nothing after it.
    """
    prefix = flag + "="
    for i, token in enumerate(rest):
        if token.startswith(prefix):
            value = token[len(prefix):]
            return value if value else _FLAG_MISSING_VALUE
        if token == flag:
            if i + 1 < len(rest) and not rest[i + 1].startswith("--"):
                return rest[i + 1]
            return _FLAG_MISSING_VALUE
    return None


def _flag_value_or_error(
    rest: List[str], flag: str, example: str
) -> "tuple[Optional[str], Optional[str]]":
    """``(value, error)`` -- the guarded form every CLI handler must use.

    `_flag_value` returns a SENTINEL for a valueless flag, and a consumer
    that forgets to check it passes the sentinel where a real value
    belongs. That happened immediately: `_cli_mark` fed the sentinel to
    `json.dump` and `mark <path> --session` died with an uncaught
    ``TypeError: Object of type object is not JSON serializable``, where it
    used to record a null session and succeed (AGORA council, PR #5076).
    The sentinel is now a ``str`` subclass, so ``Optional[str]`` stays
    honest; guarding it remains mandatory.

    `tests/test_ultraprompt_registry.py` asserts structurally that every
    `_flag_value` call site guards the sentinel, so the next consumer cannot
    reintroduce it.
    """
    value = _flag_value(rest, flag)
    if value is _FLAG_MISSING_VALUE:
        return None, f"{flag} requires a value, e.g. {flag} {example}"
    return value, None


def _first_positional(rest: List[str]) -> Optional[str]:
    skip_next = False
    for token in rest:
        if skip_next:
            skip_next = False
            continue
        if token == "--session":
            skip_next = True
            continue
        if not token.startswith("--"):
            return token
    return None


def _cli_mark(rest: List[str]) -> int:
    path = _first_positional(rest)
    if not path:
        print("usage: mark <path> [--session <id>]", file=sys.stderr)
        return 2
    session, error = _flag_value_or_error(rest, "--session", "42")
    if error:
        print(error, file=sys.stderr)
        return 2
    try:
        brief = mark_executed(path, session)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"cannot mark {path}: {exc}", file=sys.stderr)
        return 1
    print(f"marked executed: {brief.title} ({brief.sha256[:12]})")
    return 0


def _cli_is_executed(rest: List[str]) -> int:
    if not rest:
        print("usage: is-executed <path>", file=sys.stderr)
        return 2
    return 0 if is_executed(rest[0]) else 1


def _cli_unmark(rest: List[str]) -> int:
    if not rest:
        print("usage: unmark <sha_or_path>", file=sys.stderr)
        return 2
    ok = unmark(rest[0])
    print("removed" if ok else "not found")
    return 0 if ok else 1


_CLI_HANDLERS = {
    "list": _cli_list,
    "pending": _cli_pending,
    "describe": _cli_describe,
    "mark": _cli_mark,
    "is-executed": _cli_is_executed,
    "unmark": _cli_unmark,
}


def _print_usage() -> None:
    print(
        "usage: ultraprompt_registry.py "
        "{list [--json] | pending [--json] [--limit N] | describe <path> | "
        "mark <path> [--session <id>] | is-executed <path> | "
        "unmark <sha_or_path>}\n"
        "--json: one shared contract for list and pending -- a JSON object "
        '{"total": <int>, "briefs": [...]}; "pending" also carries "shown" '
        "(the page length when --limit pages it).",
        file=sys.stderr,
    )


def _cli(argv: List[str]) -> int:
    if not argv:
        _print_usage()
        return 2
    handler = _CLI_HANDLERS.get(argv[0])
    if handler is None:
        _print_usage()
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
