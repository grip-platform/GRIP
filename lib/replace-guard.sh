#!/usr/bin/env bash
#
# replace-guard.sh - refuse to destroy a populated directory for an empty one.
#
# The defect this closes, in one line: `[[ -d "$source/$dir" ]]` authorising
# `rm -rf "$target/$dir"`. An EMPTY directory satisfies -d, so a half-built or
# mid-clone source tree passes the test and the installed directory is deleted.
#
# First fixed for `rsync --delete` in GRIP#5999. The predicate is identical and
# the primitive is irrelevant, so it lives here once rather than being spelled
# out at each callsite -- five copies of a safety check drift, and they drift
# silently, which is the whole problem.
#
# Sourced from the SOURCE tree, never the target: a target's lib/ may be exactly
# what is being replaced, while a source tree missing its own lib is one that
# should not be installed from at all.

# How many times more files the target may hold before a replacement is refused.
# 4 means: a source with at least a quarter of the target's file count proceeds.
# It is a blunt instrument on purpose -- it exists to catch "source is empty or
# nearly so", not to adjudicate legitimate shrinkage. A deliberate large deletion
# is rare, visible, and better served by refusing and telling the operator.
: "${GRIP_REPLACE_RATIO:=4}"

# Set by grip_source_outweighs_target so callers can report real numbers rather
# than re-counting. Read them only immediately after a call.
GRIP_REPLACE_SRC_N=0
GRIP_REPLACE_TGT_N=0

# grip_source_outweighs_target SOURCE TARGET
#
# Returns 0 when replacing TARGET with SOURCE is safe, 1 when it must be refused.
#
# Refuses when the source holds no files at all, or when the target holds more
# than GRIP_REPLACE_RATIO times as many. An ABSENT or EMPTY target always
# passes -- that is every fresh install, and refusing there would break the
# only path that creates the directory in the first place.
#
# Under `set -e` a bare call aborts the script on a refusal, so every callsite
# must test it: `if ! grip_source_outweighs_target "$a" "$b"; then ... fi`
grip_source_outweighs_target() {
    local src="$1" tgt="$2"

    GRIP_REPLACE_SRC_N=0
    GRIP_REPLACE_TGT_N=0

    if [[ -d "$src" ]]; then
        GRIP_REPLACE_SRC_N=$(find "$src" -type f 2>/dev/null | wc -l | tr -d ' ')
    fi
    if [[ -d "$tgt" ]]; then
        GRIP_REPLACE_TGT_N=$(find "$tgt" -type f 2>/dev/null | wc -l | tr -d ' ')
    fi

    # A source with nothing in it can never justify destroying anything.
    if (( ${GRIP_REPLACE_SRC_N:-0} == 0 )); then
        return 1
    fi
    # A populated target that dwarfs the source is the half-built-source case.
    if (( ${GRIP_REPLACE_TGT_N:-0} > 0 )) \
        && (( ${GRIP_REPLACE_SRC_N:-0} * GRIP_REPLACE_RATIO < ${GRIP_REPLACE_TGT_N:-0} )); then
        return 1
    fi
    return 0
}

# grip_link_roots_are_sane SOURCE_ROOT TARGET_ROOT
#
# Returns 0 when it is safe to build "$SOURCE_ROOT/$dir" -> "$TARGET_ROOT/$dir"
# links or copies, 1 when it must be refused.
#
# The defect this closes, measured 2026-10-10: a test called create_symlinks with
# no arguments, so both roots were empty and the loop over CORE_DIRS ran
# `ln -sfn "/bin" "/bin"` and `ln -sfn "/lib" "/lib"`. On a usr-merged distro
# /bin is itself a symlink, `-d /bin` is true, and `-n -f` replaces that symlink
# with one pointing at itself -- as root, every later exec fails with ELOOP.
#
# Refuses a root that is empty, or made only of slashes and dots ("/", "//",
# "/.", ".", ".."): appending "/$dir" to any of those names a system directory
# or the caller's cwd, never an install tree. Also refuses identical roots,
# because a link onto its own source is a self-loop whatever the path.
#
# Both checks run on the RESOLVED path too: `~/.claude -> ~/.grip` makes
# `--source ~/.grip` a different string for the same directory, and a `..` alias
# such as "/tmp/.." spells "/" without looking like it.
#
# Checks the ROOTS, not each "$root/$dir": by the time "/bin" exists there is
# no telling whether it was meant.
grip_link_roots_are_sane() {
    local src="${1:-}" tgt="${2:-}" p rsrc rtgt
    for p in "$src" "$tgt"; do
        if [[ -z "$p" ]] || [[ "$p" =~ ^[/.]+$ ]]; then
            return 1
        fi
    done
    rsrc=$(_grip_resolve_root "$src")
    rtgt=$(_grip_resolve_root "$tgt")
    for p in "$rsrc" "$rtgt"; do
        if [[ -z "$p" ]] || [[ "$p" =~ ^[/.]+$ ]]; then
            return 1
        fi
    done
    [[ "$rsrc" != "$rtgt" ]]
}

# _grip_resolve_root PATH -- print PATH with every symlink and `..` resolved.
# A target that does not exist yet (every fresh install) resolves through its
# parent; one whose parent is missing too is printed with only trailing slashes
# removed, since there is nothing on disk for it to alias.
_grip_resolve_root() {
    local p="$1" parent base
    if [[ -d "$p" ]]; then
        (CDPATH='' cd -P -- "$p" 2>/dev/null && pwd -P)
        return
    fi
    parent=$(dirname -- "$p")
    base=$(basename -- "$p")
    if [[ -d "$parent" ]] && [[ "$base" != "." ]] && [[ "$base" != ".." ]]; then
        parent=$(CDPATH='' cd -P -- "$parent" 2>/dev/null && pwd -P)
        printf '%s/%s\n' "${parent%/}" "$base"
        return
    fi
    while [[ "$p" == */ ]] && [[ "$p" != "/" ]]; do p="${p%/}"; done
    printf '%s\n' "$p"
}

# Fail loudly if this file loaded without defining the function -- a truncated
# or partially-written copy would otherwise leave every call returning 127,
# which `if ! grip_source_outweighs_target ...` reads as a REFUSAL. The install
# would then skip every directory and look like it merely had nothing to do.
# Measured: that is exactly what happened when a test harness eval'd a consuming
# function's body without sourcing this file.
if ! declare -F grip_source_outweighs_target >/dev/null 2>&1 \
    || ! declare -F grip_link_roots_are_sane >/dev/null 2>&1 \
    || ! declare -F _grip_resolve_root >/dev/null 2>&1; then
    echo "FATAL: replace-guard.sh loaded but its guard functions are undefined" >&2
    return 1 2>/dev/null || exit 1
fi
