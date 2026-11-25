#!/usr/bin/env python3
"""UserPromptSubmit: remember that the operator asked for full prose in this session.

The whole prompt must be `normal mode`, `stop caveman` or `stop adhd mode`
(lib/full_prose_marker.py). A later `/caveman` command clears the marker, because it
is the newer choice. The hook prints nothing and never blocks: any failure exits 0,
because a lost marker costs one line of context and a blocked prompt costs the
operator's turn.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))


def main() -> None:
    import full_prose_marker  # noqa: PLC0415

    payload = json.load(sys.stdin)
    prompt, session = payload.get("prompt"), payload.get("session_id")
    if full_prose_marker.is_escape(prompt):
        full_prose_marker.write_marker(session)
    elif full_prose_marker.is_compression(prompt):
        full_prose_marker.clear_marker(session)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        pass
    sys.exit(0)
