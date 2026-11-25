#!/usr/bin/env python3
"""H256/H257 verification — run on 2026-06-02 to confirm or refute.

H256: JSON delta encoder keeps re-read context < 2KB vs ~80KB raw (≥90% savings).
H257: Bootstrap oracle hits on ≥80% of stable sessions (8 of 10 consecutive).

Usage:
    python3 lib/verify_h256_h257.py          # human-readable
    python3 lib/verify_h256_h257.py --json   # machine-readable JSON
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_CLAUDE = Path.home() / ".claude"
_DELTA_TEL = _CLAUDE / "state" / "json-snapshot-cache" / "telemetry.jsonl"
_HIT_LOG = _CLAUDE / "state" / "session-bootstrap-hits.jsonl"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def _verify_h256(rows: list[dict]) -> tuple[str, str]:
    """H256: avg delta_bytes < 2048 with ≥90% compression vs raw."""
    if not rows:
        return "INCONCLUSIVE", "0 delta reads recorded — hook has not fired yet"
    avg_raw = sum(r["raw_bytes"] for r in rows) / len(rows)
    avg_delta = sum(r["delta_bytes"] for r in rows) / len(rows)
    ratio = (avg_raw - avg_delta) / avg_raw if avg_raw else 0
    verdict = "CONFIRMED" if avg_delta < 2048 and ratio >= 0.90 else "REFUTED"
    detail = (
        f"{len(rows)} delta reads | avg raw={avg_raw/1024:.1f}KB "
        f"avg delta={avg_delta:.0f}B savings={ratio*100:.0f}%"
    )
    return verdict, detail


def _verify_h257(rows: list[dict]) -> tuple[str, str]:
    """H257: ≥80% CACHE-HIT rate across last 10 stable sessions."""
    if len(rows) < 2:
        return "INCONCLUSIVE", f"{len(rows)} session(s) logged — need ≥10 for significance"
    recent = rows[-10:]
    hits = sum(1 for r in recent if r.get("hit"))
    rate = hits / len(recent)
    if len(rows) < 10:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "CONFIRMED" if rate >= 0.80 else "REFUTED"
    detail = f"{hits}/{len(recent)} CACHE-HIT ({rate*100:.0f}%) across last {len(recent)} sessions"
    return verdict, detail


def main() -> None:
    as_json = "--json" in sys.argv
    delta_rows = _read_jsonl(_DELTA_TEL)
    hit_rows = _read_jsonl(_HIT_LOG)
    h256, d256 = _verify_h256(delta_rows)
    h257, d257 = _verify_h257(hit_rows)

    if as_json:
        print(json.dumps({"H256": h256, "H257": h257, "h256_detail": d256, "h257_detail": d257}))
        return

    print(f"H256 (JSON delta encoder):     {h256}")
    print(f"  {d256}")
    print(f"H257 (Bootstrap oracle hits):  {h257}")
    print(f"  {d257}")
    print(f"\nTelemetry: {len(delta_rows)} delta events, {len(hit_rows)} sessions tracked")
    print(f"Logs: {_DELTA_TEL}")
    print(f"      {_HIT_LOG}")


if __name__ == "__main__":
    main()
