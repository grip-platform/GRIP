#!/usr/bin/env bash
# KISS fix for the git-crypt catch-22:
#   - Encrypted files show as dirty when keyring is locked
#   - git-crypt unlock requires a clean working tree
#   - Can't clean the files because they're dirty FROM being locked
#
# Solution: assume-unchanged on all encrypted files, unlock, undo.
# Run from ~/.claude or any git-crypt repo.
#
# Usage: bash lib/git-crypt-unlock.sh [key-file-path]

set -euo pipefail

KEY_FILE="${1:-}"
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)

if [ -z "$REPO_ROOT" ]; then
    echo "[ERROR] Not in a git repository"
    exit 1
fi

cd "$REPO_ROOT"

# Check if already unlocked by testing a known encrypted file for the
# GITCRYPT binary header. If the file is readable text (no header),
# the repo is already unlocked.
# Cross-platform: uses Python binary read (works on macOS, Linux, Windows Git Bash).
PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"
if [ -n "$PYTHON" ]; then
    # Find a file that should be encrypted per .gitattributes
    SENTINEL=""
    for candidate in "facts/identity.md" "facts/team.md" "plugins/blocklist.json"; do
        if [ -f "$candidate" ]; then
            SENTINEL="$candidate"
            break
        fi
    done

    if [ -n "$SENTINEL" ]; then
        IS_ENCRYPTED=$("$PYTHON" -c "
import sys
try:
    with open('${SENTINEL}', 'rb') as f:
        print('yes' if f.read(9) == b'\x00GITCRYPT' else 'no')
except Exception:
    print('unknown')
" 2>/dev/null)

        if [ "$IS_ENCRYPTED" = "no" ]; then
            echo "[OK] git-crypt already unlocked"
            exit 0
        fi
    fi
else
    # Fallback without Python: check if sentinel file starts with NUL byte
    if [ -f "facts/identity.md" ]; then
        FIRST_BYTE=$(od -A n -t x1 -N 1 "facts/identity.md" 2>/dev/null | tr -d ' ')
        if [ "$FIRST_BYTE" != "00" ]; then
            echo "[OK] git-crypt already unlocked"
            exit 0
        fi
    fi
fi

echo "[1/4] Finding git-crypt encrypted files..."
ENCRYPTED_FILES=$(git-crypt status -e 2>/dev/null | sed 's/^.*: //' || true)

if [ -z "$ENCRYPTED_FILES" ]; then
    echo "[OK] No encrypted files found"
    exit 0
fi

FILE_COUNT=$(echo "$ENCRYPTED_FILES" | wc -l | tr -d ' ')
echo "      Found ${FILE_COUNT} encrypted files"

echo "[2/4] Marking encrypted files as assume-unchanged..."
echo "$ENCRYPTED_FILES" | while IFS= read -r f; do
    git update-index --assume-unchanged -- "$f" 2>/dev/null || true
done

echo "[3/4] Unlocking git-crypt..."
UNLOCK_EXIT=0
if [ -n "$KEY_FILE" ]; then
    git-crypt unlock "$KEY_FILE" || UNLOCK_EXIT=$?
else
    git-crypt unlock || UNLOCK_EXIT=$?
fi

echo "[4/4] Restoring file tracking..."
echo "$ENCRYPTED_FILES" | while IFS= read -r f; do
    git update-index --no-assume-unchanged -- "$f" 2>/dev/null || true
done

if [ "$UNLOCK_EXIT" -eq 0 ]; then
    echo "[OK] git-crypt unlocked successfully. ${FILE_COUNT} files decrypted."
else
    echo "[ERROR] git-crypt unlock failed (exit ${UNLOCK_EXIT}). Files re-tracked."
    exit "$UNLOCK_EXIT"
fi
