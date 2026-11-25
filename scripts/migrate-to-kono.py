#!/usr/bin/env python3
# DEPRECATED (W6 H218): sqlite-lembed has been retired. All embedding calls now
# route through the ollama backend (GRIP_EMBED_BACKEND=ollama). This migration
# script pre-dates the ollama switch and is retained only for historical reference.
# It contains no sqlite_lembed imports and can be safely ignored on new installs.
"""
KONO Migration Script
Migrates existing embeddings to KONO schema with 4D coordinates.

Part of GRIP-KONO Phase 5 implementation.

Usage:
    python3 migrate-to-kono.py [--dry-run] [--backup]

Options:
    --dry-run   Show what would be migrated without making changes
    --backup    Create backup before migration (recommended)
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path.home() / ".claude" / "lib"))

CLAUDE_DIR = Path.home() / ".claude"
DB_PATH = CLAUDE_DIR / "embeddings.db"
BACKUP_DIR = CLAUDE_DIR / "backups"

_TYPE_TO_TOPIC = {
    "prompt": 0, "response": 1, "concept": 2, "checkpoint": 3,
    "plan": 4, "skill": 5, "error": 6, "learning": 7, "pattern": 8,
}

_DEFAULT_TOPICS = [
    ("prompt", "User prompts", 0),
    ("response", "AI responses", 1),
    ("concept", "Conceptual embeddings", 2),
    ("checkpoint", "Session checkpoints", 3),
    ("plan", "Plans and workflows", 4),
    ("skill", "Skill references", 5),
    ("error", "Error patterns", 6),
    ("learning", "Learning events", 7),
    ("pattern", "Detected patterns", 8),
]


def backup_database():
    """Create timestamped backup of embeddings.db."""
    if not DB_PATH.exists():
        print("No database to backup")
        return None
    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"embeddings_pre_kono_{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    print(f"Backup created: {backup_path}")
    return backup_path


def _pending_embeddings(conn):
    return list(conn.execute("""
        SELECT id, type, metadata, created_at FROM embeddings
        WHERE grid_x = 0 AND grid_y = 0 AND dimension = 0 OR grid_x IS NULL
    """))


def _show_dry_run_stats(results, route_dimension):
    by_type: dict = {}
    for row in results:
        t = row[1] or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    print("\nBy type:")
    for t, count in sorted(by_type.items()):
        print(f"  {t}: {count} → dim={route_dimension(t)}, y={_TYPE_TO_TOPIC.get(t, 0)}")


def _coordinates(embed_type: str, metadata_json: str | None, route_dimension) -> tuple:
    grid_x = 0
    if metadata_json:
        try:
            json.loads(metadata_json)
        except json.JSONDecodeError:
            pass
    return grid_x, _TYPE_TO_TOPIC.get(embed_type, 0), 5, route_dimension(embed_type)


def _apply_updates(conn, results, route_dimension):
    migrated = 0
    for emb_id, embed_type, metadata_json, _ in results:
        embed_type = embed_type or "prompt"
        gx, gy, gz, dim = _coordinates(embed_type, metadata_json, route_dimension)
        conn.execute("""
            UPDATE embeddings SET grid_x=?, grid_y=?, grid_z=?, dimension=?, fitness_score=0.5
            WHERE id=?
        """, (gx, gy, gz, dim, emb_id))
        migrated += 1
        if migrated % 100 == 0:
            print(f"  Migrated {migrated}/{len(results)}...")
    return migrated


def migrate_embeddings(conn, dry_run: bool = False) -> int:
    from kono import route_dimension
    results = _pending_embeddings(conn)
    print(f"Found {len(results)} embeddings to migrate")
    if dry_run:
        _show_dry_run_stats(results, route_dimension)
        return len(results)
    migrated = _apply_updates(conn, results, route_dimension)
    print(f"\nMigrated {migrated} embeddings")
    return migrated


def create_default_topics(conn, dry_run: bool = False):
    if dry_run:
        print(f"\nWould create {len(_DEFAULT_TOPICS)} default topics")
        return
    for name, keywords, y_coord in _DEFAULT_TOPICS:
        conn.execute("""
            INSERT OR IGNORE INTO kono_topics (name, y_coord, keywords) VALUES (?, ?, ?)
        """, (name, y_coord, keywords))
    print(f"Created {len(_DEFAULT_TOPICS)} default topics")


def verify_migration(conn) -> bool:
    unmigrated = list(conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE grid_x IS NULL OR dimension IS NULL"
    ))[0][0]
    kono_tables = list(conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'kono_%'"
    ))
    print(f"\n=== Migration Verification ===\nUnmigrated: {unmigrated}  KONO tables: {len(kono_tables)}")
    ok = unmigrated == 0 and len(kono_tables) >= 7
    print("✓ Migration successful" if ok else "⚠ Migration incomplete")
    return ok


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Migrate existing embeddings to KONO schema")
    p.add_argument("--dry-run", action="store_true", help="Preview without changes")
    p.add_argument("--backup", action="store_true", help="Create backup before migration")
    p.add_argument("--verify-only", action="store_true", help="Only verify existing migration")
    return p


def _connect():
    import apsw
    import sqlite_vec
    conn = apsw.Connection(str(DB_PATH))
    conn.enableloadextension(True)
    conn.loadextension(sqlite_vec.loadable_path())
    return conn


def main():
    args = _build_parser().parse_args()

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}\nRun 'python3 lib/embeddings.py init' first")
        sys.exit(1)

    try:
        import sqlite_vec  # noqa: F401 — imported for side-effects in _connect()
    except ImportError:
        print("Missing sqlite-vec extension. Run bootstrap-semantic-rl.sh first")
        sys.exit(1)

    if args.backup and not args.dry_run:
        backup_database()

    conn = _connect()

    if args.verify_only:
        verify_migration(conn)
        conn.close()
        return

    print(f"=== KONO Migration ===\nDatabase: {DB_PATH}\nDry run: {args.dry_run}\n")
    from kono_schema import init_kono_schema
    init_kono_schema(conn)
    create_default_topics(conn, args.dry_run)
    migrated = migrate_embeddings(conn, args.dry_run)
    if not args.dry_run:
        verify_migration(conn)
    conn.close()

    print(f"\n=== Summary ===\nEmbeddings processed: {migrated}")
    if args.dry_run:
        print("\nRun without --dry-run to apply changes")
    else:
        print("\nMigration complete!\nTo rollback: restore from backup in ~/.claude/backups/")


if __name__ == "__main__":
    main()
