#!/usr/bin/env python3
"""cc-handover-hook — SessionEnd hook: regenerate the canonical harness
handover every time a Claude Code session ends (limit kills included).

Fail-closed: if the generator cannot run, a HANDOVER-STALE marker is written
to drafts/ and the hook exits non-zero so the session end is flagged — a
silent skip would leave the handover stale without anyone knowing.

Local writes only (drafts/ + the marker). No network, no git commits.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude"))).resolve()
KERNEL = CLAUDE_DIR / "bin" / "grip-cc-forensics"
STALE = CLAUDE_DIR / "drafts" / "HANDOVER-STALE"


def main():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    # SessionEnd payload keys per the CC hook contract: session_id,
    # transcript_path, cwd, hook_event_name. None of them change behaviour;
    # the hook regenerates unconditionally — always-ready by construction.
    try:
        r = subprocess.run(
            [sys.executable, str(KERNEL), "--regenerate"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        r = None
        err = str(exc)
    else:
        err = r.stderr.strip()[-400:]
    if r is None or r.returncode != 0:
        STALE.parent.mkdir(parents=True, exist_ok=True)
        STALE.write_text(
            f"handover regeneration FAILED at session end: {err}\n"
            "Next harness: run `bin/grip-cc-forensics --regenerate` by hand.\n"
        )
        print(json.dumps({"continue": True, "handover": "STALE"}), file=sys.stdout)
        return 1
    print(json.dumps({"continue": True, "handover": "regenerated"}), file=sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
