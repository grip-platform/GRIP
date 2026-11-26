#!/usr/bin/env bash
# grip-worktree.sh — git-crypt-safe worktree creation with safety guards
#
# Problem: git-crypt stores decrypted keys in .git/git-crypt/keys/ but worktrees
# have a separate git dir (.git/worktrees/<name>/) and git-crypt doesn't follow
# the commondir reference. The smudge filter fails during checkout.
#
# Solution: Create worktree with --no-checkout, place the git-crypt keys in the
# worktree's git dir, then checkout, so the smudge filter finds them. macOS and
# Linux symlink the main .git/git-crypt. Windows copies it, because Git for
# Windows deletes through a junction when it removes a worktree (GRIP#8705).
#
# Concurrency: Branch lock files in state/branch-locks/ prevent two sessions
# from checking out the same branch. For multi-session work on one feature,
# use the naming convention: feat/<issue>-<slug>-s<N> (e.g. -s1, -s2).
#
# Dependency: lib/path-encoding.sh must sit beside this script. It is sourced
# at start-up for encode_project_path, Claude Code's project-folder naming
# (GRIP#8696).
#
# Usage:
#   grip-worktree add <path> [branch] [--session-id ID] [--lock-mutex] [--base REF]
#     Create worktree. --lock-mutex opts into the atomic worktree-add
#     mutex that closes the TOCTOU race between the branch-lock check
#     and the lock-set; uses POSIX atomic mkdir on
#     .git/worktree-mutex.d so it works on macOS without GNU flock.
#     Timeout via GRIP_WORKTREE_MUTEX_TIMEOUT (default 30s).
#     --base REF starts a NEW branch from REF (e.g. origin/main) instead
#     of the main tree's current HEAD — without it, a new session branch
#     inherits whatever branch the shared tree happens to have checked
#     out (branch contamination at birth). Ignored for existing branches;
#     an unresolvable REF warns and falls back to HEAD (never blocks).
#   grip-worktree remove <path> [--force] # Remove worktree and clean up lock
#     Refuses when the worktree has uncommitted changes to TRACKED files and
#     names them, so real work is never discarded silently. --force discards
#     them. Untracked/gitignored churn (state/, data/, cache/) is always
#     removed, with or without --force.
#   grip-worktree list                   # List all worktrees
#   grip-worktree teardown --preserve-logs <path> [--rescue-to <dir>]
#     Partial cleanup: drop the workdir/ subtree but rescue logs/ and output/
#     to a sibling preserved-{name} directory BEFORE tearing down the worktree.
#     Absorbed from multica execenv (W4a research-council audit, grade-A).
#   grip-worktree migrate-project-dirs
#     Convert every registered worktree's ~/.claude/projects/<slug> entry from
#     the legacy whole-directory symlink into a real directory whose memory/
#     and grip/ subdirectories are symlinked instead (GRIP #6370). Claude Code
#     refuses to persist large hook output through a symlinked project dir, so
#     until a worktree is migrated its sessions silently lose everything past
#     the first 10,000 characters of the SessionStart rules injection.
#     Idempotent, and skips any worktree with a live session.
#   grip-worktree default-branch [repo_path]
#     Resolve the default branch of a repo via cascade:
#       1. git symbolic-ref refs/remotes/origin/HEAD
#       2. origin/main
#       3. origin/master
#       4. HEAD (fallback for detached/bare repos)
#     Absorbed from multica execenv/git.go (W4a research-council audit).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Claude Code's project-folder names come from GRIP's shared shell encoder
# (encode_project_path), used by _encode_path below (GRIP#8696).
source "$SCRIPT_DIR/path-encoding.sh"

# _resolve_main_git_dir: absolute path to the repo's SHARED git directory.
#
# `$REPO_ROOT/.git` is only correct in a normal checkout. In a linked worktree
# `.git` is a gitlink FILE pointing at .git/worktrees/<name>, so a `-d` test on
# it is false. That mattered because the git-crypt step below is guarded by
# `[ -d "$MAIN_GIT_DIR/git-crypt" ]`: the guard failed, the key symlink was
# skipped SILENTLY, and checkout then died inside the smudge filter with
# "git-crypt: Error: Unable to open key file" — an error naming encryption for
# what is really a path-resolution bug, followed by a rollback.
#
# It fires precisely in the nested case that concurrent multi-agent work
# produces constantly: a session already inside a worktree runs
# `bash lib/grip-worktree.sh add ...`, which resolves to the WORKTREE's own
# copy of this script, so SCRIPT_DIR/.. is the worktree root rather than the
# main checkout. Two background agents hit it on 2026-08-10 (GRIP #4491).
#
# `git rev-parse --git-common-dir` answers with the shared directory from
# either location. It may answer relatively (a bare `.git`), so resolve it
# against $root to get an absolute path. Falls back to the old literal when
# git cannot answer at all, which keeps non-repo callers behaving as before.
_resolve_main_git_dir() {
    local root="$1" common
    if common="$(git -C "$root" rev-parse --git-common-dir 2>/dev/null)" \
        && [ -n "$common" ] \
        && common="$(cd "$root" 2>/dev/null && cd "$common" 2>/dev/null && pwd)"; then
        printf '%s\n' "$common"
        return 0
    fi
    printf '%s\n' "$root/.git"
}

# Default repo: the one this script ships in (GRIP ~/.claude). Overridable
# per-invocation via the --repo flag. See _resolve_repo_root below.
#
# Before 2026-04-18 these were hardcoded to the script-location, so calling
# `grip-worktree add /tmp/hal-203 fix/foo` from ~/.hal created the worktree
# against GRIP instead of HAL. --repo fixes that class of cross-repo bug.
DEFAULT_REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$DEFAULT_REPO_ROOT"
MAIN_GIT_DIR="$(_resolve_main_git_dir "$REPO_ROOT")"
LOCK_DIR="$REPO_ROOT/state/branch-locks"

# _resolve_repo_root: reassign REPO_ROOT / MAIN_GIT_DIR / LOCK_DIR for this
# invocation. Call it at the top of each top-level subcommand AFTER its arg
# parser has captured the optional --repo override. Empty arg => default.
_resolve_repo_root() {
    local explicit_repo="${1:-}"
    if [ -z "$explicit_repo" ]; then
        REPO_ROOT="$DEFAULT_REPO_ROOT"
    else
        if ! REPO_ROOT="$(cd "$explicit_repo" 2>/dev/null && pwd)"; then
            echo "ERROR: --repo '$explicit_repo' does not exist" >&2
            return 1
        fi
        # git rev-parse is the authoritative "is this a git repo" check;
        # a stray `.git` directory on a non-repo path would fool a -d test.
        if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
            echo "ERROR: --repo '$REPO_ROOT' is not a git repository" >&2
            return 1
        fi
    fi
    MAIN_GIT_DIR="$(_resolve_main_git_dir "$REPO_ROOT")"
    LOCK_DIR="$REPO_ROOT/state/branch-locks"
}

# --- Platform detection ---
_IS_WINDOWS=false
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) _IS_WINDOWS=true ;; esac

# --- Branch locking helpers ---

_lock_file_for() {
    local branch="$1"
    local safe_name="${branch//\//_}"
    echo "$LOCK_DIR/$safe_name.lock"
}

_check_branch_lock() {
    local branch="$1"
    local lock_file
    lock_file="$(_lock_file_for "$branch")"

    [ -f "$lock_file" ] || return 0

    local locked_pid
    locked_pid="$(grep '^PID=' "$lock_file" | cut -d= -f2)"

    # Stale lock: PID no longer running
    if [ -n "$locked_pid" ] && ! kill -0 "$locked_pid" 2>/dev/null; then
        echo "Removing stale lock for $branch (PID $locked_pid dead)" >&2
        rm -f "$lock_file"
        return 0
    fi

    echo "ERROR: Branch '$branch' is locked by another session" >&2
    cat "$lock_file" >&2
    echo "Use feat/<issue>-<slug>-s<N> naming for multi-session work" >&2
    return 1
}

_set_branch_lock() {
    local branch="$1"
    local worktree_path="$2"
    # Owner PID: the LAUNCHER's pid, which becomes the harness pid via exec and
    # therefore names the long-lived session. Defaults to $$ (this short-lived
    # subprocess) only when no caller supplies one — that default was the whole
    # bug: the recorded pid was dead the instant the worktree finished being
    # created, so the reaper's first liveness guard never protected anything.
    local owner_pid="${3:-$$}"
    mkdir -p "$LOCK_DIR"
    cat > "$(_lock_file_for "$branch")" <<EOF
SESSION_ID=${CLAUDE_SESSION_ID:-unknown}
PID=$owner_pid
TIMESTAMP=$(date +%s)
PATH=$worktree_path
EOF
}

_clear_branch_lock() {
    local branch="$1"
    rm -f "$(_lock_file_for "$branch")"
}

# --- The lock a worktree took (GRIP#8695) ---
#
# `add` locks the branch it starts on, but the worktree may later switch branch
# or detach its HEAD, and then `remove` cannot name that lock from the
# worktree. So `add` keeps a record in the worktree's own git folder: line 1 is
# the lock file's path, the rest an exact copy of the lock. `remove` clears
# that lock only while it still matches the copy, because another worktree may
# have taken the branch's lock since. Matching content needs no path
# comparison, so the C:/ versus /c/ trap of GRIP#8691 cannot come back.
# One invariant makes the match sound: only _set_branch_lock writes a lock, and
# only at `add`. Anything that rewrites a held lock in place must update the
# record as well.
_LOCK_RECORD="grip-branch-lock"

# The worktree's own git folder, absolute. GIT_DIR and GIT_WORK_TREE are
# cleared, because an exported GIT_DIR (as in a git hook) would answer with
# the same folder for every worktree.
_worktree_git_dir() {
    env -u GIT_DIR -u GIT_WORK_TREE git -C "$1" rev-parse --absolute-git-dir
}

_lock_record_file_for() {
    echo "$1/$_LOCK_RECORD"
}

# A failed write leaves no record behind, not a partial one, so `remove` falls
# back to the branch lookup instead of comparing against a missing copy.
_record_branch_lock() {
    local record lock_file
    record="$(_lock_record_file_for "$1")"
    lock_file="$(_lock_file_for "$2")"
    if ! { printf '%s\n' "$lock_file"; cat -- "$lock_file"; } 2>/dev/null > "$record"; then
        rm -f -- "$record" 2>/dev/null || true
        echo "WARN: could not record the branch lock in $1; remove may leave it behind (GRIP#8695)" >&2
    fi
}

# Prints the worktree's record, or nothing unless it is complete: a lock path on
# line 1, then the lock's copy. A record cut short reads as no record at all.
_read_lock_record() {
    local git_dir record_file record nl=$'\n'
    git_dir="$(_worktree_git_dir "$1" 2>/dev/null)" || return 0
    record_file="$(_lock_record_file_for "$git_dir")"
    record="$(cat -- "$record_file" 2>/dev/null)" || return 0
    case "$record" in *"$nl"?*) printf '%s\n' "$record" ;; esac
}

# Clears the recorded lock, but only while it is still the lock this worktree
# took. Split with parameter expansion, not a pipe, so pipefail cannot stop it.
_clear_recorded_lock() {
    local record="$1" nl=$'\n' lock_file taken current
    lock_file="${record%%"$nl"*}"
    taken="${record#*"$nl"}"
    current="$(cat -- "$lock_file" 2>/dev/null)" || return 0
    if [ "$current" = "$taken" ]; then
        rm -f -- "$lock_file"
    else
        echo "NOTE: left $lock_file in place: it no longer matches the lock this worktree took, so another worktree may hold it (GRIP#8695)" >&2
    fi
}

# --- Worktree-add mutex (opt-in via --lock-mutex, closes TOCTOU race) ---
#
# The existing branch-lock primitive (`_check_branch_lock` + `_set_branch_lock`)
# is advisory: it cannot prevent two parallel sessions from passing the
# CHECK independently, then both racing through `git worktree add` and both
# writing their lock file. The losing `git worktree add` errors mid-way
# (git's own internal locking catches it) but the failing session has
# already mutated the lock state.
#
# `_acquire_worktree_mutex` closes that race via POSIX-atomic `mkdir`. macOS
# does not ship GNU `flock` (`rules/bash-safety.md`: "No `timeout` Command —
# permanent"), so we use the most portable atomic primitive available.
#
# Opt-in: only callers passing `--lock-mutex` engage this. Default behaviour
# is byte-identical to pre-mutex callers. Future PR may flip the default
# after dogfooding (H-WORKTREE-MUTEX-1).

_WT_MUTEX_TIMEOUT_S="${GRIP_WORKTREE_MUTEX_TIMEOUT:-30}"
_WT_MUTEX_HELD=""

_acquire_worktree_mutex() {
    # Atomic POSIX mutex via mkdir. Returns 0 on acquire, 1 on timeout.
    local mutex_dir="$MAIN_GIT_DIR/worktree-mutex.d"
    local deadline=$(( $(date +%s) + _WT_MUTEX_TIMEOUT_S ))
    while ! mkdir "$mutex_dir" 2>/dev/null; do
        # Stale-holder detection: if PID inside is dead, claim the mutex.
        if [ -f "$mutex_dir/holder.txt" ]; then
            local held_pid
            held_pid="$(grep -E '^PID=' "$mutex_dir/holder.txt" 2>/dev/null | cut -d= -f2)"
            if [ -n "$held_pid" ] && ! kill -0 "$held_pid" 2>/dev/null; then
                echo "Removing stale worktree mutex (PID $held_pid dead)" >&2
                rm -f "$mutex_dir/holder.txt"
                rmdir "$mutex_dir" 2>/dev/null || true
                continue
            fi
        fi
        if [ "$(date +%s)" -gt "$deadline" ]; then
            echo "ERROR: worktree mutex acquire timeout after ${_WT_MUTEX_TIMEOUT_S}s" >&2
            return 1
        fi
        sleep 0.05
    done
    echo "PID=$$" > "$mutex_dir/holder.txt"
    echo "TS=$(date +%s)" >> "$mutex_dir/holder.txt"
    _WT_MUTEX_HELD="$mutex_dir"
    return 0
}

_release_worktree_mutex() {
    local mutex_dir="${_WT_MUTEX_HELD:-$MAIN_GIT_DIR/worktree-mutex.d}"
    rm -f "$mutex_dir/holder.txt" 2>/dev/null
    rmdir "$mutex_dir" 2>/dev/null || true
    _WT_MUTEX_HELD=""
}

# --- State drift prevention ---

_restore_state_files() {
    local worktree_path="$1"
    local dirs_to_restore="cache state data"
    for dir in $dirs_to_restore; do
        if [ -d "$worktree_path/$dir" ]; then
            git -C "$worktree_path" restore --source=HEAD -- "$dir/" 2>/dev/null || true
        fi
    done
}

# --- Path safety ---

_reject_path_inside_repo() {
    local worktree_path="$1"
    local real_wt real_repo
    real_wt="$(cd "$(dirname "$worktree_path")" 2>/dev/null && pwd)/$(basename "$worktree_path")"
    real_repo="$(cd "$REPO_ROOT" && pwd)"

    case "$real_wt" in
        "$real_repo"/*)
            echo "ERROR: Worktree path must be outside the repo (got: $worktree_path)" >&2
            return 1
            ;;
    esac
}

# --- Context linking helpers ---
# Claude Code encodes the working directory path to create project-specific
# storage under ~/.claude/projects/. Worktrees at /tmp/ get a different
# namespace, so a worktree session would otherwise see none of the repo's
# memory.
#
# This used to symlink the whole project DIRECTORY at the parent repo's, and
# that collided with how Claude Code stores large hook output. It caps inline
# `additionalContext` at 10,000 characters and persists anything longer under
# `projects/<cwd-slug>/tool-results/`, inlining a preview. It refuses to write
# through a symlinked project dir, so on every worktree the persist failed and
# the SessionStart rules injection was truncated:
#
#   [Hook additionalContext truncated at 10000 chars - persist-to-disk failed:
#    tool-results path refused: .../-Users-you--grip-worktrees-... is a link
#    or not a directory]
#
# The session then ran believing it held the extended rules when it held a
# fragment (GRIP #6370). So the project dir is now a REAL directory and only
# the shared SUBDIRECTORIES are symlinked into it. Claude Code can create
# tool-results/ inside it, and MEMORY.md still resolves to one file.

# Subdirectories shared with the parent repo's project dir.
#
#   memory/  MEMORY.md and the feedback/reference corpus. Readers resolve it
#            from the session's own cwd slug (lib/grip-session-greeting.sh),
#            so it has to be reachable from the worktree's slug.
#   grip/    the instance index (Gen N) and active-sessions locks, which
#            hooks/session-start.sh writes to `projects/$(pwd-encoded)/grip`.
#
# Everything else in a project dir is per-session and deliberately stays local
# to the worktree: `<uuid>.jsonl` transcripts, `<uuid>/` subagent directories
# and `tool-results/`. Nothing needs those shared, because the tools that read
# a transcript either glob every slug (bin/grip-session-roster resolves
# `*/<uuid>*.jsonl`, lib/session_archiver, lib/session_timeline) or read the
# session's OWN cwd slug (lib/context_miner).
_SHARED_PROJECT_SUBDIRS="memory grip"

_encode_path() {
    # The name Claude Code gives a path's project folder, from the shared
    # encoder in lib/path-encoding.sh: / . and space become -, and under Git
    # Bash a drive path /c/... becomes c--.... The copy that used to live here
    # kept spaces and gave -c-... on Windows, so it never found the folder
    # Claude Code uses there, and no worktree was ever linked (GRIP#8696).
    encode_project_path "$1"
}

_make_junction() {
    # Make $1 a Windows directory junction to $2 (no admin needed). cmd gets the
    # two paths as separate arguments, with Git Bash's path rewriting off for
    # this one command, as bin/grip-claude-dir-link does. Packed into one
    # quoted string, they reached cmd with \" escapes it rejects ("The filename,
    # directory name, or volume label syntax is incorrect"), so no junction was
    # ever made (GRIP#8696). Never make one inside .git: git deletes through a
    # junction when it removes a worktree (GRIP#8705).
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
        cmd /c mklink /J "$(cygpath -w "$1")" "$(cygpath -w "$2")" >/dev/null
}

_link_shared_subdir() {
    # Symlink ONE shared subdirectory into the worktree's project dir.
    # Silent no-op when there is nothing to share, or when something is
    # already in place -- which is what makes re-linking idempotent.
    local repo_subdir="$1"
    local wt_subdir="$2"

    if [ ! -d "$repo_subdir" ]; then
        return 0
    fi
    if [ -e "$wt_subdir" ] || [ -L "$wt_subdir" ]; then
        return 0
    fi

    if $_IS_WINDOWS; then
        # Windows: use directory junction (no admin required)
        _make_junction "$wt_subdir" "$repo_subdir" \
            || echo "  could not link $wt_subdir to $repo_subdir" >&2
    else
        ln -s "$repo_subdir" "$wt_subdir"
    fi
}

_worktree_has_live_session() {
    # Is any process currently sitting in this worktree?
    #
    # FAIL-SAFE: every path that cannot answer reports LIVE, so an unanswerable
    # probe skips the migration rather than risking it. Converting the project
    # dir under a running session would split its transcript across two files.
    #
    # Deliberately broader than "a claude process": MCP servers and shells
    # inherit the session's cwd, so any process there means a session is
    # probably still using it. Over-reporting costs a skipped migration;
    # under-reporting costs a torn transcript.
    local worktree_path="$1"

    # A directory that no longer exists can hold no running process.
    if [ ! -d "$worktree_path" ]; then
        return 1
    fi

    # No lsof (Windows Git Bash, stripped containers) -> cannot answer -> live.
    if ! command -v lsof >/dev/null 2>&1; then
        return 0
    fi

    local pids
    pids="$(lsof -t -a -d cwd -- "$worktree_path" 2>/dev/null || true)"
    [ -n "$pids" ]
}

_materialise_project_dir() {
    # Make $wt_project_dir a REAL directory sharing $shared_target's memory
    # and grip subdirectories. The one place that decides the layout, so the
    # create path and the migrate path can never drift apart.
    #
    # Returns non-zero when the project dir must not be touched -- a live
    # session is using the worktree, or a legacy link could not be replaced.
    # Idempotent: an already-materialised dir just gets its missing links.
    local wt_project_dir="$1"
    local worktree_path="$2"
    local shared_target="$3"

    if [ -L "$wt_project_dir" ]; then
        # Legacy whole-directory symlink. Replacing it under a running
        # session would split that session's transcript across two files,
        # so a live worktree is left exactly as it is.
        if _worktree_has_live_session "$worktree_path"; then
            echo "  project dir left as-is: a session is live in $worktree_path" >&2
            return 1
        fi
        # Removing the link never touches its target, so no memory and no
        # transcript is at risk here.
        if ! rm "$wt_project_dir" 2>/dev/null; then
            echo "  could not replace project context symlink $wt_project_dir" >&2
            return 1
        fi
    fi

    if ! mkdir -p "$wt_project_dir" 2>/dev/null; then
        echo "  could not create project dir $wt_project_dir" >&2
        return 1
    fi

    local subdir
    for subdir in $_SHARED_PROJECT_SUBDIRS; do
        _link_shared_subdir "$shared_target/$subdir" "$wt_project_dir/$subdir"
    done

    return 0
}

_link_project_context() {
    local worktree_path="$1"
    local projects_dir="$HOME/.claude/projects"

    # Resolve the real path (macOS: /tmp -> /private/tmp)
    local real_wt
    real_wt="$(cd "$worktree_path" 2>/dev/null && pwd -P || echo "$worktree_path")"

    local wt_encoded
    wt_encoded="$(_encode_path "$real_wt")"
    local repo_encoded
    repo_encoded="$(_encode_path "$(cd "$REPO_ROOT" && pwd -P)")"

    local wt_project_dir="$projects_dir/$wt_encoded"
    local repo_project_dir="$projects_dir/$repo_encoded"

    # Nothing to share from.
    if [ ! -d "$repo_project_dir" ]; then
        return 0
    fi

    # Never link a project dir onto itself (repo root passed as its own worktree).
    if [ "$wt_project_dir" = "$repo_project_dir" ]; then
        return 0
    fi

    # A failure already named its own reason on stderr.
    if _materialise_project_dir "$wt_project_dir" "$real_wt" "$repo_project_dir"; then
        echo "Linked worktree project context to parent repo"
    fi
}

_migrate_project_dirs() {
    # Convert every registered worktree still carrying the legacy
    # whole-directory symlink into a real directory (GRIP #6370), so existing
    # worktrees stop losing the tail of the SessionStart rules injection.
    #
    # Idempotent by construction: a worktree whose project dir is already a
    # real directory is not a symlink, so it returns before changing anything.
    # Safe by construction: a worktree with a live session is skipped.
    #
    # The sharing relationship is read from the link ITSELF rather than from
    # REPO_ROOT, so each worktree keeps pointing where it already pointed and
    # the sweep gives the same answer from any tree it is run in.
    local projects_dir="$HOME/.claude/projects"
    local migrated=0 skipped=0

    local wt real_wt wt_encoded wt_project_dir target
    while IFS= read -r wt; do
        [ -n "$wt" ] || continue

        real_wt="$(cd "$wt" 2>/dev/null && pwd -P || echo "$wt")"
        wt_encoded="$(_encode_path "$real_wt")"
        wt_project_dir="$projects_dir/$wt_encoded"

        [ -L "$wt_project_dir" ] || continue

        target="$(cd "$wt_project_dir" 2>/dev/null && pwd -P || echo "")"
        if [ -z "$target" ] || [ ! -d "$target" ]; then
            echo "  skip $wt_encoded (link target unreadable)"
            skipped=$((skipped + 1))
            continue
        fi

        # A skip already named its own reason on stderr.
        if _materialise_project_dir "$wt_project_dir" "$real_wt" "$target"; then
            echo "  migrated $wt_encoded"
            migrated=$((migrated + 1))
        else
            echo "  skip $wt_encoded"
            skipped=$((skipped + 1))
        fi
    done < <(git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null \
        | sed -n 's|^worktree ||p')

    echo "Project dirs: $migrated migrated, $skipped skipped"
}

_unlink_project_context() {
    local worktree_path="$1"
    local projects_dir="$HOME/.claude/projects"

    local real_wt
    real_wt="$(cd "$worktree_path" 2>/dev/null && pwd -P || echo "$worktree_path")"

    local wt_encoded
    wt_encoded="$(_encode_path "$real_wt")"
    local wt_project_dir="$projects_dir/$wt_encoded"

    # Legacy whole-directory link: removing it never touches the shared target.
    if [ -L "$wt_project_dir" ]; then
        rm "$wt_project_dir"
        echo "Unlinked worktree project context"
        return 0
    fi

    if [ ! -d "$wt_project_dir" ]; then
        return 0
    fi

    # Drop the shared subdirectory links. The -L guard is what keeps this
    # safe: a real directory is never matched, so nothing shared is removed.
    local subdir
    for subdir in $_SHARED_PROJECT_SUBDIRS; do
        if [ -L "$wt_project_dir/$subdir" ]; then
            rm "$wt_project_dir/$subdir"
        fi
    done

    # Then remove the project dir ONLY if that left it empty. A dir still
    # holding this worktree's session transcripts is kept: they are the only
    # copy. A Windows junction also lands here, and rmdir removes the junction
    # itself without touching what it points at -- the old behaviour.
    if rmdir "$wt_project_dir" 2>/dev/null; then
        echo "Unlinked worktree project context"
    else
        echo "Unlinked shared project context (session transcripts kept)"
    fi
}

# --- Core functions ---

grip_worktree_add() {
    local worktree_path=""
    local branch_name=""
    local session_id="${CLAUDE_SESSION_ID:-}"
    local repo_override=""
    local use_mutex=0
    local base_ref=""
    local owner_pid=""

    # Parse arguments: positional (path, branch) + optional --session-id, --repo, --lock-mutex, --base, --owner-pid
    while [ $# -gt 0 ]; do
        case "$1" in
            --owner-pid)
                owner_pid="${2:-}"
                shift 2
                ;;
            --owner-pid=*)
                owner_pid="${1#*=}"
                shift
                ;;
            --session-id)
                session_id="${2:-}"
                shift 2
                ;;
            --session-id=*)
                session_id="${1#*=}"
                shift
                ;;
            --repo)
                repo_override="${2:-}"
                shift 2
                ;;
            --repo=*)
                repo_override="${1#*=}"
                shift
                ;;
            --base)
                base_ref="${2:-}"
                shift 2
                ;;
            --base=*)
                base_ref="${1#*=}"
                shift
                ;;
            --lock-mutex)
                # Opt-in atomic worktree-add mutex. Closes the TOCTOU race
                # between _check_branch_lock and _set_branch_lock. See
                # `_acquire_worktree_mutex` doc-comment above.
                use_mutex=1
                shift
                ;;
            *)
                if [ -z "$worktree_path" ]; then
                    worktree_path="$1"
                elif [ -z "$branch_name" ]; then
                    branch_name="$1"
                fi
                shift
                ;;
        esac
    done

    if [ -z "$worktree_path" ]; then
        echo "Usage: grip-worktree add <path> [branch] [--session-id ID] [--repo PATH]" >&2
        return 1
    fi

    _resolve_repo_root "$repo_override" || return 1

    # Default branch name from path basename
    if [ -z "$branch_name" ]; then
        branch_name="$(basename "$worktree_path")"
    fi

    # Append session fragment to branch name when --session-id is provided
    if [ -n "$session_id" ]; then
        local fragment="${session_id:0:4}"
        branch_name="${branch_name}-s${fragment}"
    fi

    # Guard: reject paths inside the repo
    _reject_path_inside_repo "$worktree_path"

    # Opt-in mutex: acquire BEFORE the porcelain check + branch-lock check so
    # both the read and the subsequent write are atomic. Released on every
    # return path below (success + every failure mode).
    if [ "$use_mutex" -eq 1 ]; then
        _acquire_worktree_mutex || return 1
        trap '_release_worktree_mutex' RETURN
    fi

    # Guard: check if branch is already checked out by another worktree
    local porcelain
    porcelain="$(git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null)"
    if echo "$porcelain" | grep -q "branch refs/heads/$branch_name$"; then
        echo "ERROR: Branch '$branch_name' is already checked out in another worktree" >&2
        echo "$porcelain" | grep -B1 "branch refs/heads/$branch_name$" | head -1 >&2
        return 1
    fi

    # Guard: branch lock
    _check_branch_lock "$branch_name"

    # Step 1: Create worktree WITHOUT checkout (avoids smudge filter)
    # New-branch start-point precedence: existing local branch (checked out
    # as-is) > explicit --base > origin/<branch> DWIM (a remote branch with
    # this exact name — mirrors git's own worktree-add DWIM, so a remote
    # branch is never silently shadowed by a HEAD-based local one) > HEAD.
    # The ladder resolves a start_point FIRST so an unresolvable --base
    # ALWAYS warns, even when the DWIM rung then catches the branch name
    # (AGORA #3939 round 2, item 2 — the warning must never be swallowed).
    echo "Creating worktree at $worktree_path (branch: $branch_name)..."
    local start_point=""
    if [ -n "$base_ref" ]; then
        if git -C "$REPO_ROOT" rev-parse --verify --quiet "${base_ref}^{commit}" >/dev/null 2>&1; then
            start_point="$base_ref"
        else
            echo "WARN: --base '$base_ref' does not resolve to a commit" >&2
        fi
    fi
    if [ -z "$start_point" ] && git -C "$REPO_ROOT" rev-parse --verify --quiet "origin/${branch_name}^{commit}" >/dev/null 2>&1; then
        echo "Note: no local branch '$branch_name' but origin/$branch_name exists; basing on the remote branch" >&2
        start_point="origin/$branch_name"
    fi
    if git -C "$REPO_ROOT" rev-parse --verify "$branch_name" >/dev/null 2>&1; then
        if [ -n "$base_ref" ]; then
            echo "Note: branch '$branch_name' already exists; --base '$base_ref' ignored" >&2
        fi
        git -C "$REPO_ROOT" worktree add --no-checkout "$worktree_path" "$branch_name"
    elif [ -n "$start_point" ]; then
        git -C "$REPO_ROOT" worktree add --no-checkout -b "$branch_name" "$worktree_path" "$start_point"
    else
        if [ -n "$base_ref" ]; then
            echo "WARN: branching '$branch_name' from HEAD (--base unresolvable, no origin/$branch_name)" >&2
        else
            # No --base at all: the SILENT contamination-at-birth path.
            # The shared tree's HEAD routinely carries local-only commits
            # (measured 2026-09-14: 37 commits / 34 files / 4,600 insertions
            # ahead of origin/main, settings.json among them), so a PR opened
            # from such a worktree sweeps every one of them in.
            # rules/worktree-must-track-main.md requires birth from a freshly
            # fetched origin/main. This WARNS rather than changing the default,
            # so no existing caller's behaviour shifts underneath it.
            echo "WARN: branching '$branch_name' from the shared tree's HEAD (no --base given)." >&2
            echo "      That HEAD may carry local-only commits, so a PR from this worktree can" >&2
            echo "      sweep in unrelated changes. Pass --base origin/main for a clean base." >&2
        fi
        git -C "$REPO_ROOT" worktree add --no-checkout -b "$branch_name" "$worktree_path"
    fi

    # Step 2: Resolve worktree git dir and place the git-crypt keys
    local worktree_git_dir
    worktree_git_dir="$(_worktree_git_dir "$worktree_path")"

    if [ -d "$MAIN_GIT_DIR/git-crypt" ] && [ ! -L "$worktree_git_dir/git-crypt" ] && [ ! -d "$worktree_git_dir/git-crypt" ]; then
        if $_IS_WINDOWS; then
            # Windows copies the keys and never links them. Git for Windows
            # deletes through a directory junction when it removes a worktree's
            # git folder (git worktree remove, prune, and the prune inside gc),
            # so a junction here emptied the main repository's .git/git-crypt
            # (GRIP#8705). A copy shares nothing with the original, so removing
            # the worktree deletes only the copy. Limits: a key changed later in
            # the main repository does not reach an existing worktree, and a
            # `git-crypt lock` in the main repository leaves the copies, so
            # remove worktrees before locking. A `git-crypt lock` inside a
            # worktree deletes only its copy, but it also clears the filter
            # settings the main checkout shares. That happens on macOS and Linux
            # too.
            if cp -R "$MAIN_GIT_DIR/git-crypt" "$worktree_git_dir/git-crypt"; then
                echo "Copied git-crypt keys into worktree"
            else
                rm -rf "$worktree_git_dir/git-crypt"
                echo "WARN: could not copy git-crypt keys into the worktree (GRIP#8705)" >&2
            fi
        else
            ln -s "$MAIN_GIT_DIR/git-crypt" "$worktree_git_dir/git-crypt"
            echo "Linked git-crypt keys into worktree"
        fi
    fi

    # Step 3: Checkout with error capture and rollback on failure
    local checkout_output
    if ! checkout_output="$(git -C "$worktree_path" checkout 2>&1)"; then
        echo "ERROR: Checkout failed in worktree:" >&2
        echo "$checkout_output" >&2
        echo "Rolling back worktree..." >&2
        git -C "$REPO_ROOT" worktree remove --force "$worktree_path" 2>/dev/null || true
        return 1
    fi

    # Prevent state drift (cache/, state/, data/ reset to branch HEAD)
    _restore_state_files "$worktree_path"

    # Link project context (MEMORY.md, instance state shared with parent)
    _link_project_context "$worktree_path"

    # Set branch lock, and record it in the worktree's git folder (GRIP#8695)
    _set_branch_lock "$branch_name" "$worktree_path" "$owner_pid"
    _record_branch_lock "$worktree_git_dir" "$branch_name"

    echo "Worktree ready at $worktree_path (branch: $branch_name)"
}

grip_worktree_remove() {
    local worktree_path=""
    local repo_override=""
    local force=false

    while [ $# -gt 0 ]; do
        case "$1" in
            --force|-f) force=true; shift ;;
            --repo)    repo_override="${2:-}"; shift 2 ;;
            --repo=*)  repo_override="${1#*=}"; shift ;;
            *)
                if [ -z "$worktree_path" ]; then worktree_path="$1"; fi
                shift
                ;;
        esac
    done

    if [ -z "$worktree_path" ]; then
        echo "Usage: grip-worktree remove <path> [--force] [--repo PATH]" >&2
        return 1
    fi

    _resolve_repo_root "$repo_override" || return 1

    # Refuse to destroy uncommitted work unless --force is passed explicitly.
    #
    # `git worktree remove --force` overrides git's own refusal to remove a
    # dirty worktree. Passing it unconditionally (as this function used to)
    # silently discarded uncommitted modifications to TRACKED files. Committed
    # work survives on the branch ref, so what is lost is precisely the
    # working-tree edits that have no other copy anywhere.
    #
    # Claude Code gained this safety property for the worktrees IT manages
    # (2.1.143 dropped the rm -rf fallback; 2.1.208 made Ctrl-X never destroy
    # unpushed work). GRIP's worktrees are created and removed here, not by
    # the harness, so the property has to be implemented rather than inherited.
    #
    # Scoped to TRACKED modifications deliberately. Worktrees carry constant
    # gitignored churn (state/, data/ and cache/ are reset by
    # _restore_state_files), so refusing on untracked files would fire on
    # essentially every removal and train callers to pass --force reflexively,
    # which would defeat the guard entirely. Untracked and gitignored files
    # are therefore still removed — this is narrower than the harness
    # behaviour it mirrors, and deliberately so.
    #
    # Checked before any mutation (project-context unlink, lock cleanup) so a
    # refusal leaves the worktree completely intact.
    if ! $force; then
        local dirty=""
        dirty="$(git -C "$worktree_path" status --porcelain --untracked-files=no 2>/dev/null || true)"
        if [ -n "$dirty" ]; then
            local dirty_count
            dirty_count="$(printf '%s\n' "$dirty" | wc -l | tr -d ' ')"
            echo "ERROR: refusing to remove worktree with $dirty_count uncommitted change(s) to tracked files" >&2
            echo "  $worktree_path" >&2
            printf '%s\n' "$dirty" | sed 's|^|    |' >&2
            echo "" >&2
            echo "Commit or stash the changes first, or discard them explicitly:" >&2
            echo "  grip-worktree remove $worktree_path --force" >&2
            return 1
        fi
    fi

    # The lock this worktree took at add (GRIP#8695), read before git deletes
    # the worktree's git folder, where the record lives.
    local lock_record=""
    lock_record="$(_read_lock_record "$worktree_path")"

    # When the worktree has no record, because it was made before GRIP#8695 or
    # its folder is gone, detect the branch name for lock cleanup, from the
    # worktree itself. Matching its folder in `git worktree list` fails on
    # Windows, where `pwd -P` prints /c/... and git prints C:/... (GRIP#8691),
    # so that match is only the fallback, for a worktree whose folder is gone.
    local branch_name=""
    if [ -z "$lock_record" ]; then
        branch_name="$(git -C "$worktree_path" symbolic-ref --short -q HEAD 2>/dev/null || true)"
        if [ -z "$branch_name" ]; then
            # Resolve real path (macOS: /tmp -> /private/tmp) for porcelain matching
            local resolved_path
            resolved_path="$(cd "$worktree_path" 2>/dev/null && pwd -P || echo "$worktree_path")"
            local porcelain
            porcelain="$(git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null)"
            branch_name="$(echo "$porcelain" | grep -A2 "worktree $resolved_path" | grep "^branch " | sed 's|branch refs/heads/||' || true)"
        fi
    fi

    # Unlink project context before removal
    _unlink_project_context "$worktree_path"

    git -C "$REPO_ROOT" worktree remove --force "$worktree_path"

    # Clean up the lock this worktree took, or else the branch's lock.
    if [ -n "$lock_record" ]; then
        _clear_recorded_lock "$lock_record"
    elif [ -n "$branch_name" ]; then
        _clear_branch_lock "$branch_name"
    fi

    echo "Removed worktree at $worktree_path"
}

grip_worktree_list() {
    local repo_override=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --repo)    repo_override="${2:-}"; shift 2 ;;
            --repo=*)  repo_override="${1#*=}"; shift ;;
            *)         shift ;;
        esac
    done
    _resolve_repo_root "$repo_override" || return 1
    git -C "$REPO_ROOT" worktree list
}

# --- Partial teardown helpers (multica W4a absorb) ---

# Rescue logs/ and output/ subdirectories from a worktree to a preserved
# sibling directory. Called by grip_worktree_teardown before actual removal.
# Returns 0 on success (even if nothing to rescue), 1 on error.
_rescue_preserved_dirs() {
    local worktree_path="$1"
    local rescue_root="$2"
    local preserved_any=0

    mkdir -p "$rescue_root" || { echo "ERROR: cannot create $rescue_root" >&2; return 1; }

    for sub in logs output; do
        local src="$worktree_path/$sub"
        if [ -d "$src" ]; then
            # Use cp -R to preserve contents even though src is about to be removed
            if cp -R "$src" "$rescue_root/$sub" 2>/dev/null; then
                preserved_any=1
                echo "Preserved $sub/ to $rescue_root/$sub"
            else
                echo "WARN: failed to preserve $sub/ from $worktree_path" >&2
            fi
        fi
    done

    return 0
}

# grip_worktree_teardown: drop workdir, rescue logs/ and output/ first.
# Default rescue target: sibling dir named preserved-{basename}-{epoch}.
grip_worktree_teardown() {
    local worktree_path=""
    local rescue_to=""
    local preserve_logs=false
    local repo_override=""

    while [ $# -gt 0 ]; do
        case "$1" in
            --preserve-logs) preserve_logs=true; shift ;;
            --rescue-to) rescue_to="${2:-}"; shift 2 ;;
            --rescue-to=*) rescue_to="${1#*=}"; shift ;;
            --repo) repo_override="${2:-}"; shift 2 ;;
            --repo=*) repo_override="${1#*=}"; shift ;;
            *) if [ -z "$worktree_path" ]; then worktree_path="$1"; fi; shift ;;
        esac
    done

    if [ -z "$worktree_path" ]; then
        echo "Usage: grip-worktree teardown --preserve-logs <path> [--rescue-to <dir>] [--repo PATH]" >&2
        return 1
    fi

    _resolve_repo_root "$repo_override" || return 1
    if [ ! -d "$worktree_path" ]; then
        echo "ERROR: worktree path does not exist: $worktree_path" >&2
        return 1
    fi

    # Default rescue target: preserved-{basename}-{epoch} alongside the worktree
    if $preserve_logs && [ -z "$rescue_to" ]; then
        local parent base epoch
        parent="$(dirname "$worktree_path")"
        base="$(basename "$worktree_path")"
        epoch="$(date +%s)"
        rescue_to="$parent/preserved-${base}-${epoch}"
    fi

    if $preserve_logs; then
        _rescue_preserved_dirs "$worktree_path" "$rescue_to" || return 1
        echo "$rescue_to" > "$worktree_path/.preserved-to" 2>/dev/null || true
    fi

    # Reuse the regular removal path so branch locks and project context
    # are cleaned up consistently. --force is explicit here: teardown's
    # contract is to drop the workdir, having already rescued logs/ and
    # output/ above, so the discard is the intended behaviour rather than an
    # accident of the callee's default.
    grip_worktree_remove "$worktree_path" --force

    if $preserve_logs; then
        echo "Teardown complete. Preserved artefacts: $rescue_to"
        echo "$rescue_to"
    fi
}

# --- Default-branch cascade (multica W4a absorb) ---
#
# Resolve the default branch of a repo by cascading through:
#   1. git symbolic-ref refs/remotes/origin/HEAD (authoritative if set)
#   2. origin/main (modern convention)
#   3. origin/master (legacy convention)
#   4. HEAD (last-resort fallback for detached or bare repos)
#
# Prints the resolved short branch name to stdout. Never fails — always
# returns something (even if just "HEAD"). Intended for cross-repo
# compatibility when a caller needs the default branch of a foreign repo.
grip_worktree_default_branch() {
    local repo="${1:-$REPO_ROOT}"
    local ref=""

    # Tier 1: authoritative symbolic ref
    ref="$(git -C "$repo" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || true)"
    if [ -n "$ref" ]; then
        # Strip the "origin/" prefix to return just the branch name
        echo "${ref#origin/}"
        return 0
    fi

    # Tier 2: origin/main
    if git -C "$repo" show-ref --verify --quiet refs/remotes/origin/main; then
        echo "main"
        return 0
    fi

    # Tier 3: origin/master
    if git -C "$repo" show-ref --verify --quiet refs/remotes/origin/master; then
        echo "master"
        return 0
    fi

    # Tier 4: local main/master (no remote case)
    if git -C "$repo" show-ref --verify --quiet refs/heads/main; then
        echo "main"
        return 0
    fi
    if git -C "$repo" show-ref --verify --quiet refs/heads/master; then
        echo "master"
        return 0
    fi

    # Tier 5: HEAD fallback (detached, bare, or no commits yet)
    echo "HEAD"
    return 0
}

# CLI entry point (when run as script, not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        add)            shift; grip_worktree_add "$@" ;;
        remove)         shift; grip_worktree_remove "$@" ;;
        teardown)       shift; grip_worktree_teardown "$@" ;;
        list)           shift; grip_worktree_list "$@" ;;
        default-branch) shift; grip_worktree_default_branch "$@" ;;
        migrate-project-dirs) shift; _migrate_project_dirs "$@" ;;
        *)              echo "Usage: grip-worktree {add|remove|teardown|list|default-branch|migrate-project-dirs} [args...]" >&2; exit 1 ;;
    esac
fi
