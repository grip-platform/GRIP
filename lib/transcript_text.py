"""The final assistant message a Stop hook is about, read from the right source.

The shared primitive for Stop hooks that must inspect what the assistant just
SAID. ``stop_message_text(payload)`` is the entry point, and it reads two
sources in this order:

1. The Stop payload's own copy of the message, ``last_assistant_message`` on
   Claude Code. It arrives with the event, so it is the message the turn is
   ending on.
2. The transcript JSONL, through ``last_assistant_text(path)``: walk from the
   end and return the LAST assistant entry that carries text, skipping
   tool-use-only turns and flattening its content blocks.

THE ORDER IS THE POINT. When this module was written (2026-07-17) the payload
carried no message text, so the transcript was the only source and two Stop
advisories that read only the payload never fired. By Claude Code 2.1.319
(measured 2026-11-09) the payload carries it, and the transcript is a separate
file that can lag behind the Stop event. hooks/next-step-recorder.py read only
the transcript: in the seven days to 2026-11-09, 73 turns on this machine ended
on a ``(mine)`` closer, the hook continued 20 and let 53 stop, and it had logged
none of those 53 -- it never saw them. A Stop hook that reads the final message
from the transcript alone can act on the previous message.

CONTRACT: both functions return the text, or ``""`` on any error (no payload
text and a falsy path, a missing or unreadable file, malformed JSONL, no
text-bearing assistant turn). PURE + FAIL-OPEN by construction: a read defect
must never break the Stop hook that calls it.
"""
from __future__ import annotations

import json
from pathlib import Path


def _flatten_content(content) -> str:
    """Flatten a CC message ``content`` (str, or list of blocks) to plain text.

    Assistant content is a list of blocks — ``{"type": "text", "text": ...}``
    interleaved with ``tool_use`` / ``thinking`` blocks. Only text blocks carry
    prose; everything else is dropped. A bare string content is returned as-is.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p)


def _entries(transcript_path, tail: int) -> list[dict]:
    """The last ``tail`` parseable JSONL entries, oldest first; [] on any failure."""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for raw in lines[-tail:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _collect_commands(entry: dict, commands: dict[str, str]) -> None:
    """Record ``tool_use_id -> command`` for every Bash tool_use block."""
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            command = str((block.get("input") or {}).get("command") or "")
            commands[str(block.get("id") or "")] = command


def _collect_results(entry: dict, commands: dict[str, str], calls: list[dict]) -> None:
    """Pair every tool_result with the command that produced it."""
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_result"):
            continue
        output = _flatten_content(block.get("content"))
        if output.strip():
            calls.append({"command": commands.get(str(block.get("tool_use_id") or ""), ""),
                          "output": output})


def recent_tool_calls(transcript_path, tail: int = 400) -> list[dict]:
    """Recent ``{"command", "output"}`` pairs from a transcript JSONL.

    Used to tell QUOTED tool output apart from the agent's own assertions
    (issue #4409). Never raises: a transcript-read defect must not break the
    advisory hook that calls it — an unreadable transcript yields ``[]``, which
    restores the pre-#4409 behaviour exactly.
    """
    if not transcript_path:
        return []
    commands: dict[str, str] = {}
    calls: list[dict] = []
    for entry in _entries(transcript_path, tail):
        _collect_commands(entry, commands)
        _collect_results(entry, commands, calls)
    return calls


def last_assistant_text(transcript_path) -> str:
    """Return the last text-bearing assistant turn from a transcript JSONL, else ""."""
    if not transcript_path:
        return ""
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return ""
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        text = _flatten_content((entry.get("message") or {}).get("content"))
        if text.strip():
            return text
    return ""


#: Where a Stop payload carries its own copy of the final message. Claude Code
#: sends ``last_assistant_message``; the other spellings are the ones the
#: payload-reading hooks already accepted (hooks/operator-close-hook.py and its
#: siblings), kept so no caller loses a source it had.
STOP_TEXT_KEYS = ("last_assistant_message", "assistant_message", "final_message", "message")


def payload_message_text(payload) -> str:
    """The payload's own copy of the final message, or "" when it carries none.

    Returns the first non-empty string under ``STOP_TEXT_KEYS`` and never opens
    the transcript, for a hook that already reads the transcript its own way
    (GRIP#9075). Never raises.
    """
    if not isinstance(payload, dict):
        return ""
    for key in STOP_TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def stop_message_text(payload) -> str:
    """The message a Stop event is about: the payload's copy, else the transcript's.

    Returns ``payload_message_text(payload)`` when it is not empty; otherwise
    the last text-bearing assistant turn of ``payload["transcript_path"]``;
    otherwise "". Never raises. Read the module docstring for why the payload
    comes first.
    """
    if not isinstance(payload, dict):
        return ""
    return payload_message_text(payload) or last_assistant_text(payload.get("transcript_path"))


def _row_content(entry: dict):
    message = entry.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _has_text(content) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    items = [item for item in content or [] if isinstance(item, dict)]
    return any(item.get("type") == "text" for item in items) and not any(
        item.get("type") == "tool_result" for item in items)


def is_human(entry: dict) -> bool:
    """True for a message the operator typed; never for a notification, a summary or an injected note.

    Current Claude Code records where a user row came from as ``origin.kind``: a
    task notification is a user row whose kind is not ``human``. A transcript from
    before that field falls back to a user row with text, no tool result and no
    meta, summary or sidechain flag. One definition for lib/untrusted_turn.py and
    the Rule 20 scanner, moved here from the former (GRIP#9084).
    """
    if entry.get("type") != "user":
        return False
    origin = entry.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
        return False
    return _has_text(_row_content(entry))


def _preferred_slug_dir(cwd, root) -> "Path | None":
    """`<root>/projects/<slug of cwd>`, or None when ``cwd`` is unset/invalid."""
    if cwd is None:
        return None
    try:
        from lib.path_encoding import project_dir
    except ImportError:  # imported as a top-level module from lib/
        from path_encoding import project_dir
    try:
        return project_dir(cwd, claude_dir=root)
    except (OSError, ValueError):
        return None


def _newest_by_mtime(paths: list) -> "Path | None":
    if not paths:
        return None
    try:
        return max(paths, key=lambda p: p.stat().st_mtime)
    except OSError:
        return paths[0]  # a stat raced away mid-sort; any match beats none


def _match_session_id(projects: Path, session_id: str, preferred: "Path | None") -> "Path | None":
    try:
        matches = sorted(projects.glob(f"*/{session_id}.jsonl"))
    except OSError:
        return None
    if preferred is not None:
        same_slug = [m for m in matches if m.parent == preferred]
        if same_slug:
            return same_slug[0]
    return _newest_by_mtime(matches)


def _newest_in_dir(directory: "Path | None") -> "Path | None":
    if directory is None or not directory.is_dir():
        return None
    try:
        candidates = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return candidates[-1] if candidates else None


def transcript_for_session(
    session_id: "str | None" = None,
    *,
    cwd: "str | None" = None,
    claude_dir: "str | None" = None,
) -> "Path | None":
    """The transcript file for a session, preferring the slug of ``cwd``.

    With a ``session_id``: every ``<claude dir>/projects/*/<session_id>.jsonl``
    match, preferring the one filed under ``cwd``'s own project slug
    (``path_encoding.project_dir``) when more than one exists — the same
    situation ``thinking_trace._resolve_transcripts`` unions across instead,
    because ITS caller wants every specimen; this caller wants the one live
    conversation — else the newest match by mtime. With no ``session_id``:
    the newest ``*.jsonl`` already in ``cwd``'s slug directory.

    The one PUBLIC resolver for this lookup. ``thinking_trace.py`` and
    ``bin/grip-session-roster`` (``transcript_for``) each carry a private
    version of a slice of it; this is the shared one the ``/continue`` engine
    (``lib/continue_card.py``) needed, rather than a third private copy.
    Never raises: any OSError
    while globbing or stat-ing a candidate is "no match", the same fail-soft
    contract as every other function in this module.
    """
    try:
        from lib.claude_dir import get_claude_dir
    except ImportError:  # imported as a top-level module from lib/
        from claude_dir import get_claude_dir

    root = Path(claude_dir) if claude_dir else get_claude_dir()
    preferred = _preferred_slug_dir(cwd, root)
    if session_id:
        return _match_session_id(root / "projects", session_id, preferred)
    return _newest_in_dir(preferred)
