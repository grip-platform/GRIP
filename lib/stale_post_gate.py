"""Stale-post gate: a post never reads a file whose refused edit never landed.

GRIP#9394. A session can send two tool calls in one response: an Edit to a
file, and the command that publishes that file (``gh pr create|edit|comment
--body-file``, ``gh issue create|edit|comment --body-file``, ``git commit
-F``). When a gate refuses the Edit, or Claude Code's own validation fails
it, the second call still runs -- the hook that refused the Edit cannot see
the call beside it, and the harness runs both calls whatever the first
returned. The text that goes out is then the old text, with nothing to say
so: the post exits 0. Three stale posts reached GitHub or a commit this way
between 2026-10-27 and 2026-11-24, on Windows each time; a memory record did
not prevent the second or the third
(feedback_never_batch_an_edit_with_the_command_that_publishes_it.md, N=48 by
the time this was built).

V>>'s decision on 2026-11-26 (issue #9394, option 1, narrow): a check before
each publishing Bash command refuses the post only while a refused Edit,
Write or MultiEdit to that same file is STILL missing from it -- never on
history alone.

What it checks. For ``git commit -F <path>``, ``gh pr create|edit|comment
--body-file <path>`` and ``gh issue create|edit|comment --body-file <path>``,
every path a ``-F``/``--file``/``--body-file`` argument names. For each such
path, every Edit, Write or MultiEdit SINCE THE OPERATOR'S LAST MESSAGE (the
same turn boundary lib/untrusted_turn.py uses) whose own tool_result reports
``is_error`` -- a GRIP gate's refusal and a native Claude Code failure both
count; the harm is the stale text, not who said no. The file is then read:
if it still lacks what that call tried to write (the new text for an Edit or
a Write, or -- for a pure deletion, an Edit whose new text is empty -- the
OLD text is still there), the post is DENIED and the reason names the call
that failed. A path with no such unlanded failure is allowed; so is the
retry itself (an Edit/Write/MultiEdit is never this gate's target, only the
Bash call that posts) and a file a later, different call already fixed.
Checking the FILE rather than only the history is the point: read literally,
"an edit to this path was refused earlier in the same turn" would also
refuse the retry, because the retry comes in the same turn too.

Paths are compared after ``canonical_path`` folds the three Windows
spellings of a drive (``C:\\x``, ``C:/x``, ``/c/x``) to one, and resolves
symlinks so a shared root such as macOS's ``/tmp`` (really
``/private/tmp``) does not defeat the match. The three recorded cases were
all on Windows, and the fold is a textual no-op on any other OS (a leading
``/<letter>/`` there is an ordinary directory, never a drive).

Fails open: any error inside the gate allows the command and is logged, the
same contract as lib/measured_claim_gate.py, next to which this runs
(rules/measured-claims-read-the-output-first.md). ``GRIP_STALE_POST_GATE=0``
on the command overrides for a deliberate post of the old text, and is
logged.

What this gate does NOT cover (named, not hidden, per the decision brief's
own "what stays risky"): a post that does not name a file by one of the
three flags above (``gh api -F body=@file``, ``gh pr merge``'s squash body,
a ``curl`` with ``@file``), a path reached through a copy of the refused
file rather than the file itself, and a transcript shape Claude Code has not
yet been observed to write differently. The landed check is substring
containment (the issue's own ``rg -c '<new text>' <file>`` guard, made
automatic): a short or already-common ``new_string`` can therefore read as
landed when it is only coincidentally present elsewhere in the file, and
the miss is silent in exactly the direction this gate exists to catch
(AGORA on GRIP#9394, round 1). Redesigning the match itself is out of this
narrow build's scope -- it is the mechanism V>>'s decision asked for -- so
this is named as a known limitation rather than hidden.
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from measured_claim_gate import agent_transcript, assignments
from transcript_text import is_human
from untrusted_turn import read_window

ENV_OVERRIDE = "GRIP_STALE_POST_GATE"

#: The three verbs this gate watches. Narrower than
#: lib.measured_claim_gate._PUBLISH_RE (which also reads close/review/inline
#: text, for a different purpose -- a test count, not a file's staleness):
#: a stale post reaching GitHub or a commit is the harm here, and it only
#: ever travels through one of these three forms.
_PUBLISH_RE = re.compile(
    r"\bgit\s+commit\b|\bgh\s+(?:pr|issue)\s+(?:create|edit|comment)\b")

#: A file this command reads its posted text from: git's -F/--file, gh's
#: --body-file. Same quoting shape as measured_claim_gate._FILE_FLAG_RE (not
#: imported -- it is private to that module, and reused here only in spirit:
#: this gate never needs that module's heredoc/stdin branches, which
#: --body-file never takes).
_QUOTED = r"\"((?:[^\"\\]|\\.)*)\"|'([^']*)'|(\S+)"
#: --file/--body-file take a space OR an attached "="; -F takes a space OR
#: nothing at all (getopt's attached-value form, "-Fnote.txt"), so \s* -- not
#: \s+ -- separates it from its value. A bare "--file" or "--body-file" with
#: neither form present is not a match (AGORA on GRIP#9394 round 2: the
#: \s+-only original silently allowed "--body-file=pr.md" and "-Fnote.txt"
#: straight past this gate).
_FILE_FLAG_RE = re.compile(
    r"(?:^|\s)(?:(?:--file|--body-file)(?:=|\s+)|-F\s*)(?:" + _QUOTED + ")")
_VAR_RE = re.compile(r"\$\{?([A-Za-z_]\w*)\}?")
_OVERRIDE_RE = re.compile(r"(?:^|\s)" + ENV_OVERRIDE + r"=0(?:\s|$)")

_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit"})

#: Windows's three spellings of a drive path, folded to one textually -- see
#: canonical_path, which gates this on the running OS.
_GITBASH_DRIVE_RE = re.compile(r"^/([A-Za-z])(/.*|)$")
_DOS_DRIVE_RE = re.compile(r"^([A-Za-z]):")


@dataclass(frozen=True)
class Decision:
    """What the gate decided, why, and the path and transcript it checked."""

    action: str  # "allow" | "deny"
    reason: str
    path: str = ""
    transcript_file: str = ""


@dataclass(frozen=True)
class FailedEdit:
    """One Edit, Write or MultiEdit in the turn whose own tool_result failed."""

    tool_use_id: str
    name: str  # "Edit" | "Write" | "MultiEdit"
    path: str
    pairs: tuple  # ((old_or_None, new), ...) -- what the call tried to make true
    excerpt: str  # the failing tool_result's own text, for the deny reason


# -- the command: which file(s) it would read its posted text from ------------


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _first(groups) -> str:
    return next((g for g in groups if g is not None), "")


def _resolve_path(raw: str, command: str, environ, cwd) -> "Path | None":
    """``~`` and ``$VAR`` expanded, relative to ``cwd``; None for an unresolved ``$``."""
    env = {**dict(environ), **assignments(command)}
    expanded = os.path.expanduser(_VAR_RE.sub(lambda m: env.get(m.group(1), m.group(0)), raw))
    if "$" in expanded:
        return None
    path = Path(expanded)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return path


def publish_paths(command: str, environ=None, cwd=None) -> list:
    """Every path a ``-F``/``--file``/``--body-file`` argument names; ``-`` (stdin) is skipped."""
    environ = os.environ if environ is None else environ
    found = []
    for m in _FILE_FLAG_RE.finditer(command):
        raw = _unquote(_first(m.groups()))
        if raw == "-":
            continue
        path = _resolve_path(raw, command, environ, cwd)
        if path is not None:
            found.append(path)
    return found


def override_requested(command: str, environ=None) -> bool:
    """``GRIP_STALE_POST_GATE=0`` on the command or in the environment."""
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_OVERRIDE, "")).strip() == "0" or bool(_OVERRIDE_RE.search(command or ""))


# -- paths: the same file under its different spellings ------------------------


def _gitbash_drive_form(path_str: str) -> str:
    """``/c/x`` -> ``c:/x``: Git Bash's spelling of a drive, folded to DOS form."""
    m = _GITBASH_DRIVE_RE.match(path_str)
    return f"{m.group(1).lower()}:{m.group(2) or '/'}" if m else path_str


def canonical_path(path_str, windows: "bool | None" = None) -> str:
    """One form for comparing two paths that may name the same file.

    Windows's three spellings of a drive (``C:\\x``, ``C:/x``, ``/c/x``) are
    folded to one lowercase-drive form first -- pure string work, so it is
    testable on any host -- gated by ``windows`` (default: this process's
    own OS, ``os.name == "nt"``), because a leading ``/<letter>/...`` is an
    ORDINARY absolute path on any other OS, never a drive.

    The realpath/normpath step then uses ``ntpath`` or ``posixpath``
    EXPLICITLY, chosen by the same ``windows`` flag -- never the ambient
    ``os.path``, which is bound to whatever OS this process actually runs
    on and would ignore ``windows=False`` on a real Windows host (AGORA on
    GRIP#9394: ``os.path.realpath("/a/file.md")`` there resolves a
    drive-relative path against the CURRENT drive, so the explicit-False
    test case would have silently failed on the very OS the three recorded
    incidents came from). This also resolves a symlinked root, so a shared
    root such as macOS's ``/tmp`` (really ``/private/tmp``) compares equal
    to itself.
    """
    is_windows = (os.name == "nt") if windows is None else windows
    text = str(path_str)
    pathmod = ntpath if is_windows else posixpath
    if is_windows:
        text = _gitbash_drive_form(text.replace("\\", "/"))
        text = _DOS_DRIVE_RE.sub(lambda m: m.group(1).lower() + ":", text, count=1)
    try:
        return pathmod.realpath(text)
    except OSError:
        return pathmod.normpath(text)


# -- the transcript: failed Edit/Write/MultiEdit calls since the operator's last message


def _entry(line: str) -> dict:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _content(entry: dict):
    message = entry.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _tool_uses(entry: dict) -> list:
    content = _content(entry)
    if entry.get("type") != "assistant" or not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def _tool_results(entry: dict) -> list:
    content = _content(entry)
    if entry.get("type") != "user" or not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def _result_excerpt(block: dict, limit: int = 160) -> str:
    content = block.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    else:
        text = ""
    return " ".join(text.split())[:limit]


def _edit_pairs(name: str, tool_input: dict) -> tuple:
    """``((old_or_None, new), ...)`` -- what the call tried to make true."""
    if name == "Edit":
        return ((tool_input.get("old_string"), str(tool_input.get("new_string", ""))),)
    if name == "Write":
        return ((None, str(tool_input.get("content", ""))),)
    if name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return ()
        return tuple((e.get("old_string"), str(e.get("new_string", "")))
                     for e in edits if isinstance(e, dict))
    return ()


def _record_tool_uses(entry: dict, at: int, pending: dict) -> None:
    """Remember every Edit/Write/MultiEdit tool_use in ``entry``, by its id."""
    for block in _tool_uses(entry):
        name = block.get("name")
        if name not in _EDIT_TOOLS:
            continue
        tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
        path = str(tool_input.get("file_path") or "").strip()
        if path:
            pending[str(block.get("id") or "")] = (at, name, path, tool_input)


def _record_tool_results(entry: dict, turn_start: int, pending: dict, found: list) -> None:
    """Pair every failing tool_result in ``entry`` with its remembered tool_use,
    appending a FailedEdit for each one that falls on or after ``turn_start``."""
    for block in _tool_results(entry):
        if not block.get("is_error"):
            continue
        use_id = str(block.get("tool_use_id") or "")
        record = pending.get(use_id)
        if record is None:
            continue
        use_at, name, path, tool_input = record
        if use_at < turn_start:
            continue
        found.append(FailedEdit(tool_use_id=use_id, name=name, path=path,
                                pairs=_edit_pairs(name, tool_input), excerpt=_result_excerpt(block)))


def _turn_start(entries: list) -> int:
    """The index right after the LAST human message in ``entries``; 0 when
    none is found, meaning the whole window counts as the turn (the same
    conservative default untrusted_turn.split_turn uses). A one-pass update
    made while also scanning forward would use whichever human message has
    been SEEN SO FAR, not the last one overall, and so would count a failure
    that in fact sits in an earlier, already-closed turn -- found live by
    this module's own test suite."""
    last = -1
    for at, entry in enumerate(entries):
        if entry and is_human(entry):
            last = at
    return last + 1


def failed_edits_in_turn(lines: list) -> list:
    """Every Edit/Write/MultiEdit in ``lines`` since the last human message
    whose own tool_result reports ``is_error`` -- a GRIP gate's refusal and a
    native Claude Code failure both count (the decision brief's point 2).
    ``lines`` is oldest-first, as untrusted_turn.read_window returns them.
    """
    entries = [_entry(raw) for raw in lines]
    turn_start = _turn_start(entries)
    pending: dict = {}
    found: list = []
    for at, entry in enumerate(entries):
        if not entry or is_human(entry):
            continue
        _record_tool_uses(entry, at, pending)
        _record_tool_results(entry, turn_start, pending, found)
    return found


def _landed(path: Path, pairs: tuple) -> bool:
    """True when every pair's change already shows in the file at ``path`` now:
    the new text present, or -- for a deletion, an empty new string -- the old
    text gone. An unreadable file counts as not landed: we have no evidence it
    is fixed, and a missed stale post is worse than a spare refusal."""
    if not pairs:
        return True
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for old, new in pairs:
        if new:
            if new not in text:
                return False
        elif old and old in text:
            return False
    return True


# -- the decision ---------------------------------------------------------------


def _transcript_path(transcript_path, agent_id: str):
    if not agent_id:
        return transcript_path
    return agent_transcript(transcript_path, agent_id) or transcript_path


def _stale_target(failures: list, targets: list, windows: "bool | None"):
    """``(path, FailedEdit)`` for the most recent still-missing failure on one of
    ``targets``, or ``(None, None)`` when every failure landed or named no target."""
    canon_targets = {canonical_path(t, windows): t for t in targets}
    for failure in reversed(failures):  # the most recent failure names the reason
        target = canon_targets.get(canonical_path(failure.path, windows))
        if target is not None and not _landed(target, failure.pairs):
            return target, failure
    return None, None


def _deny_reason(target: Path, failure: FailedEdit) -> str:
    excerpt = f" ({failure.excerpt})" if failure.excerpt else ""
    return (
        f"[GRIP stale-post gate] this command reads {target} to publish it, and the "
        f"{failure.name} that changed it failed earlier in this turn{excerpt}; its "
        "change is still missing from the file. Read the file, reapply the edit, "
        "then post again -- neither is denied. For a deliberate post of this text, "
        f"prefix the command with {ENV_OVERRIDE}=0 (logged).")


def decide(command: str, transcript_path, environ=None, cwd=None, agent_id: str = "",
          windows: "bool | None" = None) -> Decision:
    """Allow or deny one Bash command; see the module docstring.

    ``agent_id`` is the PreToolUse payload's field of that name, read exactly
    as lib.measured_claim_gate.decide reads it: empty for the session
    itself, set for a dispatched worker, whose own transcript is read when
    it exists. ``windows`` forces canonical_path's Windows-drive fold for a
    test; leave it unset in production, where it reads this process's own OS.
    """
    command = command or ""
    if not _PUBLISH_RE.search(command):
        return Decision("allow", "not a publishing command")
    if override_requested(command, environ):
        return Decision("allow", f"overridden by {ENV_OVERRIDE}=0")
    targets = publish_paths(command, environ, cwd)
    if not targets:
        return Decision("allow", "no -F/--file/--body-file argument to check")
    path = _transcript_path(transcript_path, agent_id)
    lines = read_window(str(path) if path else "")
    if lines is None:
        return Decision("allow", "no transcript to check", transcript_file=str(path or ""))
    failures = failed_edits_in_turn(lines)
    if not failures:
        return Decision("allow", "no failed Edit, Write or MultiEdit in this turn",
                        transcript_file=str(path or ""))
    target, failure = _stale_target(failures, targets, windows)
    if target is None:
        return Decision("allow", "every failed edit in this turn landed, or touched a different file",
                        transcript_file=str(path or ""))
    return Decision("deny", _deny_reason(target, failure), path=str(target), transcript_file=str(path or ""))
