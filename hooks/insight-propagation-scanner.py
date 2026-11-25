#!/usr/bin/env python3
"""Rule 20 propagation scanner — Stop hook.

Scans the session transcript for ★ Insight blocks, classifies each per
Rule 20 (`rules/efficiency-rules.md`) categories, and surfaces any classified
as Design pattern / Behavioural rule / Operational discovery that were NOT
accompanied by a canonical-file edit in the same turn, before or after the
insight, or in the next turn. A turn runs from one prompt the operator typed
to the next, as `transcript_text.is_human` decides: tool results, hook
feedback, task notifications and compaction summaries are user rows too, and
they do not end it (GRIP#9083).

Output mirrors GRIP POWER nudge format so it slots into existing operator
attention budget. Advisory only — never blocks session close.

Claude Code Stop hook input (stdin):
    {"session_id": "...", "transcript_path": "/path/to/transcript.jsonl",
     "hook_event_name": "Stop", "stop_hook_active": false,
     "last_assistant_message": "..."}

The transcript is written separately and can lag behind the Stop event, so the
message the turn ends on may not be in it yet. The payload's own copy of that
message joins the scan as one more turn, unless the transcript already ends on
it (GRIP#9075).

Performance budget: < 1 second. Fast-paths when no ★ substring in transcript.
Exit code: 0 always.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from functools import lru_cache
from itertools import chain
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude")
LOG_FILE = CLAUDE_DIR / "logs" / "insight-propagation-scanner.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# GRIP's canonical file lock, rather than a hand-rolled one — `lib/file_lock.py`
# exists precisely to replace the locking patterns that kept being reinvented.
# Resolved relative to THIS file rather than $HOME, so a checkout under test
# loads its own copy instead of the shared tree's.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from file_lock import FileLock, LockTimeoutError  # noqa: E402

PENDING_FILE = CLAUDE_DIR / "state" / "pending-insights.md"
SIBLING_SCAN_WINDOW_SEC = 24 * 3600

# How long a closing session waits for the shared artefact before giving up.
# Long enough to outlast a normal merge-and-render (milliseconds), short enough
# that a wedged holder cannot noticeably delay session close.
PENDING_LOCK_TIMEOUT_SEC = 10.0

# Parses back the heading `write_pending_artefact` emits, so entries recorded
# by an earlier scan can be carried forward rather than overwritten.
_PENDING_ENTRY_RE = re.compile(r"^## \[(?P<category>[^\]]+)\] (?P<preview>.*)$")

PROPAGATION_HINTS = {
    "behavioural": "rules/efficiency-rules.md or new feedback memory",
    "design_pattern": "rules/<doctrine>.md or skills/<skill>/SKILL.md",
    "operational": "rules/operational-context.md (Tier-1 canonical)",
}

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("insight_propagation_scanner")


# Match both the bare form (`★ Insight ─...─`) and the canonical
# explanatory-style form (`` `★ Insight ─...─` ``) — the system prompt
# wraps divider lines in backticks so they render as inline code.
INSIGHT_RE = re.compile(
    r"`?\s*★ Insight ─+`?\s*\n(.*?)\n\s*`?─+`?",
    re.DOTALL,
)


# Every pattern below `behavioural` used to match only PRESCRIPTIVE
# rule-register prose — commanding verbs ("always use", "never skip") or nouns
# that explicitly NAME a rule ("as a rule", "golden rule", "discipline").
# Real insights are overwhelmingly DESCRIPTIVE: they state how a system
# actually behaves. There was no pattern family for that register at all, so
# genuine insights fell through to `conversation` and were discarded uncounted
# — a 3/3 miss rate on one live session's output. The `descriptive discovery`
# and `concrete artefact` groups under `operational` close that gap.
CATEGORY_PATTERNS = {
    "operational": [
        r"\bchannel id\b", r"\bDM id\b", r"\bkeychain\b",
        r"\bendpoint\b.*\b(URL|url)\b", r"\benv var\b",
        r"\benvironment variable\b", r"\bslack workspace\b",
        r"\bport \d+\b", r"\bhostname\b", r"\bbot token\b",
        r"\bworkspace=\w+\b", r"\bC0[A-Z0-9]{7,}\b",
        # Descriptive discovery: a fact about how a thing ACTUALLY behaves,
        # usually stated as a contrast between what it appears to say and what
        # it means. These carry no imperative verb, which is exactly why the
        # prescriptive patterns above could never see them.
        r"\bthe discriminator is\b",
        r"\bit (actually|really) means\b",
        r"\breads as\b",
        r"\blooks like\b[^.]{0,80}\b(but|not)\b",
        r"\bnever tells you\b",
        r"\b(tells|told) you\b[^.]{0,60}\bnot\b",
        r"\b(does|do|did|is|are|was|were)\s*n[o']?t\s+mean\b",
        # Concrete artefacts: an absolute or home-anchored path of at least
        # two segments, a service identifier, a stateful file, or a named
        # system tool. A relative mention like "tests/foo.py" deliberately
        # does NOT match — that shape appears in ordinary chatter.
        r"(?:^|\s)[~.]{0,2}/[\w.\-]+/[\w.\-/]+",
        r"\bcom\.[\w.]{4,}\b",
        r"\b[\w.\-]+\.(json|jsonl|plist|log|db|sqlite|pem|key)\b",
        r"\b(launchctl|launchd|systemd|crontab|pm2|git-crypt)\b",
        r"\borigin/(main|master)\b",
    ],
    "design_pattern": [
        r"\bshim\b", r"\bdoctrine\b", r"\barchitecture\b",
        r"\bcanonical\b.*\b(pattern|file|spec)\b",
        r"\babstraction\b", r"\bsyscall\b",
        r"\bcontract\b.*\b(API|interface|boundary)\b",
        r"\bproposer-checker\b", r"\bidiom\b",
        # Structural properties, stated descriptively.
        r"\bpure\b[^.]{0,40}\bfunctions?\b",
        r"\bby construction\b", r"\binvariant\b",
        r"\bprimitive\b", r"\bseam\b", r"\bidempotent\b",
    ],
    "behavioural": [
        # Prescriptive register: a rule being stated, not a behaviour observed.
        # The verb groups are wider than the originals, but deliberately hold
        # only verbs that carry an instruction — "never commits" is a
        # description of a file, not a rule, and must stay out.
        r"\balways\s+(do|use|prefer|read|run|verify|check|confirm|assert|test|resolve|ask|cite|ground)\b",
        r"\bnever\s+(do|use|skip|spawn|invoke|trust|assume|guess|blame|hardcode|rely)\b",
        r"\bdon'?t\s+(trust|assume|guess|blame|rely|skip)\b",
        r"\bcheck\s+[\w'\-]+(\s+[\w'\-]+){0,5}\s+before\b",
        r"\bprefer\s+[\w'\-]+(\s+[\w'\-]+){0,5}\s+over\b",
        r"\b(the\s+)?(antidote|remedy)\s+is\b",
        r"\bas a rule\b", r"\bgolden rule\b",
        r"\b(should|must)\s+(propagate|register|verify|check)\b",
        r"\brule \d+\b", r"\bdiscipline\b",
        r"\bclosed loop\b", r"\bpush-based\b",
    ],
    "code_change": [
        r"\bmissing test\b", r"\brefactor needed\b",
        r"\bwrapper needed\b",
    ],
    "doc_gap": [
        r"\bdoc(umentation)? gap\b",
        r"\bdoc(umentation)? (does\s*not|doesn'?t) exist\b",
    ],
}

# Categories that REQUIRE canonical propagation per Rule 20.
PROPAGATION_REQUIRED = {"operational", "design_pattern", "behavioural"}

# Canonical propagation targets, expressed RELATIVE to a repo root (below).
# They were previously anchored on a literal ".claude/" prefix, which is why
# this scanner could not see three of its own five targets — see canonical_roots().
CANONICAL_SUBPATH_PATTERNS = [
    re.compile(r"(?:^|/)rules/.+\.md$"),
    re.compile(r"(?:^|/)skills/[^/]+/SKILL\.md$"),
    re.compile(r"(?:^|/)CLAUDE\.md$"),
    re.compile(r"(?:^|/)happi\.md$", re.IGNORECASE),
]

# Project memory is matched wherever it lives, not only under a repo root.
# This is the one pattern that already worked, and it is kept root-agnostic so
# the fix below tightens nothing that currently passes.
MEMORY_PATH_PATTERN = re.compile(r"/memory/[^/]*\.md$")

_ROOT_BASENAMES = (".claude", ".grip", ".hal")


@lru_cache(maxsize=8)
def _roots_for(claude_dir: str) -> tuple[str, ...]:
    """Build the root set for a given ``$CLAUDE_DIR`` (cached — resolve() is I/O)."""
    bases = [Path(claude_dir)] if claude_dir else []
    bases += [Path.home() / name for name in _ROOT_BASENAMES]
    roots: list[str] = []
    for base in bases:
        roots.append(str(base))
        try:
            roots.append(str(base.resolve()))
        except OSError:
            pass
    # dict.fromkeys dedupes while preserving order (a non-symlinked root
    # yields the same string twice).
    return tuple(dict.fromkeys(roots))


def canonical_roots() -> tuple[str, ...]:
    """Every spelling of every root that can hold a canonical target.

    BOTH the literal and the fully-resolved spelling of each root are needed.
    ``~/.claude`` is a SYMLINK to ``~/.grip``, so a real edit is recorded as
    ``~/.grip/rules/x.md`` and could never match a hardcoded
    ``.claude/`` pattern — propagation to ``rules/``, ``skills/`` and
    ``CLAUDE.md`` therefore always read as NOT-propagated, nagging forever.
    Same defect class as the repo-protection gate fixed in #4150, fixed the
    same way. Keeping the literal spelling too preserves the fail-safe branch
    in ``_resolve``, where a path that cannot be resolved is still compared.

    ``$CLAUDE_DIR`` leads so a non-default install is covered
    (rules/path-portability.md); ``.grip`` is named because it is GRIP's real
    repository directory (rules/shared-tree-is-not-canonical.md).
    """
    return _roots_for(os.environ.get("CLAUDE_DIR") or "")


def _resolve(path: str) -> str:
    """Real path where possible; the plain expanded path when it cannot be."""
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


def _relative_to_canonical_root(resolved: str) -> str | None:
    """Path relative to the first canonical root containing it, else None.

    Compares on a path BOUNDARY, not a raw prefix: a bare ``startswith`` also
    matches a sibling that merely shares leading characters, so a
    ``~/.grip-backup`` tree would have been treated as the repo itself.
    """
    for root in canonical_roots():
        if resolved == root:
            return ""
        if resolved.startswith(root + os.sep):
            return resolved[len(root) + 1:]
    return None


def classify(insight_text: str) -> str:
    """Return the highest-priority category, or 'conversation' if no match.

    Priority order matters: behavioural > design_pattern > operational
    > code_change > doc_gap. A "rule about a pattern" classifies as
    behavioural so it lands in `rules/`.
    """
    text = insight_text.lower()
    for category in (
        "behavioural", "design_pattern", "operational",
        "code_change", "doc_gap",
    ):
        for pat in CATEGORY_PATTERNS[category]:
            if re.search(pat, text, re.IGNORECASE):
                return category
    return "conversation"


def is_canonical_path(path: str) -> bool:
    """True when ``path`` is a Rule 20 canonical propagation target.

    Resolves the path first so both spellings of a symlinked root agree, then
    matches the sub-path shape relative to whichever root contains it.
    """
    if not path:
        return False
    resolved = _resolve(path)
    if MEMORY_PATH_PATTERN.search(resolved):
        return True
    rel = _relative_to_canonical_root(resolved)
    if rel is None:
        return False
    return any(rx.search(rel) for rx in CANONICAL_SUBPATH_PATTERNS)


def extract_text_from_content(content) -> str:
    """Pull text from a message.content (list of blocks or str)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b.get("text", "") for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def collect_canonical_edits(content) -> list[str]:
    """Return file_paths of canonical-file Edit/Write/MultiEdit calls."""
    edits = []
    if not isinstance(content, list):
        return edits
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        if block.get("name") not in ("Edit", "Write", "MultiEdit"):
            continue
        path = (block.get("input") or {}).get("file_path", "")
        if is_canonical_path(path):
            edits.append(path)
    return edits


def _transcript_has_star(path: Path) -> bool:
    """Fast-path: True iff ★ (U+2605, UTF-8 E2 98 85) appears anywhere in file."""
    star = b"\xe2\x98\x85"
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(200_000)
                if not chunk:
                    return False
                if star in chunk:
                    return True
    except Exception as exc:
        logger.warning("fast-path read failed: %s", exc)
        return True  # fail open — let scan run rather than skip silently


def _insights_in(text: str) -> list[str]:
    """The body of every ★ Insight block in *text*, in order."""
    return [m.group(1).strip() for m in INSIGHT_RE.finditer(text or "")]


def _payload_text(payload: dict) -> str:
    """The Stop payload's own copy of the final message; "" when it has none or
    the shared reader cannot be imported (the transcript alone then decides)."""
    try:  # lib/ is on sys.path from the module header, as for file_lock
        from transcript_text import payload_message_text
    except ImportError:
        return ""
    return payload_message_text(payload)


def _new_turn() -> dict:
    return {"insights": [], "edits": []}


def _merge_payload(final_text: str, turns: list[dict]) -> None:
    """Add the payload's insights to the last turn, the one this Stop ends, unless
    the transcript already holds them there: it can lag the Stop event."""
    if not turns:
        turns.append(_new_turn())
    last = turns[-1]
    last["insights"] += [i for i in _insights_in(final_text) if i not in last["insights"]]


def _rows_of(path: Path):
    """The transcript's JSON objects, skipping lines that do not parse to one."""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if isinstance(entry, dict):
                yield entry


def _starts_a_turn():
    """lib/transcript_text.is_human, which tells a prompt the operator typed from
    every other user row. Beside an older lib/transcript_text.py that lacks it, no
    row starts a turn and the scan reads the transcript as one turn, so the Stop
    hook still exits 0 (Codex on GRIP#9084)."""
    try:  # lib/ is on sys.path from the module header, as for file_lock
        from transcript_text import is_human
    except ImportError:
        return lambda entry: False
    return is_human


def _collect_turns(path: Path) -> list[dict]:
    """Walk JSONL, return one {insights, edits} per turn, from one typed prompt to
    the next, so an edit anywhere in a turn counts for every insight in it. The
    rows before the first prompt form a turn too."""
    starts_a_turn = _starts_a_turn()
    turns = [_new_turn()]
    for entry in _rows_of(path):
        if starts_a_turn(entry):
            turns.append(_new_turn())
        elif entry.get("type") == "assistant":
            content = (entry.get("message") or {}).get("content")
            turns[-1]["insights"] += _insights_in(extract_text_from_content(content))
            turns[-1]["edits"] += collect_canonical_edits(content)
    return turns


def _finding(insight: str, turn: dict, next_turn: dict | None) -> dict:
    """Classify a single insight + check propagation in same or next turn.

    ALWAYS returns a finding, including a `conversation` one. Returning None
    for non-propagatable categories made a CLASSIFIER MISS invisible: a real
    insight the patterns failed to recognise vanished before it was counted,
    so the log read `total=0` — indistinguishable from a session that emitted
    no insights at all. Callers filter on `needs_propagation`; the count of
    what was dropped is now visible in the log line.
    """
    category = classify(insight)
    propagated = bool(turn["edits"]) or bool(next_turn and next_turn["edits"])
    return {
        "preview": insight[:140].replace("\n", " "),
        "category": category,
        "propagated": propagated,
        "needs_propagation": category in PROPAGATION_REQUIRED,
    }


def scan_transcript(transcript_path: Path, final_text: str = "") -> list[dict]:
    """Coordinate: fast-path check, JSONL walk, per-insight classification.

    *final_text* is the Stop payload's copy of the message the turn ends on. Its
    insights join the last turn when the transcript has not caught up with it,
    so the edits that turn made count for them too.
    """
    wanted = transcript_path.exists() and (
        bool(_insights_in(final_text)) or _transcript_has_star(transcript_path))
    turns = _collect_turns(transcript_path) if wanted else []
    _merge_payload(final_text, turns)
    pairs = (
        (insight, turn, turns[i + 1] if i + 1 < len(turns) else None)
        for i, turn in enumerate(turns)
        for insight in turn["insights"]
    )
    return [_finding(i, t, n) for i, t, n in pairs]


def write_pending_artefact(entries: list[dict]) -> None:
    """Render the pending-propagation entries to operator-visible markdown.

    Stop-hook stderr is suppressed by Claude Code's TUI, so this file is where
    a flagged insight actually survives the session that produced it.

    Its only reader today is `lib/weekly_rule20_triage.py`. It is NOT read by
    /recall and NOT surfaced at session start — the docstring claimed both for
    a long time, and neither was ever true (no SessionStart hook references it,
    and the triage module has never run on this machine). Wiring a reader is a
    separate, operator-gated decision.

    Pure renderer: it writes exactly the entries it is given. Deciding WHICH
    entries survive belongs to `merge_pending`, so that a scan which found
    nothing cannot erase another session's findings.
    """
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not entries:
        if PENDING_FILE.exists():
            PENDING_FILE.unlink()
        return
    n = len(entries)
    plural = "s" if n != 1 else ""
    lines = [
        "# Pending Insight Propagation (Rule 20)",
        "",
        f"_{n} insight{plural} flagged for canonical-file propagation._",
        "_Auto-refreshed by `hooks/insight-propagation-scanner.py` on Stop event._",
        f"_Last updated: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime())}_",
        "",
    ]
    for f in entries:
        cat = f["category"]
        target = PROPAGATION_HINTS.get(cat, "appropriate canonical file")
        lines.extend([
            f"## [{cat}] {f['preview']}",
            "",
            f"**Propagate to:** {target}",
            "",
        ])
    # Written via a temp file so the weekly triage never reads a half-written
    # artefact when a Stop hook fires underneath it.
    tmp = PENDING_FILE.with_name(PENDING_FILE.name + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, PENDING_FILE)


def read_pending_entries() -> list[dict]:
    """Parse entries previously written to the artefact back out of it."""
    try:
        text = PENDING_FILE.read_text(encoding="utf-8")
    except OSError:
        return []
    entries = []
    for line in text.splitlines():
        match = _PENDING_ENTRY_RE.match(line)
        if match:
            entries.append({
                "category": match.group("category"),
                "preview": match.group("preview"),
                "propagated": False,
                "needs_propagation": True,
            })
    return entries


def merge_pending(existing: list[dict], findings: list[dict]) -> list[dict]:
    """Carry entries forward; drop only those THIS scan proves were propagated.

    The Stop hook fires on EVERY session close, across every concurrent
    session. Previously a scan that found nothing in its own transcript
    unlinked the shared file outright, so session B erased session A's
    findings seconds after they were written — an expected artefact lifetime of
    about one Stop event, which is why the file was absent on the operator's
    machine despite 127 `unpropagated=1` runs in the log.

    A scan that saw nothing has PROVED nothing, so it now removes nothing. An
    entry leaves only on evidence: this scan saw that same insight alongside a
    canonical-file edit.
    """
    proven = {_finding_key(f) for f in findings if f["propagated"]}
    carried = [e for e in existing if _finding_key(e) not in proven]
    fresh = [f for f in findings if not f["propagated"]]
    return _dedupe_findings(carried + fresh)


def sync_pending_artefact(findings: list[dict]) -> list[dict]:
    """Read, merge, write — the only path that should touch the artefact.

    The whole read-merge-write is held under one exclusive lock. This hook
    fires once per session close, and GRIP routinely runs several sessions
    against one working tree, so two hooks arriving here together is normal
    operation rather than an edge case. Unlocked, session B's
    `read_pending_entries()` runs before session A's write lands, and B's own
    write then drops A's findings. Nothing raises; the file is simply short
    some entries — the same silent shortfall `merge_pending` was written to
    stop, reached by a different route.

    The lock is a SIDECAR (`<file>.lock`) because `write_pending_artefact`
    ends in `os.replace`, which swaps the inode. A lock held on the artefact's
    own inode would, after that replace, guard a file nobody can reach any
    more, and the next process to open the path would lock a different inode
    entirely — two writers each holding "the" lock, which is no lock at all.

    Raises `LockTimeoutError` if the lock cannot be acquired; the caller
    decides what to do about it, since silently skipping the write would be
    the very lost update this exists to prevent.
    """
    with FileLock(PENDING_FILE, timeout=PENDING_LOCK_TIMEOUT_SEC, sidecar=True):
        entries = merge_pending(read_pending_entries(), findings)
        write_pending_artefact(entries)
    return entries


def _safe_scan(path: Path) -> list[dict]:
    try:
        return scan_transcript(path)
    except Exception as exc:
        logger.warning("sibling scan of %s failed: %s", path.name, exc)
        return []


def _finding_key(finding: dict) -> tuple[str, str]:
    """Identity of a finding, shared by dedupe and by merge-forward.

    Both must agree, or an entry carried forward from the artefact would not
    match the same insight seen again in a later scan.
    """
    return (finding["category"], finding["preview"][:80])


def _dedupe_findings(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for f in items:
        key = _finding_key(f)
        if key not in seen:
            seen.add(key)
            merged.append(f)
    return merged


def sweep_recent_transcripts(
    project_dir: Path, exclude: Path | None
) -> list[dict]:
    """Bug 2 fix (flush race): merge findings from recently-closed transcripts.

    The Stop hook on the *current* turn races the JSONL writer for that turn's
    content. Sibling transcripts in the same project dir are fully flushed
    (their sessions ended), so re-scanning them on every fire catches any
    final-turn insights the originating Stop hook missed.
    """
    if not project_dir.is_dir():
        return []
    cutoff = time.time() - SIBLING_SCAN_WINDOW_SEC
    transcripts = [
        p for p in project_dir.glob("*.jsonl")
        if p != exclude and p.stat().st_mtime > cutoff
    ]
    all_findings = list(chain.from_iterable(_safe_scan(t) for t in transcripts))
    return _dedupe_findings(all_findings)


def render_message(unpropagated: list[dict]) -> str:
    n = len(unpropagated)
    lines = [
        f"[GRIP POWER] Insight propagation scan complete | "
        f"{n} insight{'s' if n != 1 else ''} need{'s' if n == 1 else ''} canonical-file propagation per Rule 20:",
    ]
    for f in unpropagated[:5]:
        lines.append(
            f"  • [{f['category']}] {f['preview']}"
        )
    if n > 5:
        lines.append(f"  ...and {n - 5} more")
    lines.append(
        "Act in next session or schedule a follow-up agent. "
        "Categories: operational → rules/operational-context.md; "
        "design_pattern → rules/<doctrine>.md or skills/; "
        "behavioural → rules/efficiency-rules.md."
    )
    return "\n".join(lines)


def _sync_pending_or_warn(actionable: list[dict]) -> None:
    """Record the findings, or say loudly that they were not recorded.

    This hook is advisory and must never hold up session close, so a lock it
    cannot get is caught rather than raised. It is NOT swallowed: a write that
    vanishes quietly is the same lost update the lock exists to prevent, just
    reached from the other side. So it goes to the log AND to stderr, where the
    operator already reads this hook's output.
    """
    try:
        sync_pending_artefact(actionable)
    except LockTimeoutError as exc:
        logger.error(
            "pending artefact NOT updated — %d finding(s) dropped: %s",
            len(actionable), exc,
        )
        print(
            f"[GRIP POWER] Insight propagation: {len(actionable)} finding(s) "
            f"NOT recorded — {PENDING_FILE.name} stayed locked by another "
            f"session for {PENDING_LOCK_TIMEOUT_SEC:g}s.",
            file=sys.stderr,
        )


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception as exc:
        logger.warning("stdin parse failed: %s", exc)
        return 0

    transcript_path_raw = payload.get("transcript_path") or ""
    transcript_path = Path(transcript_path_raw) if transcript_path_raw else None

    findings: list[dict] = []
    project_dir: Path | None = None

    if transcript_path:
        project_dir = transcript_path.parent
        try:
            findings = scan_transcript(transcript_path, _payload_text(payload))
        except Exception as exc:
            logger.exception("scan failed: %s", exc)

    if project_dir:
        sibling = sweep_recent_transcripts(project_dir, exclude=transcript_path)
        findings = _dedupe_findings(findings + sibling)

    actionable = [f for f in findings if f["needs_propagation"]]
    unpropagated = [f for f in actionable if not f["propagated"]]
    conversation = len(findings) - len(actionable)
    name = transcript_path.name if transcript_path else "<no-transcript>"
    # `insights` counts EVERY ★ block found and `conversation` counts those the
    # patterns could not classify. Logging only the actionable total made a
    # classifier miss read identically to a session with no insights at all.
    logger.info(
        "scanned %s | insights=%d actionable=%d propagated=%d unpropagated=%d conversation=%d",
        name, len(findings), len(actionable),
        len(actionable) - len(unpropagated), len(unpropagated), conversation,
    )

    # Pass the actionable findings, not just the unpropagated ones: the
    # propagated ones are the evidence that clears a carried-forward entry.
    _sync_pending_or_warn(actionable)

    if not unpropagated:
        return 0

    print(render_message(unpropagated), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
