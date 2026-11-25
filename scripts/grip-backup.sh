#!/bin/bash
# GRIP Backup Script - Creates tiered infrastructure backups
# Usage: grip-backup.sh [quick|full|complete] [--output DIR]
set -euo pipefail

CLAUDE_DIR="$HOME/.claude"
BACKUP_DIR="${GRIP_BACKUP_DIR:-$HOME/backups/grip}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

usage() {
    cat <<EOF
Usage: grip-backup.sh [quick|full|complete] [--output DIR]

Tiers:
  quick    - Config + skills only (~3MB, <5s)
  full     - All infrastructure (~5MB, <10s)  [default]
  complete - Including projects/ (~73MB, <30s)

Options:
  --output DIR  Output directory (default: ~/backups/grip)

Environment:
  GRIP_BACKUP_DIR  Override default backup directory
EOF
}

create_backup() {
    local tier="${1:-full}"
    local output_dir="${2:-$BACKUP_DIR}"

    mkdir -p "$output_dir"
    local tarball="$output_dir/grip-$tier-$TIMESTAMP.tar.gz"

    # Base excludes (always)
    local excludes=(
        --exclude='.env'
        --exclude='.env.*'
        --exclude='*.db'
        --exclude='*.jsonl'
        --exclude='commands-index.json'
        --exclude='stats-cache.json'
        --exclude='cache'
        --exclude='debug'
        --exclude='contexts'
        --exclude='statsig'
        --exclude='todos'
        --exclude='session-env'
        --exclude='shell-snapshots'
        --exclude='file-history'
        --exclude='image-cache'
        --exclude='*.tar.gz'
        --exclude='.git'
        --exclude='site'
        --exclude='node_modules'
        --exclude='.vercel'
        --exclude='__pycache__'
        --exclude='*.pyc'
        --exclude='*.log'
        --exclude='.hooks.log'
        --exclude='.DS_Store'
        --exclude='*.swp'
        --exclude='*-pre-bounce'
    )

    # Tier-specific excludes
    case "$tier" in
        quick)
            excludes+=(
                --exclude='projects'
                --exclude='bin'
                --exclude='hooks'
                --exclude='scripts'
                --exclude='config'
                --exclude='plugins'
                --exclude='homebrew-tap'
                --exclude='.github'
            )
            ;;
        full)
            excludes+=(--exclude='projects')
            ;;
        complete)
            # Include everything (no additional excludes)
            ;;
        *)
            echo "Error: Unknown tier '$tier'. Use quick, full, or complete."
            exit 1
            ;;
    esac

    echo "Creating $tier backup..."
    # ~/.claude is a SYMLINK to ~/.grip in the canonical GRIP layout. Default
    # tar archives the symlink itself (a 1-entry, ~100B tarball — silently
    # empty backup). Follow symlinks so the real tree is archived. The flag
    # differs by tar flavour: bsdtar (macOS) = -L, GNU tar = -h.
    local deref="-L"
    if tar --version 2>/dev/null | grep -qi "GNU tar"; then
        deref="-h"
    fi
    tar -czf "$tarball" "$deref" "${excludes[@]}" -C "$HOME" .claude 2>/dev/null || {
        echo "Warning: Some files could not be read (permissions). Backup continues."
    }

    local size
    size=$(ls -lh "$tarball" | awk '{print $5}')
    echo "Backup created: $tarball ($size)"
    echo "$tarball"
}

# Parse arguments
tier="full"
output_dir="$BACKUP_DIR"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --output)
            output_dir="$2"
            shift 2
            ;;
        quick|full|complete)
            tier="$1"
            shift
            ;;
        *)
            echo "Error: Unknown argument '$1'"
            usage
            exit 1
            ;;
    esac
done

create_backup "$tier" "$output_dir"
