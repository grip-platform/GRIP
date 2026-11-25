"""grip-radar: an economical monitor for replies, GRASP activity and web mentions.

A launchd job runs ``python -m lib.radar.cli --once`` every 30 minutes. Each tick
is deterministic: fetch what is due, diff it against ``state/radar/state.json``,
format one line per change, send the lines to the operator's WhatsApp, and write
one JSON object per source to ``logs/radar.jsonl``. No language model is called
anywhere in the loop, every API is free, and GitHub is read through the ``gh``
CLI's existing login.

This package ships the reply watch (``sources/mail_replies.py``) and the GitHub
watch (``sources/github_repo.py``). The plan this package implements names the
Vercel, Search Console, IETF and web-mention sources for a second PR; each one
registers in ``sources/__init__.py`` and adds its interval to ``schedule.py``.

Every module in GRIP's ``lib/`` is imported by its bare name (``claude_dir``,
``grasp_google``), so the directory is put on ``sys.path`` here, once, before
any sibling import.
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent  # <checkout>/lib
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from .tick import Runtime, run_tick, status_report  # noqa: E402

__all__ = ["Runtime", "run_tick", "status_report"]
