"""UTF-8 stdio for GRIP's directly-executable ``bin/`` commands.

Windows Python defaults stdout and stderr to the ANSI code page -- cp1252 on a
Western locale -- not UTF-8. GRIP's commands print box-drawing, arrows and
status glyphs, and cp1252 cannot encode any of them, so the first such print
raises UnicodeEncodeError and kills the command outright::

    UnicodeEncodeError: 'charmap' codec can't encode character '\\u21c4'

That is a real crash, not a cosmetic one. ``bin/grip-otty status`` dies on it
today under a forced cp1252, exactly as ``grip-doctor --check`` died on A>>'s
Windows 11 machine on 2026-08-11.

WHY THIS MODULE EXISTS RATHER THAN A COPIED PRELUDE. Two seams already force
UTF-8 and neither reaches these commands:

* ``scripts/lib/venv-utils.sh`` exports ``PYTHONIOENCODING`` -- but only the 25
  ``bin/`` commands that are shell scripts source it.
* The ``env`` block in ``settings.json`` covers every process Claude Code
  spawns -- but an operator running ``bin/grip-otty`` straight from Git Bash
  goes nowhere near Claude Code.

A ``#!/usr/bin/env python3`` command invoked directly is therefore on its own,
and the only thing that can protect it is an in-process call. Putting that call
behind this module keeps ONE implementation: the logic still lives in
``lib/_compat.py``, and this file is the import seam that makes it reachable.

HOW CALLERS FIND IT. Running ``python bin/grip-otty`` puts ``bin/`` on
``sys.path[0]``, so a bare import would work -- but the test suite loads these
same commands with ``importlib.machinery.SourceFileLoader``, which does not.
Relying on ``sys.path[0]`` therefore breaks every test that imports a command
as a module, so each caller locates ``bin/`` from its own ``__file__`` instead
and works under both.

Usage, as the first executable statement of a ``bin/`` command::

    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from _grip_console import ensure_utf8_stdio  # noqa: E402

    ensure_utf8_stdio()

Covered by ``tests/test_windows_console_encoding.py``, which forces cp1252 and
asserts every glyph-carrying ``bin/`` command survives -- and fails for any new
one that forgets this call.
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Re-exported, never reimplemented: lib/_compat.py stays the single definition
# of what "force UTF-8 stdio" means, so a fix there reaches every caller.
from _compat import ensure_utf8_stdio  # noqa: E402

__all__ = ["ensure_utf8_stdio"]
