#!/usr/bin/env python3
"""Drive the Google Workspace absorber and log the result.

Calls ``lib.aide.absorb.gog.absorb_all`` for V>>'s account, then appends
a structured log entry to ``state/copilot/absorb_log.jsonl``. Idempotent —
safe to run hourly via launchd.

When the underlying CLI helpers (``lib.gcal``, ``lib.gog_gmail``,
``lib.gog_drive``) are missing, ``_run`` returns None and ``_ingest`` becomes
a no-op; this driver records a zero-count entry and exits cleanly. That keeps
the schedule running even on machines that have not yet wired DWD auth.

CLI:
    python3 scripts/aide-absorb-gog.py [--user EMAIL] [--hours-back N] [--days-ahead N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
DEFAULT_USER = os.environ.get("COPILOT_GOG_USER", "licensing@codetonight.co.za")
ABSORB_LOG = CLAUDE_DIR / "state" / "copilot" / "absorb_log.jsonl"


def run_absorb(user: str, hours_back: int, days_ahead: int) -> dict:
    """Invoke absorb_all and return the per-source kept counts dict."""
    sys.path.insert(0, str(CLAUDE_DIR))
    from lib.aide.absorb.gog import absorb_all
    return absorb_all(user, hours_back=hours_back, days_ahead=days_ahead)


def log_result(user: str, counts: dict) -> None:
    """Append one structured line to absorb_log.jsonl."""
    ABSORB_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "counts": counts,
        "total_kept": sum(counts.values()),
    }
    with ABSORB_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--user", default=DEFAULT_USER, help="Google Workspace email")
    parser.add_argument("--hours-back", type=int, default=24)
    parser.add_argument("--days-ahead", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    counts = run_absorb(args.user, args.hours_back, args.days_ahead)
    log_result(args.user, counts)

    if not args.quiet:
        total = sum(counts.values())
        per_source = " ".join(f"{src}={n}" for src, n in counts.items())
        print(f"copilot-absorb-gog: user={args.user} total={total} {per_source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
