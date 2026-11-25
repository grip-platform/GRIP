#!/usr/bin/env python3
"""update_team_tick — launchd entry for the ~4x/day team work-update.

Fires update_team(post=True). The real post is still double-gated:
GRIP_UPDATE_TEAM_POST=1 must be set (the installed plist defaults it to "0"),
and the no-spam fingerprint dedup skips ticks where nothing new has landed.
Appends one JSONL telemetry row per tick. ALWAYS exits 0 (a scheduled job
must never look like a crash — mirrors lib/proactive_tick.py).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.update_team import update_team  # noqa: E402 — root path injected above


def _log(root: Path, row: dict) -> None:
    """Append one telemetry row to logs/update-team.jsonl (best-effort)."""
    logp = root / "logs" / "update-team.jsonl"
    logp.parent.mkdir(parents=True, exist_ok=True)
    with logp.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def main() -> int:
    res = update_team(post=True)
    _log(_ROOT, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "posted": res.get("posted"),
        "reason": res.get("reason"),
        "fingerprint": res.get("fingerprint"),
    })
    print(f"[update-team-tick] posted={res.get('posted')} "
          f"reason={res.get('reason')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    raise SystemExit(main())
