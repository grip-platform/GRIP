#!/bin/bash
# GRIP Restore Script - Restores infrastructure from backup
# Usage: grip-restore.sh <backup.tar.gz> [--force]
set -euo pipefail

CLAUDE_DIR="$HOME/.claude"

# No real GRIP backup holds fewer files than this. The floor separates "a small
# restore" from "an archive that did not produce what it should have".
MIN_RESTORE_FILES=10

# A count alone cannot tell a real backup from junk -- ten files under
# .claude/junk/ clear any floor. A genuine GRIP tree also carries these.
GRIP_SENTINEL_DIRS=(lib skills hooks rules docs agents commands bin scripts)
MIN_SENTINEL_DIRS=4

usage() {
    cat <<EOF
Usage: grip-restore.sh <backup.tar.gz> [--force]

Restores GRIP infrastructure from backup archive.

Options:
  --force  Overwrite without prompting (default: prompt for conflicts)

Notes:
  - .env files are NEVER restored (security)
  - Recreate secrets manually after restore
EOF
}

restore_backup() {
    local tarball="$1"
    local force="${2:-false}"

    if [[ ! -f "$tarball" ]]; then
        echo "Error: Backup file not found: $tarball"
        exit 1
    fi

    # Extract to temp location for inspection
    local temp_dir
    temp_dir=$(mktemp -d)
    echo "Extracting backup to temporary location..."
    tar -xzf "$tarball" -C "$temp_dir"

    # sync-mirror-safety.md mandate 3: assert on produced CONTENT, not exit
    # status. `set -euo pipefail` already covers a tar that exits non-zero and
    # an absent .claude/ (rsync itself errors). Neither covers a VALID archive
    # that under-produced: it reaches the `rsync --delete` below and is
    # mirrored onto the install by deleting everything absent from it.
    # `--max-delete=0` cannot substitute for this -- macOS openrsync accepts,
    # documents and silently ignores it (exit 0, everything deleted).
    #
    # A COUNT ALONE IS NOT ENOUGH, and the first version of this guard shipped
    # one: ten files under .claude/junk/ cleared `extracted < 10` and the
    # restore deleted a twenty-file install at exit 0. The archive must also
    # LOOK like a GRIP tree before anything is deleted.
    local extracted present_core sentinel
    extracted=$(find "$temp_dir/.claude" -type f 2>/dev/null | wc -l | tr -d ' ')
    extracted=${extracted:-0}

    if [[ ! -f "$temp_dir/.claude/CLAUDE.md" ]]; then
        echo "Error: the archive has no CLAUDE.md at the root of .claude/." >&2
        echo "  Every GRIP install carries one, so this is not a GRIP backup." >&2
        echo "  Refusing to run 'rsync --delete' against ${CLAUDE_DIR}." >&2
        rm -rf "$temp_dir"
        exit 1
    fi

    present_core=0
    for sentinel in "${GRIP_SENTINEL_DIRS[@]}"; do
        if [[ -d "$temp_dir/.claude/$sentinel" ]]; then
            present_core=$(( present_core + 1 ))
        fi
    done
    if (( present_core < MIN_SENTINEL_DIRS )); then
        echo "Error: the archive holds ${present_core} of GRIP's core directories" >&2
        echo "  (${GRIP_SENTINEL_DIRS[*]}); at least ${MIN_SENTINEL_DIRS} are expected." >&2
        echo "  Refusing to run 'rsync --delete' against ${CLAUDE_DIR}." >&2
        rm -rf "$temp_dir"
        exit 1
    fi

    if (( extracted < MIN_RESTORE_FILES )); then
        echo "Error: the archive yielded only ${extracted} file(s) under .claude/." >&2
        echo "  A GRIP backup holds far more, so this archive is incomplete." >&2
        echo "  Refusing to run 'rsync --delete' against ${CLAUDE_DIR}." >&2
        rm -rf "$temp_dir"
        exit 1
    fi

    # Check for conflicts
    if [[ -d "$CLAUDE_DIR" ]] && [[ "$force" != "true" ]]; then
        # Get modification times (cross-platform)
        local backup_mtime local_mtime
        if [[ "$(uname)" == "Darwin" ]]; then
            backup_mtime=$(stat -f %m "$tarball")
            local_mtime=$(stat -f %m "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null || echo 0)
        else
            backup_mtime=$(stat -c %Y "$tarball")
            local_mtime=$(stat -c %Y "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null || echo 0)
        fi

        if (( local_mtime > backup_mtime )); then
            echo ""
            echo "WARNING: Local GRIP is NEWER than backup."
            echo "  Local:  $(date -r "$local_mtime" 2>/dev/null || date -d @"$local_mtime")"
            echo "  Backup: $(date -r "$backup_mtime" 2>/dev/null || date -d @"$backup_mtime")"
            echo ""
            echo "Options:"
            echo "  1) Replace local with backup (lose local changes)"
            echo "  2) Keep local (abort restore)"
            echo "  3) Backup local first, then restore"
            read -rp "Choice [1/2/3]: " choice

            case "$choice" in
                1)
                    echo "Proceeding with restore..."
                    ;;
                2)
                    echo "Aborted."
                    rm -rf "$temp_dir"
                    exit 0
                    ;;
                3)
                    echo "Backing up local first..."
                    "$CLAUDE_DIR/scripts/grip-backup.sh" full
                    echo "Local backup complete. Proceeding with restore..."
                    ;;
                *)
                    echo "Invalid choice. Aborted."
                    rm -rf "$temp_dir"
                    exit 1
                    ;;
            esac
        fi
    fi

    # Perform restore
    echo "Restoring GRIP infrastructure..."
    if [[ -d "$CLAUDE_DIR" ]]; then
        rsync -av --delete \
            --exclude='.env' \
            --exclude='.env.*' \
            "$temp_dir/.claude/" "$CLAUDE_DIR/"
    else
        mv "$temp_dir/.claude" "$CLAUDE_DIR"
    fi

    # Cleanup
    rm -rf "$temp_dir"

    echo ""
    echo "Restore complete."
    echo ""
    echo "IMPORTANT: .env files are NOT restored for security."
    echo "Recreate ~/.claude/.env manually if needed:"
    echo "  GRIP_TEAM_PASSWORD=your_password"
    echo ""
}

# Parse arguments
if [[ $# -eq 0 ]] || [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    usage
    exit 0
fi

tarball="$1"
force="false"
[[ "${2:-}" == "--force" ]] && force="true"

restore_backup "$tarball" "$force"
