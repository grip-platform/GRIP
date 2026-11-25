#!/usr/bin/env bash
# GRIP Shell Functions - source this in your shell config
# These functions require being sourced (not executed) to affect the parent shell
#
# The install.sh script adds 'source' and 'alias gg++="gogrip"' to your
# shell config (.zshrc/.bashrc). The bin/gg++ script is the fallback for
# non-interactive use when shell-functions.sh hasn't been sourced.

# gogrip: Navigate to GRIP home and start a fresh session, in any harness.
# Aliased as gg++ by install.sh for quick access.
#
#   gg++                      Claude Code, 12000 tokens of context
#   gg++ 8000                 Claude Code, 8000 tokens
#   gg++ codex                Codex, wired to HAL, in an isolated worktree
#   gg++ opencode 8000        harness and budget, in either order
#   gg++ codex --kimi         Codex on the best Kimi model
#   gg++ opencode --qwen max  ... on the qwen 'max' tier
#   gg++ claude --kimi        Claude Code on Kimi K3 via the HAL gateway
#                             (GRIP_INFINITE=1); add --direct for the vendor
#                             endpoint instead (no HAL witness, no cost row)
#   gg++ --list               what can be launched on this machine
#
# This stays a FUNCTION because it cds the parent shell. Argument
# classification lives in lib/grip_launch_args.py, where it can be tested.
# Behavioural coverage, under bash AND zsh: tests/test_gg_harness_arg.py.
gogrip() {
    local CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

    if [[ ! -d "$CLAUDE_DIR" ]]; then
        echo "Error: GRIP not installed at $CLAUDE_DIR" >&2
        echo "Install: git clone https://github.com/grip-platform/GRIP.git ~/.claude" >&2
        return 1
    fi

    # Backward-compatibility floor. No arguments, or a single all-digits
    # budget, runs the historical command verbatim and RETURNS before any new
    # code path is reached. The guarantee is structural, not a promise: a
    # missing python3, a broken picker or an unparseable providers.yaml cannot
    # regress the launch every existing habit depends on.
    if [[ $# -eq 0 ]]; then
        cd "$CLAUDE_DIR" && grip fresh latest 12000
        return
    fi
    case "$#:$1" in
        1:*[!0-9]*) : ;;
        1:) : ;;
        1:*) cd "$CLAUDE_DIR" && grip fresh latest "$1"; return ;;
    esac

    local _plan _rc _kw _h _t _m _e _d
    _plan="$(python3 "$CLAUDE_DIR/lib/grip_launch_args.py" "$@")"
    _rc=$?
    # The classifier already explained itself on stderr. Nothing has happened
    # yet: no cd, no worktree, no config write, no grip invocation.
    [[ $_rc -ne 0 ]] && return $_rc

    case "$_plan" in
        LAUNCH\ *) : ;;
        *) printf '%s\n' "$_plan"; return 0 ;;   # --list / --help
    esac

    # LAUNCH <harness|-> <tokens> <model|-> <effort|-> <direct|->
    read -r _kw _h _t _m _e _d <<EOF
$_plan
EOF

    cd "$CLAUDE_DIR" || return 1

    # `fresh` MUST stay the first argument. bin/grip gates worktree isolation
    # on the RAW argv[1] against an allowlist that does not contain --harness,
    # so `grip --harness codex fresh ...` would skip isolation entirely and run
    # a foreign harness on the shared git-crypt main tree.
    local _args=(fresh latest "$_t")
    if [[ "$_h" != "-" ]]; then
        _args=(fresh --harness "$_h" latest "$_t")
    fi

    # Environment handed to grip, mirroring bin/gg++'s _LAUNCH_ENV so the two
    # surfaces cannot disagree. "-" means the operator did not ask, and the
    # variable is then not exported at all (an empty GRIP_HARNESS_EFFORT would
    # be indistinguishable from "no effort set" downstream). Scoped to this one
    # command via env(1), never exported into the parent shell, so a model
    # chosen once cannot persist into the next launch. This used to export the
    # model only beside --harness and the effort never (Defect A): a bare
    # `gg++ --kimi` lost its model and `gg++ codex --kimi xhigh` its effort.
    local _env=()
    if [[ "$_m" != "-" ]]; then _env+=("GRIP_HARNESS_MODEL=$_m"); fi
    if [[ "$_e" != "-" ]]; then _env+=("GRIP_HARNESS_EFFORT=$_e"); fi
    if [[ "$_d" == "direct" ]]; then _env+=("GRIP_HARNESS_DIRECT=1"); fi
    if [[ ${#_env[@]} -gt 0 ]]; then
        env "${_env[@]}" grip "${_args[@]}"
    else
        grip "${_args[@]}"
    fi
}

# py: run GRIP Python under the GRIP venv, never the system interpreter.
#
# V>> locked this on 2026-09-13, and the wording matters: `py` runs the VENV
# python. Aliasing it to plain `python3` would have made the trap one character
# shorter to fall into -- `python3 lib/grip_save.py` does not fail, it silently
# signs the decision record with a forgeable demo seal, because `python3` here
# is Homebrew's interpreter and carries no `cryptography`. So the short, natural
# thing to type is the safe one, and system python stays reachable as the
# unchanged `python3`.
#
# A FUNCTION HERE RATHER THAN A NEW LINE IN THE RC FILES, deliberately. Both rc
# writers (scripts/install.sh and `grip install-aliases`) already emit
# `source "$HOME/.claude/lib/shell-functions.sh"`, so a function defined here
# reaches every installed machine on its next shell without any rc being
# rewritten. That matters because those writers guard on marker presence only
# and cannot correct a line they have already written -- an alias shipped
# through them would be frozen on every existing install. It also keeps the two
# emitters byte-identical, which tests/test_alias_emitters_agree.py requires.
#
# All argument handling, the venv resolution, the refusal to fall back to system
# python and the cannot-dual-sign warning live in bin/grip-py. This is a thin
# forwarder; it reimplements none of it.
#
# Behavioural coverage, bash AND zsh: tests/test_py_shell_function.py.
py() {
    local CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
    local shim="$CLAUDE_DIR/bin/grip-py"

    # Absolute path first, PATH second: a GRIP that is installed but not yet on
    # PATH is the common state during setup, and that is exactly when someone
    # reaches for `py`.
    if [[ -x "$shim" ]]; then
        "$shim" "$@"
    elif command -v grip-py >/dev/null 2>&1; then
        grip-py "$@"
    else
        echo "[py] grip-py not found at $shim, and not on PATH." >&2
        echo "[py] GRIP may not be installed at $CLAUDE_DIR. Use python3 directly if you meant the system interpreter." >&2
        return 127
    fi
}

# `show` -- open a file or folder, or reveal it in the file manager, on any
# platform: `show <path>`, `show -r <path>` (reveal), `show -n <path>` (dry run).
# All the platform logic lives in bin/grip-show; this is the same thin
# forwarder shape as py() above, for the same reason -- a function here reaches
# every installed machine on its next shell.
#
# The verb is `show`, deliberately not `view`: /usr/bin/view is vim's read-only
# mode on macOS and most Linux, and a function named view would shadow it
# silently in every shell that sources this file. V>> ruled on 2026-10-02 after
# a sovereign council sitting (seal 1f47e253330877a5).
#
# Behavioural coverage, bash AND zsh: tests/test_grip_show.py.
show() {
    local CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
    local shim="$CLAUDE_DIR/bin/grip-show"

    if [[ -x "$shim" ]]; then
        "$shim" "$@"
    elif command -v grip-show >/dev/null 2>&1; then
        grip-show "$@"
    else
        echo "[show] grip-show not found at $shim, and not on PATH." >&2
        echo "[show] GRIP may not be installed at $CLAUDE_DIR." >&2
        return 127
    fi
}
