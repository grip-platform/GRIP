#!/usr/bin/env python3
"""The per-machine switch for Claude Code's own permission prompts at a GRIP launch.

Until 2026-10-18 every launch path in bin/grip passed `--dangerously-skip-permissions`,
written for the operator's own machines and inherited by every fresh install. The
one-folder product's planning fleet flagged it (plans/ULTRAPLAN-2026-10-17-grip-one-
folder-fleet.md, finding F2, item A8) and V>> ruled: host prompts plus GRIP's gates for
every non-operator profile. So the launcher now asks this module, and the answer comes
from a gitignored per-machine file, config/launch.local.json:

    {"skip_permissions": true}    -> the launcher adds --dangerously-skip-permissions
    {"skip_permissions": false}   -> the launcher adds nothing; Claude Code prompts
    no file, or an unreadable one -> "prompt", the safer mode, never "skip"

The operator opts a machine in once with `bin/grip-launch-permissions skip`. A fresh
install has no file and so launches with prompts AND GRIP's gates.

The file's full shape, as write_mode writes it:

    {"skip_permissions": true | false,
     "set_at": "<UTC time, ISO 8601, when the switch was last run>",
     "set_by": "<the command that wrote it, bin/grip-launch-permissions>",
     "note":   "<one sentence for whoever opens the file>"}

Only skip_permissions is read, and only the JSON value true means skip. The other
three fields are for the reader. Any other shape, or no file, reads as prompt. A caller's own
--dangerously-skip-permissions on the command line is not this module's business; the
launcher passes it through untouched and this decides only the launcher's default.

Same shape as lib/model_pin.py: stdlib only, the file under CLAUDE_DIR, a `show`
that prints one word so a shell helper can read it, and an unreadable file that
chooses the safe side instead of raising, because a launch must never fall into the
unsafe mode by accident.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

FILE_NAME = "config/launch.local.json"
SKIP = "skip"
PROMPT = "prompt"
MODES = (SKIP, PROMPT)


def file_path() -> Path:
    return get_claude_dir() / FILE_NAME


def read_mode(path: Path | None = None) -> str:
    """`skip` only when the file exists, parses, and says so; `prompt` otherwise."""
    path = path or file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PROMPT
    if isinstance(data, dict) and data.get("skip_permissions") is True:
        return SKIP
    return PROMPT


def write_mode(mode: str, path: Path | None = None, *, set_by: str = "lib/launch_permissions.py") -> Path:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    path = path or file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "skip_permissions": mode == SKIP,
        "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "set_by": set_by,
        "note": "Per-machine. Gitignored. `skip` is the operator's own choice for this machine; "
                "a fresh install has no file and launches with Claude Code's prompts plus GRIP's gates.",
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".launch-local-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def clear(path: Path | None = None) -> bool:
    path = path or file_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _usage() -> str:
    return (
        "usage: launch_permissions.py show | set skip|prompt | off\n"
        "  show   print the launch mode for this machine: skip or prompt\n"
        "  set    write config/launch.local.json (skip: the launcher adds "
        "--dangerously-skip-permissions; prompt: it adds nothing)\n"
        "  off    remove the file, which means prompt\n"
    )


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(_usage(), end="")
        return 0 if argv else 2
    command = argv[0]
    if command == "show":
        print(read_mode())
        return 0
    if command == "set":
        if len(argv) != 2 or argv[1] not in MODES:
            print(_usage(), end="", file=sys.stderr)
            return 2
        path = write_mode(argv[1], set_by="bin/grip-launch-permissions")
        print(f"{path}: launch mode {argv[1]}")
        return 0
    if command == "off":
        removed = clear()
        print(f"{file_path()}: {'removed' if removed else 'absent'}; launch mode prompt")
        return 0
    print(_usage(), end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
