#!/usr/bin/env python3
"""eod_tick — launchd entry for the once-daily EOD team digest.

Fires eod_report(post=True). Real post is double-gated:
GRIP_EOD_POST=1 must be set in the plist (defaults to "0"), AND the
no-spam fingerprint dedup skips ticks where nothing new has landed.
Appends one JSONL telemetry row per tick. ALWAYS exits 0.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.eod_report import eod_report  # noqa: E402


def _log(root: Path, row: dict) -> None:
    logp = root / "logs" / "eod.jsonl"
    logp.parent.mkdir(parents=True, exist_ok=True)
    with logp.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def main() -> int:
    res = eod_report(post=True)
    _log(_ROOT, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "posted": res.get("posted"),
        "reason": res.get("reason"),
        "fingerprint": res.get("fingerprint"),
        "provenance_rate": res.get("provenance_rate"),
    })
    print(f"[eod-tick] posted={res.get('posted')} "
          f"reason={res.get('reason')} "
          f"prov={res.get('provenance_rate', 0.0):.2f}", file=sys.stderr)
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
