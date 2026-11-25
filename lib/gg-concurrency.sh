#!/usr/bin/env bash
# gg-concurrency.sh — concurrent-session detection + worktree isolation for
# gg++ launches.
#
# Problem (recurring since Gen 348, re-confirmed Gen 509 + Rule 13): N
# concurrent Claude Code sessions launched in the SAME git working tree
# (typically ~/.grip on main) share one .git/HEAD, one .git/index, and one
# set of files. Any session switching branches flips the tree under every
# other session; concurrent commits fight over index.lock. The only
# structural fix is one-working-tree-per-session: git worktrees give each
# session its own HEAD while sharing the object store.
#
# This library is the launcher-side pre-flight:
#   DECIDE  — pure, deterministic, unit-testable (gg_should_isolate)
#   ISOLATE — delegates to lib/grip-worktree.sh, the git-crypt-safe creator
#             (native `git worktree add` breaks on git-crypt repos because
#             the smudge filter cannot find the keys — Rule 13,
#             rules/efficiency-rules.md)
#   REAP    — post-session cleanup that is fail-safe toward keeping work:
#             a worktree is removed ONLY when it has no uncommitted changes
#             and no commits beyond its recorded launch base.
#
# Sourced by bin/gg++. No `set -e` here: the caller owns shell options, and
# tests exercise failure paths. Every function returns 0 unless stated.
#
# Tests: tests/test_gg_concurrency.py

# GG_HARNESS_BINARIES — process names that count as a live GRIP session.
#
# This MUST equal the set of `binary` values in HARNESS_REGISTRY
# (lib/grip_harness_picker.py). It is duplicated here rather than derived at
# runtime because this function runs on the interactive gg++ launch path, and
# spawning a Python interpreter to read a short list would add startup latency
# to every launch.
#
# The duplication is made safe by a test, not by discipline:
# tests/test_gg_concurrency.py asserts this list equals the registry's binaries,
# so adding a harness without updating this line fails CI. Until 2026-10-03 no
# CI job ran that test, and this line stayed at six names while the registry
# grew to twenty, so fourteen harnesses' sessions went uncounted (GRIP#7233).
GG_HARNESS_BINARIES="agy|aider|claude|cline|cn|codewhale|codex|crush|dsh|gemini|goose|hermes|interpreter|kilo|kimi|kiro-cli|opencode|openhands|pi|qwen"

# gg_count_claude_sessions — number of live interactive harness processes.
#
# Mirrors lib/gates/session_isolation.py::count_claude_sessions: `pgrep -x`
# exact-match counts the harness CLIs themselves, excluding Claude Desktop,
# MCP servers, and hook subprocesses.
#
# Counts EVERY harness gg++ can launch, not just claude. `pgrep -x claude` is an
# exact whole-name match, so a codex or opencode session was invisible to it and
# could not trigger worktree isolation — the shared-tree contamination that
# isolation exists to prevent was therefore reachable precisely when a second
# session used a different harness. Measured on the machine this was written:
# `pgrep -x claude` returned 6 while a codex session was also live, so the true
# count was 7. `pgrep -x` takes an ERE, and `-x` anchors each alternative to the
# whole process name, so the alternation stays exact-match (verified equal to a
# per-binary loop over the same set).
#
# The name is kept for compatibility with existing callers and tests.
#
# GG_FAKE_CLAUDE_COUNT is a test seam (deterministic tests cannot control
# how many real sessions run on the test machine). Production callers never
# set it.
gg_count_claude_sessions() {
    if [[ -n "${GG_FAKE_CLAUDE_COUNT:-}" ]]; then
        echo "${GG_FAKE_CLAUDE_COUNT}"
        return 0
    fi
    case "${OSTYPE:-}" in
        msys*|cygwin*) gg_count_windows_sessions; return 0 ;;
    esac
    local n
    n="$(pgrep -x "${GG_HARNESS_BINARIES}" 2>/dev/null | wc -l | tr -d '[:space:]')" || n=0
    echo "${n:-0}"
}

# gg_count_windows_sessions — the same count on Windows, where Git Bash has no
# pgrep (GRIP#8835).
#
# Asks lib/windows_processes.py, the psutil scan every Windows session counter
# shares. It finds <harness>.exe console processes and leaves out the Claude
# desktop app's GUI ones, which a name match alone would count. This spawns
# Python, which the pgrep path avoids for launch latency; Windows has no pgrep
# to avoid it with, and the session wizard that runs next spawns Python anyway.
# The interpreter is the GRIP venv's, resolved in a subshell so nothing that
# venv-utils.sh exports reaches the launch; GG_PYTHON overrides it for tests.
# Python ends its line with CRLF on Windows. Git Bash drops a carriage return
# that comes before a newline but keeps a lone one, and a POSIX shell keeps
# both, so every carriage return is stripped.
# Prints 0 when no count can be read, as a missing pgrep did: never isolate on
# a process table that cannot be observed.
gg_count_windows_sessions() {
    local lib py n
    local -a names
    lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    py="${GG_PYTHON:-}"
    if [[ -z "$py" ]]; then
        # shellcheck source=../scripts/lib/venv-utils.sh
        py="$(source "$lib/../scripts/lib/venv-utils.sh" && resolve_venv_python "$lib/../venv")" || py=""
    fi
    IFS='|' read -r -a names <<< "$GG_HARNESS_BINARIES"
    n=""
    if [[ -n "$py" ]]; then
        n="$("$py" "$lib/windows_processes.py" count "${names[@]}" 2>/dev/null)" || n=""
    fi
    n="${n//$'\r'/}"
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    echo "$n"
}

# gg_tree_state <dir> — classify the launch directory. Prints one of:
#   shared-main : inside a git repo's MAIN working tree (contamination risk —
#                 this is the tree every non-isolated session shares)
#   worktree    : inside a linked worktree (already has its own HEAD)
#   none        : not a git repo (nothing to contaminate)
gg_tree_state() {
    local dir="${1:-.}"
    local git_dir common_dir
    git_dir="$(git -C "$dir" rev-parse --git-dir 2>/dev/null)" || { echo "none"; return 0; }
    common_dir="$(git -C "$dir" rev-parse --git-common-dir 2>/dev/null)" || { echo "none"; return 0; }
    # Main tree: git-dir and git-common-dir are the same path. Linked
    # worktree: git-dir is .git/worktrees/<name> under the common dir.
    if [[ "$git_dir" != "$common_dir" ]]; then
        echo "worktree"
    else
        echo "shared-main"
    fi
}

# gg_should_isolate <live_claude_count> <tree_state> <mode> — pure decision.
# Prints "isolate" or "direct". Modes (GG_ISOLATE env / gg++ flags):
#   off  — direct always (operator opt-out wins; legacy shared-tree launch)
#   on   — isolate whenever there is a repo to isolate (tree_state != none)
#   auto — isolate iff tree_state == shared-main AND live_claude_count >= 1
#
# Rationale for auto: alone-in-main keeps today's single-session behaviour
# (Rule 13 relaxed default); already-in-worktree is already isolated;
# outside a repo there is no shared HEAD to protect.
gg_should_isolate() {
    local count="${1:-0}" tree="${2:-none}" mode="${3:-auto}"
    case "$mode" in
        off)
            echo "direct"; return 0 ;;
        on)
            if [[ "$tree" == "none" ]]; then echo "direct"; else echo "isolate"; fi
            return 0 ;;
    esac
    if [[ "$tree" == "shared-main" && "$count" -ge 1 ]]; then
        echo "isolate"
    else
        echo "direct"
    fi
}

# gg_worktree_create <repo_root> [worktree_root] — create a session worktree.
#
# Echoes the worktree path on stdout (everything else goes to stderr).
# Branch: sess/gg-<UTC timestamp>-<pid> — unique per launch, greppable,
# clearly disposable. Location: ${GG_WORKTREE_ROOT:-$HOME/.gg-worktrees}
# (outside every repo, survives reboot unlike /tmp, easy to audit).
#
# Delegates to lib/grip-worktree.sh (git-crypt key symlink + branch lock +
# --lock-mutex TOCTOU-safe add + project-context symlink so MEMORY.md and
# session continuity are SHARED with the parent repo). Records the launch
# base SHA in the worktree's PRIVATE git dir (never dirties the tree) so
# gg_worktree_reap can tell "no new work" from "has commits".
gg_worktree_create() {
    local repo="$1"
    local root="${2:-${GG_WORKTREE_ROOT:-$HOME/.gg-worktrees}}"
    local grip_dir="${GRIP_DIR:-$HOME/.grip}"
    local stamp name branch wt
    stamp="$(date -u +%Y%m%d-%H%M%S)"
    name="gg-${stamp}-$$"
    branch="sess/${name}"
    wt="${root}/${name}"
    mkdir -p "$root"

    # Branch the session worktree off a FRESHLY-FETCHED origin/<default-branch>,
    # never the shared main tree's HEAD. The shared tree routinely lags origin
    # (concurrent sessions push via temp-index plumbing without advancing the
    # main checkout), so a HEAD-based session branch is born stale. Both steps are FAIL-OPEN so a launch never
    # blocks, and the fetch is ref-only — it never checks out / pulls the shared
    # tree, which a concurrent session may hold on a feature branch.
    #   1. fetch origin/<def>            (updates the remote-tracking ref only)
    #   2. --base origin/<def>           (grip-worktree.sh's start_point ladder
    #      branches the new session branch from that fresh tip; it falls back to
    #      HEAD with a warning if origin/<def> is unresolvable — offline/no-remote)
    local -a add_args=("$wt" "$branch" --repo "$repo" --lock-mutex)
    local def_branch
    def_branch="$(bash "${grip_dir}/lib/grip-worktree.sh" default-branch "$repo" 2>/dev/null)" || def_branch=""
    if [[ -n "$def_branch" && "$def_branch" != "HEAD" ]]; then
        git -C "$repo" fetch --quiet origin "$def_branch" 2>/dev/null \
            || echo "[gg++] WARN: could not fetch origin/$def_branch (offline?)" >&2
        if git -C "$repo" rev-parse --verify --quiet "origin/${def_branch}^{commit}" >/dev/null 2>&1; then
            add_args+=(--base "origin/$def_branch")
        else
            # No usable origin/<def> ref (offline with no last-known ref, or no
            # remote): --base would be unresolvable, so grip-worktree.sh branches
            # from HEAD. Say so -- the fallback is fail-open, never silent.
            echo "[gg++] WARN: origin/$def_branch unresolved; branching session worktree from HEAD" >&2
        fi
    fi

    # --owner-pid: without it the branch lock records grip-worktree.sh's own
    # pid, which exits the moment the worktree is created, leaving the reaper's
    # first liveness guard permanently satisfied. On this path gg++ ($$) stays
    # alive for the whole session -- it waits on the launch in a subshell
    # rather than exec'ing -- so it is the correct long-lived owner.
    bash "${grip_dir}/lib/grip-worktree.sh" add "${add_args[@]}" \
        --owner-pid "$$" 1>&2 || return 1
    git -C "$wt" rev-parse HEAD > "$(git -C "$wt" rev-parse --absolute-git-dir)/gg-base" 2>/dev/null || true
    echo "$wt"
}

# gg_worktree_reap <worktree_path> <repo_root> — post-session cleanup.
#
# Prints "reaped" or "kept". FAIL-SAFE: any uncommitted change, any commit
# beyond the recorded launch base, or any doubt (missing base marker, git
# error) means KEEP — a kept worktree costs disk; a wrongly-removed one
# costs work. Kept worktrees print resume/discard instructions on stderr;
# the worktree-reaper agent handles long-term stale accumulation.
gg_worktree_reap() {
    local wt="$1" repo="$2"
    local grip_dir="${GRIP_DIR:-$HOME/.grip}"
    [[ -d "$wt" ]] || { echo "reaped"; return 0; }

    local branch dirty base ahead
    branch="$(git -C "$wt" symbolic-ref --short HEAD 2>/dev/null)" || branch=""
    dirty="$(git -C "$wt" status --porcelain 2>/dev/null)" || dirty="unknown"
    base="$(cat "$(git -C "$wt" rev-parse --absolute-git-dir 2>/dev/null)/gg-base" 2>/dev/null)" || base=""
    if [[ -n "$base" ]]; then
        ahead="$(git -C "$wt" rev-list --count "${base}..HEAD" 2>/dev/null)" || ahead=999
    else
        ahead=999  # no base marker → not provably work-free → keep
    fi

    if [[ -z "$dirty" && "${ahead:-999}" -eq 0 ]]; then
        bash "${grip_dir}/lib/grip-worktree.sh" remove "$wt" --repo "$repo" 1>&2 || return 1
        if [[ -n "$branch" ]]; then
            git -C "$repo" branch -D "$branch" >/dev/null 2>&1 || true
        fi
        echo "reaped"
    else
        {
            echo "[gg++] session worktree KEPT — uncommitted changes: $([[ -n "$dirty" ]] && echo yes || echo no) · commits beyond launch base: ${ahead}"
            echo "[gg++]   resume:  cd $wt && gg++        (already isolated — will not re-nest)"
            echo "[gg++]   discard: bash ${grip_dir}/lib/grip-worktree.sh remove $wt --repo $repo"
        } 1>&2
        echo "kept"
    fi
}
