#!/usr/bin/env bash
# safe-git.sh — Lock-aware git wrapper for GRIP hooks.
#
# Wraps git commands that require the index lock with retry logic.
# Prevents "Unable to create '.git/index.lock'" errors from GRIP hooks
# running concurrent git status/diff calls during tool execution.
#
# Usage:
#   source lib/safe-git.sh
#   safe_git status          # auto-retry on index.lock collision
#   safe_git diff --cached   # same, with retries
#
# Environment:
#   SAFE_GIT_MAX_RETRIES    (default: 3)
#   SAFE_GIT_RETRY_DELAY    (default: 0.2 seconds)
#   SAFE_GIT_STALE_LOCK_AGE (default: 60 seconds, 0-byte locks always cleaned)

SAFE_GIT_MAX_RETRIES="${SAFE_GIT_MAX_RETRIES:-3}"
SAFE_GIT_RETRY_DELAY="${SAFE_GIT_RETRY_DELAY:-0.2}"
SAFE_GIT_STALE_LOCK_AGE="${SAFE_GIT_STALE_LOCK_AGE:-60}"

# Remove stale index.lock ONLY when no live process holds it.
#
# SAFETY FIX (2026-04-11, Gen 509 ultrado): the prior version removed any
# 0-byte lock file unconditionally. That is UNSAFE — git creates
# .git/index.lock with 0 bytes as its first step when acquiring the lock,
# then writes to it. A concurrent safe_git call could delete a legitimately
# held lock during that race window and corrupt a real commit.
#
# New staleness criteria (all required):
#   1. File older than SAFE_GIT_STALE_LOCK_AGE seconds (default 60)
#   2. No live process has the file open (checked via lsof)
#
# Size is no longer used as a staleness signal. For truly stuck locks,
# inspect with `python3 -m lib.git_lock status` first, then delete by hand.
#
# Returns 0 if lock was removed, 1 otherwise.
_clean_stale_lock() {
    local git_dir
    git_dir=$(git rev-parse --git-dir 2>/dev/null) || return 1
    local lock_file="$git_dir/index.lock"
    [[ -f "$lock_file" ]] || return 1

    # NOTE: do NOT pass `--` before positional args to `python3 -c`. Python's
    # `-c` mode does not consume the separator; it ends up at sys.argv[1] and
    # shifts all real args by one position. The pre-fix version had this bug
    # for years, masked by the retry loop silently eating the ValueError.
    GRIP_LOCK_PATH="$lock_file" \
    GRIP_LOCK_MAX_AGE="$SAFE_GIT_STALE_LOCK_AGE" \
    python3 -c "
import os, subprocess, sys, time
lock = os.environ['GRIP_LOCK_PATH']
max_age = int(os.environ['GRIP_LOCK_MAX_AGE'])
try:
    age = time.time() - os.path.getmtime(lock)
except OSError:
    sys.exit(1)
if age <= max_age:
    sys.exit(1)
try:
    r = subprocess.run(
        ['lsof', '-t', lock],
        capture_output=True, text=True, timeout=5,
    )
    if r.stdout.strip():
        sys.exit(1)
except (FileNotFoundError, subprocess.TimeoutExpired):
    sys.exit(1)
try:
    os.unlink(lock)
    sys.exit(0)
except OSError:
    sys.exit(1)
"
}

safe_git() {
    local attempt=0
    local output
    local exit_code
    local cleaned_stale=0

    while [[ $attempt -lt $SAFE_GIT_MAX_RETRIES ]]; do
        output=$(git "$@" 2>&1)
        exit_code=$?

        if [[ $exit_code -eq 0 ]]; then
            echo "$output"
            return 0
        fi

        if echo "$output" | grep -q "index.lock"; then
            # On first lock failure, try stale cleanup before wasting retries
            if [[ $cleaned_stale -eq 0 ]]; then
                cleaned_stale=1
                if _clean_stale_lock; then
                    echo "[safe-git] cleaned stale index.lock" >&2
                    continue  # Retry immediately, don't increment attempt
                fi
            fi
            attempt=$(( attempt + 1 ))
            if [[ $attempt -lt $SAFE_GIT_MAX_RETRIES ]]; then
                sleep "$SAFE_GIT_RETRY_DELAY"
            fi
        else
            # Non-lock error — propagate immediately
            echo "$output" >&2
            return $exit_code
        fi
    done

    echo "$output" >&2
    return $exit_code
}

# Cache for git status to avoid redundant calls within same hook execution
_SAFE_GIT_STATUS_CACHE=""
_SAFE_GIT_STATUS_CACHE_PID=""

cached_git_status() {
    local current_pid=$$
    if [[ -z "$_SAFE_GIT_STATUS_CACHE" ]] || [[ "$_SAFE_GIT_STATUS_CACHE_PID" != "$current_pid" ]]; then
        _SAFE_GIT_STATUS_CACHE=$(safe_git status --porcelain 2>/dev/null)
        _SAFE_GIT_STATUS_CACHE_PID="$current_pid"
    fi
    echo "$_SAFE_GIT_STATUS_CACHE"
}

invalidate_git_cache() {
    _SAFE_GIT_STATUS_CACHE=""
    _SAFE_GIT_STATUS_CACHE_PID=""
}
