#!/usr/bin/env python3
"""Backfill data/absorbed-urls.jsonl into KONO (H791 W6).

The Guild bot fetched URLs into data/absorbed-urls.jsonl but NONE reached the
MemoryEngine — 0 were /recall-able. This tool reads the ledger, dedups by URL
(keeping the richest extraction per URL), drops failed/thin extractions, and
writes each survivor into KONO via the W3 sink (lib.hal.knowledge_absorption.
absorb_url). It RE-FETCHES NOTHING — the extracted text is already in the
ledger, and many rows are auth-walled (LinkedIn/X) where a re-fetch would get
login-wall garbage strictly worse than what was captured.

Provenance: backfilled rows originate from the Guild Discord channel, so they
are tagged source='discord'. Idempotent: absorb_url keys the memory filename on
md5(text), so re-running does not duplicate.

Quality gate (the mechanical form of the requested broly dedup/quality-gate;
per dispatch-economics a 56-row dedup does not warrant an agent mesh):
  - dedup by URL, keep max charCount
  - drop charCount < MIN_CHARS (failed/thin extractions, e.g. 23-char repo stubs)

Usage:
  python3 scripts/backfill_absorbed_urls.py --dry-run   # show what would absorb
  python3 scripts/backfill_absorbed_urls.py             # absorb into KONO
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

LEDGER = _ROOT / "data" / "absorbed-urls.jsonl"
MIN_CHARS = 200  # below this an extraction is a failed/thin stub, not knowledge


def grip_memory_dir() -> Path:
    """GRIP's project-memory dir (the KONO substrate /recall reads).

    CRITICAL: lib.hal.memory_engine defaults to HAL_HOME (~/.hal), so a bare
    MemoryEngine() from a GRIP cwd writes to ~/.hal/memory — the WRONG store,
    invisible to GRIP /recall (verified 2026-05-29: a first backfill landed 22
    files in ~/.hal/memory). We derive GRIP's own project-memory dir from the
    repo root (same project-key scheme as _default_memory_dir, but rooted at
    the GRIP install, not HAL) and inject it. Path-portable — no hardcoded home.
    """
    project_key = str(_ROOT).replace("/", "-").replace(".", "-")
    return _ROOT / "projects" / project_key / "memory"


def load_ledger(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # skip malformed line, never crash the backfill
    return rows


def dedup_and_filter(rows: list[dict], min_chars: int = MIN_CHARS) -> tuple[list[dict], dict]:
    """Dedup by URL keeping the richest extraction; drop thin ones.

    Returns (survivors, stats). Pure — no I/O, unit-testable.
    """
    best: dict[str, dict] = {}
    for r in rows:
        url = r.get("url")
        if not url:
            continue
        cc = int(r.get("charCount", 0) or 0)
        if url not in best or cc > int(best[url].get("charCount", 0) or 0):
            best[url] = r
    survivors = [r for r in best.values() if int(r.get("charCount", 0) or 0) >= min_chars]
    dropped_thin = [r for r in best.values() if int(r.get("charCount", 0) or 0) < min_chars]
    stats = {
        "ledger_rows": len(rows),
        "unique_urls": len(best),
        "survivors": len(survivors),
        "dropped_thin": len(dropped_thin),
    }
    return survivors, stats


def backfill(survivors: list[dict], *, dry_run: bool) -> dict:
    """Absorb survivors into KONO. Returns counts. Dry-run skips the write."""
    absorbed, failed = 0, 0
    engine = None
    if not dry_run:
        from lib.hal.knowledge_absorption import absorb_url  # lazy: dry-run needs no engine
        from lib.hal.memory_engine import MemoryEngine
        # Inject a GRIP-pointed engine — without this, MemoryEngine defaults to
        # HAL_HOME (~/.hal/memory) and the absorptions never reach GRIP KONO.
        engine = MemoryEngine(memory_dir=str(grip_memory_dir()))
    for r in survivors:
        url = r["url"]
        title = r.get("title", "") or ""
        text = r.get("text", "") or ""
        # ledger rows came from the Guild Discord channel
        channel = "discord"
        if dry_run:
            print(f"  WOULD absorb [{channel}] {r.get('charCount', 0):>5}c  {url}")
            absorbed += 1
            continue
        try:
            absorb_url(url, title, text, channel, sender=r.get("userId"), engine=engine)
            absorbed += 1
        except Exception as e:  # never let one bad row abort the batch
            print(f"  FAILED {url}: {e}", file=sys.stderr)
            failed += 1
    return {"absorbed": absorbed, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backfill_absorbed_urls")
    ap.add_argument("--dry-run", action="store_true", help="show what would absorb, write nothing")
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--min-chars", type=int, default=MIN_CHARS)
    args = ap.parse_args(argv)

    if not args.ledger.exists():
        print(f"ledger not found: {args.ledger}", file=sys.stderr)
        return 1

    rows = load_ledger(args.ledger)
    survivors, stats = dedup_and_filter(rows, args.min_chars)
    print(
        f"ledger={stats['ledger_rows']} unique={stats['unique_urls']} "
        f"survivors={stats['survivors']} dropped_thin={stats['dropped_thin']}"
    )
    result = backfill(survivors, dry_run=args.dry_run)
    verb = "would-absorb" if args.dry_run else "absorbed"
    print(f"{verb}={result['absorbed']} failed={result['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
