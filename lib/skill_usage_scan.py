"""Measure Skill-tool and slash-command invocations from Claude Code transcripts.

Scans JSONL session transcripts under ``<claude_dir>/projects/*/`` (recursively,
so subagent transcripts nested under a session's ``subagents/`` directory are
included) for two signals:

  * Skill-tool invocations -- an assistant ``tool_use`` content block named
    ``"Skill"``, keyed by its ``input.skill`` argument.
  * Slash-command invocations -- a user message whose text begins with ``/``
    (typed directly), OR wraps a Claude Code command in a
    ``<command-name>/name</command-name>`` tag (``lib/message_filters.py``
    names this exact wrapper) -- keyed by the token after the slash either
    way.

Built for the skills-archive PR: the brief requires counting usage "yourself
before trusting" a pre-computed figure, and requires doing so without reading
message content beyond the skill/command name
(``rules/personal-matters-stay-private.md`` -- some transcripts under
``projects/`` concern the operator's personal matters).

Privacy discipline, load-bearing: this module extracts ONLY tool names and the
skill/command argument. The slash-command regex match group is a short
command word, never the message; the surrounding text is never stored,
returned, or printed -- it is read into a local variable, matched once, and
discarded in the same function that reads it. No caller of this module can
recover message text through its return value, because none is ever put into
it.

Pure where it matters: ``scan_transcripts`` takes an explicit root and cutoff
so it is unit-testable against a small fixture tree without touching the real
corpus, and makes no network or write call.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

_SLASH_RE = re.compile(r"^/([A-Za-z0-9_:-]+)")

# Claude Code expands a slash command into the conversation wrapped in this
# tag, rather than leaving the literal "/name" at the start of the message --
# measured 2026-10-08: 986 of 4,326 transcript files carry this wrapper,
# more than the 886 carrying the plain leading-slash shape _SLASH_RE alone
# matched. A real invocation of ANY installed slash command, GRIP skill-
# backed or native, is recorded this way; missing it meant a skill used only
# through its registered command (never autonomously via the Skill tool)
# scanned as unused. Same capture shape as _SLASH_RE (the name, no slash).
_COMMAND_NAME_TAG_RE = re.compile(r"<command-name>/([A-Za-z0-9_:-]+)</command-name>")

# Transcripts live under <claude_dir>/projects/, never under state/; this
# directory name is excluded from the walk as belt-and-braces, even though it
# is not expected to appear under projects/ at all.
_NEVER_WALK_DIRNAME = "whatsapp-history"


@dataclass
class UsageReport:
    """Aggregated, content-free usage counts. No message text is ever held here."""

    tool_counts: Counter = field(default_factory=Counter)
    slash_counts: Counter = field(default_factory=Counter)
    files_scanned: int = 0
    files_skipped_stale: int = 0
    lines_inspected: int = 0

    def combined(self) -> Counter:
        """Union of tool-invocation and slash-invocation counts per name.

        A name invoked both ways (e.g. called as a Skill tool AND typed as a
        slash command) is counted under both and summed here -- the question
        this answers is "was this skill used at all, and how often", not
        "which surface used it".
        """
        merged: Counter = Counter()
        merged.update(self.tool_counts)
        merged.update(self.slash_counts)
        return merged

    def to_dict(self) -> dict:
        merged = self.combined()
        return {
            "tool_counts": dict(self.tool_counts),
            "slash_counts": dict(self.slash_counts),
            "files_scanned": self.files_scanned,
            "files_skipped_stale": self.files_skipped_stale,
            "lines_inspected": self.lines_inspected,
            "distinct_names": len(merged),
            "total_invocations": sum(merged.values()),
        }


def _user_text_block(content: object) -> str | None:
    """Return the first ``type: text`` block's text from a content list."""
    if not isinstance(content, list):
        return None
    for block in content:
        is_text_block = isinstance(block, dict) and block.get("type") == "text"
        candidate = block.get("text") if is_text_block else None
        if isinstance(candidate, str):
            return candidate
    return None


def _extract_user_text(content: object) -> str | None:
    """Return the first text the user actually typed, or None.

    Handles both transcript shapes: a bare string, or a list of content
    blocks where the first ``type: text`` block carries it. A ``tool_result``
    block (also sent with role ``user`` in the Anthropic Messages API) is
    skipped by construction -- it is not type ``text``.
    """
    if isinstance(content, str):
        return content
    return _user_text_block(content)


def _skill_tool_use_names(content: object) -> list[str]:
    """Names passed to every ``Skill`` tool_use block in an assistant content list."""
    if not isinstance(content, list):
        return []
    names = []
    for block in content:
        is_skill_call = (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "Skill"
        )
        tool_input = block.get("input") if is_skill_call else None
        skill = tool_input.get("skill") if isinstance(tool_input, dict) else None
        if isinstance(skill, str) and skill:
            names.append(skill)
    return names


def _fold_assistant_message(content: object, report: UsageReport) -> None:
    """O(1): single pass over one message's content blocks (bounded per-turn list)."""
    for skill in _skill_tool_use_names(content):
        report.tool_counts[skill] += 1


def _fold_user_message(content: object, report: UsageReport) -> None:
    """Match a slash-command token -- wrapped in Claude Code's <command-name>
    tag, or typed directly -- and discard the rest of the text.

    The tag is checked first: a wrapped message starts with ``<``, not
    ``/``, so a real command invocation recorded this way would otherwise
    be missed entirely rather than merely mismatched.
    """
    text = _extract_user_text(content)
    if not isinstance(text, str):
        return
    tag_match = _COMMAND_NAME_TAG_RE.search(text)
    if tag_match:
        report.slash_counts[tag_match.group(1)] += 1
        text = None  # never retained past this point
        return
    match = _SLASH_RE.match(text.lstrip())
    if match:
        report.slash_counts[match.group(1)] += 1
    text = None  # never retained past this point


def _process_line(line: str, cutoff_iso: str, report: UsageReport) -> None:
    """Parse one JSONL line and fold any Skill/slash invocation into ``report``.

    The caller has already applied a cheap substring pre-filter; this
    function does the precise, structural check. It stores nothing beyond a
    short matched token -- message text is read once, matched once, and
    never returned from this call stack.
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    timestamp = obj.get("timestamp")
    if isinstance(timestamp, str) and timestamp < cutoff_iso:
        return
    message = obj.get("message")
    if not isinstance(message, dict):
        return
    report.lines_inspected += 1
    role = message.get("role")
    content = message.get("content")
    if role == "assistant":
        _fold_assistant_message(content, report)
    if role == "user":
        _fold_user_message(content, report)


def _iso_to_epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def cutoff_iso_for_days(days: int) -> str:
    """The ISO-8601 UTC cutoff string for ``days`` ago, in transcript format.

    Transcript timestamps sort correctly as plain strings because every one
    uses the same zero-padded ``YYYY-MM-DDTHH:MM:SS.fffZ`` shape, so this
    avoids parsing a timestamp per line on the hot path.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _is_candidate_line(line: str) -> bool:
    """Cheap substring pre-filter -- reject most lines without parsing JSON."""
    if '"Skill"' in line:
        return True
    if "<command-name>" in line:
        return True
    if '"content":"/' in line:
        return True
    return '"text":"/' in line


def _all_jsonl_paths(root: Path) -> list[Path]:
    """Every ``*.jsonl`` path under ``root``, found in a single tree walk."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != _NEVER_WALK_DIRNAME]
        jsonl_names = (name for name in filenames if name.endswith(".jsonl"))
        found.extend(Path(dirpath) / name for name in jsonl_names)
    return found


def _jsonl_files_modified_since(
    root: Path, cutoff_epoch: float
) -> tuple[list[Path], int]:
    """Split every ``*.jsonl`` under ``root`` into (fresh, stale_count).

    Transcripts are append-only, so the newest line is never older than the
    file's own last-write time; a file whose mtime predates the cutoff cannot
    hold any in-window line and is excluded from ``fresh`` without being
    opened. One walk computes both halves so the tree is never visited twice.
    """
    all_paths = _all_jsonl_paths(root)
    fresh = [p for p in all_paths if _mtime_at_or_after(p, cutoff_epoch)]
    return fresh, len(all_paths) - len(fresh)


def _mtime_at_or_after(path: Path, cutoff_epoch: float) -> bool:
    try:
        return path.stat().st_mtime >= cutoff_epoch
    except OSError:
        return False


def _scan_one_file(path: Path, cutoff_iso: str, report: UsageReport) -> None:
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle as fh:
        candidate_lines = (raw for raw in fh if _is_candidate_line(raw))
        for raw in candidate_lines:
            _process_line(raw, cutoff_iso, report)


def scan_transcripts(root: Path, cutoff_iso: str) -> UsageReport:
    """Scan every ``*.jsonl`` under ``root`` (recursively) for Skill/slash use.

    ``root`` is normally ``<claude_dir>/projects``. ``cutoff_iso`` is an
    ISO-8601 UTC cutoff string (see ``cutoff_iso_for_days``).

    Three substring pre-filters (``_is_candidate_line``) keep the hot path
    cheap: a line must contain the literal ``"Skill"`` (a candidate tool
    invocation), ``<command-name>`` (a wrapped slash-command invocation), or
    a leading-slash content/text opener (a directly-typed slash command)
    before it is handed to ``_process_line`` for the precise, structural
    check. Everything else -- plain assistant prose, large tool outputs,
    hook attachment events -- is rejected by a pure substring scan and never
    parsed as JSON.
    """
    report = UsageReport()
    cutoff_epoch = _iso_to_epoch(cutoff_iso)
    fresh_paths, stale_count = _jsonl_files_modified_since(root, cutoff_epoch)
    report.files_skipped_stale = stale_count
    report.files_scanned = len(fresh_paths)
    for path in fresh_paths:
        _scan_one_file(path, cutoff_iso, report)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Measure Skill-tool and slash-command usage from transcripts"
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--root", default=None, help="Override the projects/ root (used by tests)"
    )
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else (get_claude_dir() / "projects")
    cutoff_iso = cutoff_iso_for_days(args.days)
    report = scan_transcripts(root, cutoff_iso)
    data = report.to_dict()

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    print(
        f"Scanned {data['files_scanned']} files modified in the last {args.days} days."
    )
    print(
        f"Distinct names invoked: {data['distinct_names']}  "
        f"Total invocations: {data['total_invocations']}"
    )
    for name, count in sorted(report.combined().items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {count:5d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
