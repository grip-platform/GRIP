#!/usr/bin/env bash
# setup-local-git.sh — one-time per-clone setup
#
# Configures local git state for GRIP development:
# 1. Checks git-crypt unlock status
# 2. Sets skip-worktree on learning/*.jsonl to prevent branch-switch conflicts
#
# learning/*.jsonl are append-only session logs shared across all branches.
# git stash cannot cleanly save them (git-crypt re-decrypts on checkout,
# leaving the working tree "dirty" and blocking branch switches).
#
# --skip-worktree tells git: "the working tree version is intentionally
# ahead of HEAD — don't touch this file on branch switches."
# The file stays accumulating locally; explicit `git add` still works.

set -euo pipefail

# Cross-platform Python detection (Windows uses 'python', macOS/Linux use 'python3')
PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"

echo "GRIP local git setup"
echo "===================="

# Check git-crypt
if command -v git-crypt &>/dev/null; then
    # Detect unlock status via binary header check on a known encrypted file
    GC_UNLOCKED=false
    for _sentinel in "facts/identity.md" "facts/team.md"; do
        if [ -f "$_sentinel" ] && [ -n "$PYTHON" ]; then
            _is_enc=$("$PYTHON" -c "
import sys
try:
    with open('${_sentinel}', 'rb') as f:
        print('yes' if f.read(9) == b'\x00GITCRYPT' else 'no')
except Exception:
    print('unknown')
" 2>/dev/null)
            if [ "$_is_enc" = "no" ]; then
                GC_UNLOCKED=true
            fi
            break
        fi
    done

    if [ "$GC_UNLOCKED" = true ]; then
        echo "[OK] git-crypt: unlocked"
    else
        echo "[!!] git-crypt: locked or not configured"
        echo "     Run: git-crypt unlock /path/to/key"
        echo "     Get the key from a team member."

        # Non-keyholder protection: set skip-worktree on all encrypted files
        # to prevent phantom diffs for developers without decryption keys
        echo ""
        echo "Setting skip-worktree on encrypted files (non-keyholder protection)..."
        git-crypt status -e 2>/dev/null | sed 's/^.*: //' | while IFS= read -r _ef; do
            [ -z "$_ef" ] && continue
            git update-index --skip-worktree -- "$_ef" 2>/dev/null && echo "  skip-worktree: $_ef"
        done
    fi
else
    echo "[!!] git-crypt: not installed (brew install git-crypt)"
fi

# Set skip-worktree flags
#
# The list lives in config/skip-worktree-paths.txt so this script and
# verify-clone.sh cannot disagree about which files are covered. It used to be
# the inline glob `learning/*.jsonl`, which stopped covering the class when five
# of those accumulators moved to data/ and to the repo root.
#
# Failures are COUNTED and reported. They used to be swallowed: the body was
# `git update-index ... && echo ...`, and a command whose failure is tested by
# `&&` does not trip `set -e`, so the loop carried on and the script printed
# "Done." and exited 0. Measured 2026-09-22 on the shared tree, where two files
# silently kept no flag because a concurrent git process held .git/index.lock --
# which is routine here, with many sessions and launchd agents sharing one tree.
# Hence the retry as well as the count: the lock is transient, so give it a
# moment before calling it a failure.
echo ""
SKIP_LIST="config/skip-worktree-paths.txt"
if [ ! -f "$SKIP_LIST" ]; then
    echo "[!!] $SKIP_LIST is missing -- cannot set skip-worktree flags"
    SKIP_FAILED=1
else
    echo "Setting skip-worktree on the paths in $SKIP_LIST..."
    SKIP_FAILED=0
    SKIP_SET=0
    # Strip trailing comments and surrounding blanks, drop comment and empty
    # lines, then let the shell expand any globs.
    while IFS= read -r _pattern; do
        _pattern="${_pattern%%#*}"
        _pattern="$(printf '%s' "$_pattern" | tr -d '[:space:]')"
        [ -z "$_pattern" ] && continue
        for f in $_pattern; do
            [ -f "$f" ] || continue
            git ls-files --error-unmatch "$f" >/dev/null 2>&1 || continue
            _done=0
            for _attempt in 1 2 3; do
                if git update-index --skip-worktree "$f" 2>/dev/null; then
                    _done=1
                    break
                fi
                sleep 1
            done
            if [ "$_done" = "1" ]; then
                SKIP_SET=$((SKIP_SET + 1))
                echo "  skip-worktree: $f"
            else
                SKIP_FAILED=$((SKIP_FAILED + 1))
                echo "  [!!] could not set skip-worktree: $f (index.lock held?)"
            fi
        done
    done < "$SKIP_LIST"
    echo "  $SKIP_SET flag(s) set, $SKIP_FAILED failed"
fi

# Merge renormalize (prevents smudge filter conflicts on pull)
echo ""
git config merge.renormalize true && echo "[OK] merge.renormalize = true"

# The `ours` driver. .gitattributes marks the wholesale-regenerated substrate
# (data/genome-shared.json, data/genomes/*.json, rules/learned-insights.md)
# merge=ours, but git builds in only text, binary and union. An undefined name
# falls back to an ordinary text merge, so a pull that meets a local rewrite
# stops with conflict markers inside JSON. `true` leaves git's chosen side in
# place and exits 0. Registered outside the Python check below because it
# needs no interpreter. Details: docs/substrate-merge-strategy.md.
git config merge.ours.name "keep the side being merged into (regenerated substrate)"
git config merge.ours.driver true && echo "[OK] merge driver 'ours' configured"

# Substrate merge drivers (feat/substrate-merge-strategy)
# .gitattributes maps substrate file-classes to these driver names; the
# drivers themselves must be registered per-clone (git config cannot live in
# .gitattributes). Cross-platform: invoke via the detected python (Windows
# Git Bash has 'python' not 'python3'). Unregistered driver => git falls back
# to a normal conflict (safe; never corrupts).
echo ""
if [ -n "$PYTHON" ] && [ -f scripts/substrate_merge.py ]; then
    git config merge.grip-substrate-union.name \
        "GRIP substrate: dedup-union append-only JSONL"
    git config merge.grip-substrate-union.driver \
        "$PYTHON scripts/substrate_merge.py jsonl-union %O %A %B %P"
    git config merge.grip-substrate-keyed.name \
        "GRIP substrate: id-keyed reconcile (hypotheses.jsonl)"
    git config merge.grip-substrate-keyed.driver \
        "$PYTHON scripts/substrate_merge.py jsonl-keyed %O %A %B %P"
    echo "[OK] substrate merge drivers configured (union + keyed)"
else
    echo "[!!] substrate merge drivers NOT configured"
    echo "     (python or scripts/substrate_merge.py missing — pulls will"
    echo "      conflict on substrate until this is resolved)"
fi

# grip-pull alias (fetch + sync encrypted files + pull)
git config alias.grip-pull '!bash scripts/grip-pull.sh'
echo "[OK] git grip-pull alias configured"

# Windows GPG path auto-detection
# Git Bash on Windows may not find gpg unless gpg.program is set explicitly
if [[ "$OSTYPE" == msys* || "$OSTYPE" == mingw* || "$OSTYPE" == cygwin* ]]; then
    if command -v gpg &>/dev/null; then
        GPG_PATH=$(command -v gpg)
        git config --local gpg.program "$GPG_PATH"
        echo "[OK] gpg.program set to $GPG_PATH"
    else
        echo "[!!] gpg not found in PATH (needed for git-crypt)"
        echo "     Install: choco install gnupg"
    fi
fi

# Store key path if provided as argument
if [ -n "${1:-}" ] && [ -f "$1" ]; then
    git config --local grip.keypath "$1"
    echo "[OK] Key path stored: $1"
fi

echo ""
if [ "${SKIP_FAILED:-0}" -gt 0 ]; then
    # Reporting success while a setup step silently failed is the defect this
    # exit code closes, not a stylistic preference: the caller is usually a
    # fresh clone or a script, and neither reads the lines above.
    echo "INCOMPLETE. $SKIP_FAILED skip-worktree flag(s) could not be set -- re-run this script."
    echo "Everything else is configured. Run 'bash scripts/verify-clone.sh' to see what is missing."
    exit 1
fi
echo "Done. Run 'bash scripts/verify-clone.sh' to validate."
echo "Use 'git grip-pull' instead of 'git pull' for safe pulling."
