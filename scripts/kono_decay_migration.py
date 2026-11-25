#!/usr/bin/env python3
"""
Add the KONO decay ledger to an existing embeddings database.

NOT RUN AS PART OF ANY BUILD. Invoke it deliberately, on purpose, once.

WHAT IT DOES
    1. Adds the ``last_decayed_at`` column if absent (idempotent ALTER).
    2. Stamps rows whose ledger is NULL with the current timestamp.

WHAT IT DOES NOT DO
    It never writes ``fitness_score``. Not one row. The old decay applied a
    multiplier per CALL and kept no record of what it had already charged, so
    for an unstamped row that history does not exist anywhere. Back-computing
    it would mean inventing a number and writing it over 21,000 live rows.
    Starting the clock at migration time is the only honest option, and it is
    also the safe one: fitness is left exactly as found.

    Rows already below the recall floor therefore stay below it. That is a
    pre-existing condition this script deliberately declines to paper over --
    repairing it is a separate, evidence-led decision, not a side effect of
    adding a column.

USAGE
    python3 scripts/kono_decay_migration.py                 # dry run (default)
    python3 scripts/kono_decay_migration.py --apply
    python3 scripts/kono_decay_migration.py --apply --db /path/to/embeddings.db

Dry run is the default precisely so that running this by accident does
nothing at all.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import apsw  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402

LEDGER_COLUMN = "last_decayed_at"


def _columns(conn) -> set:
    return {row[1] for row in conn.execute("PRAGMA table_info(embeddings)")}


def _report(conn) -> None:
    """Print what the ledger change will and will not affect."""
    total, unstamped = list(conn.execute(
        f"SELECT COUNT(*), SUM({LEDGER_COLUMN} IS NULL) FROM embeddings"
    ))[0] if LEDGER_COLUMN in _columns(conn) else (
        list(conn.execute("SELECT COUNT(*) FROM embeddings"))[0][0], None
    )
    print(f"  rows in embeddings          : {total}")
    print(f"  rows needing a ledger stamp : "
          f"{unstamped if unstamped is not None else total}")

    cols = _columns(conn)
    limbs = []
    if "priority" in cols:
        limbs.append("priority = 'system'")
    if "type" in cols:
        limbs.append("type LIKE 'concept\\_%' ESCAPE '\\'")
    if limbs:
        where = " OR ".join(limbs)
        protected, unrecallable = list(conn.execute(
            f"SELECT COUNT(*), SUM(is_permanent = 0 AND fitness_score < 0.3) "
            f"FROM embeddings WHERE {where}"
        ))[0]
        print(f"  standing instructions        : {protected} "
              f"(now exempt from decay)")
        print(f"    of which below 0.3 floor   : {unrecallable} "
              f"(left as found -- fitness is never written)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without it, nothing is changed.")
    ap.add_argument("--db", type=Path,
                    default=get_claude_dir() / "embeddings.db")
    args = ap.parse_args()

    if not args.db.exists():
        print(f"No database at {args.db}", file=sys.stderr)
        return 1

    conn = apsw.Connection(str(args.db))
    mode = "APPLY" if args.apply else "DRY RUN (nothing will be written)"
    print(f"KONO decay ledger migration -- {mode}")
    print(f"  database: {args.db}")
    _report(conn)

    if not args.apply:
        print("\nRe-run with --apply to write.")
        return 0

    if LEDGER_COLUMN not in _columns(conn):
        conn.execute(
            f"ALTER TABLE embeddings ADD COLUMN {LEDGER_COLUMN} TEXT")
        print(f"  added column {LEDGER_COLUMN}")

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        f"UPDATE embeddings SET {LEDGER_COLUMN} = ? "
        f"WHERE {LEDGER_COLUMN} IS NULL", (now,))
    stamped = list(conn.execute("SELECT changes()"))[0][0]
    print(f"  stamped {stamped} rows at {now}")
    print("  fitness_score: untouched (0 rows written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
