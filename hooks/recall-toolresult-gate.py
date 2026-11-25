#!/usr/bin/env python3
"""PostToolUse advisory (F2/F3) — an empty search never proves absence.

When a Bash/Grep/Glob probe (find/rg/grep/ls/fd-shaped) returns empty or
errors, this hook infers subjects from the command string, queries the moat
index, and — if ACTIVE artefacts exist — injects additionalContext saying the
moat holds N artefact(s) with paths + hashes, so the agent does not conclude
absence from one empty tool result. Advisory only: it NEVER blocks, and it
fails open on any error.

PostToolUse stdin: {"tool_name", "tool_input", "tool_response", ...}.
Output: {"hookSpecificOutput": {"hookEventName": "PostToolUse",
"additionalContext": "..."}} or {} (silent).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PROBE_RE = re.compile(r"\b(?:find|rg|grep|ls|fd|stat|locate)\b")
_EMPTY_MARKERS = ("no such file", "no matches found", "not found", "0 matches")


def _silent() -> int:
    print("{}")
    return 0


def _emit_context(context: str) -> int:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": context,
    }}))
    return 0


def _response_text(tool_response) -> str:
    if isinstance(tool_response, str):
        return tool_response
    if isinstance(tool_response, dict):
        return str(tool_response.get("stdout", "") or tool_response.get("content", "")
                   or tool_response.get("output", "") or tool_response.get("error", ""))
    return ""


def _probe_string(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        return cmd if _PROBE_RE.search(cmd) else ""
    return str(tool_input.get("pattern", "") or tool_input.get("path", ""))


def _looks_empty(text: str) -> bool:
    stripped = text.strip()
    return not stripped or any(m in stripped.lower() for m in _EMPTY_MARKERS)


def _advisory_lines(subject: str, artefacts: list) -> list[str]:
    lines = [f"Recall index: the moat holds {len(artefacts)} artefact(s) for "
             f"'{subject}' — an empty search here does not establish absence:"]
    lines.extend(
        f"  - [{a.get('type')}] {a.get('path')} (sha256={a.get('blob_hash') or '-'}, "
        f"idr={a.get('idr_id') or '-'})" for a in artefacts[:5])
    return lines


_NOISE_SUBJECTS = frozenset({
    "users", "home", "tmp", "var", "private", "name", "maxdepth", "type",
    "path", "grep", "find", "print", "user", "library", "documents",
})
_MAX_HITS_PER_SUBJECT = 50  # more hits = the subject is non-discriminative
_MAX_SUBJECTS_REPORTED = 3


def _active_unique(artefacts: tuple, reported: set) -> list:
    from lib.recall.schema import STATUS_ACTIVE
    out = []
    for art in artefacts:
        path = art.get("path")
        if art.get("status") == STATUS_ACTIVE and path not in reported:
            reported.add(path)
            out.append(art)
    return out


def _moat_context(probe: str) -> str:
    from lib.recall.aliases import normalise
    from lib.recall.index import query_subject
    from lib.recall.preassert import extract_subjects
    lines: list[str] = []
    reported: set = set()
    subjects_used = 0
    for subject in extract_subjects(probe):
        if normalise(subject) in _NOISE_SUBJECTS or subjects_used >= _MAX_SUBJECTS_REPORTED:
            continue
        artefacts = query_subject(subject).artefacts
        if len(artefacts) > _MAX_HITS_PER_SUBJECT:
            continue  # matched half the index: says nothing about this probe
        active = _active_unique(artefacts, reported)
        if not active:
            continue
        subjects_used += 1
        lines.extend(_advisory_lines(subject, active))
    if lines:
        lines.append("Do not conclude absence — check these paths, or state the "
                     "searched scope explicitly.")
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return _silent()
    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Bash", "Grep", "Glob"):
        return _silent()
    probe = _probe_string(tool_name, payload.get("tool_input") or {})
    if not probe or not _looks_empty(_response_text(payload.get("tool_response"))):
        return _silent()
    try:
        context = _moat_context(probe)
    except Exception:  # noqa: BLE001 — advisory: fail open
        return _silent()
    return _emit_context(context) if context else _silent()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — advisory surface: never break a tool result
        sys.exit(_silent())
