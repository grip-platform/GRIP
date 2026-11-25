#!/usr/bin/env bash
# grip-effort.sh — build the argv that sets reasoning effort on a harness.
#
# Extracted from bin/grip so it can be unit-tested. The logic it replaces sat
# immediately before an `exec`, which is untestable by construction: any test
# that reached it would replace its own process. Shipping an untested splice on
# the launch path is not a trade worth making, so the assembly lives here and
# bin/grip calls it.
#
# The registry (lib/grip_harness_picker.py) owns which harnesses have a VERIFIED
# way to receive an effort setting. This asks rather than hardcoding a flag: a
# guessed flag either crashes the launch or — far worse — is silently ignored,
# leaving the operator believing they run at an effort they do not. That silent
# case is the whole reason grip_launch_args refused effort in the first place.
#
# Tests: tests/test_grip_effort.py

# grip_effort_argv <harness-binary> <effort-level>
#
# Prints one argv element per line and returns 0 when the harness can take the
# setting; prints nothing and returns 1 when it cannot, or when either argument
# is empty. Callers MUST check the exit status — empty output alone is
# ambiguous, and treating it as success is how a setting gets silently dropped.
# Resolved from THIS FILE's own location, not from an environment variable.
#
# The classifier (lib/grip_launch_args.py) already resolves its registry from
# its own path, so an env-var default here could point the two halves at
# DIFFERENT registries: the classifier would accept an effort the executor then
# could not apply. Not hypothetical — running from a git worktree makes GRIP_DIR
# the worktree while CLAUDE_DIR still resolves to the main checkout, which is the
# normal state during any isolated session. Self-locating removes the divergence
# rather than documenting it.
GRIP_EFFORT_LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

grip_effort_argv() {
    local bin="${1:-}"
    local effort="${2:-}"
    if [[ -z "$bin" || -z "$effort" ]]; then
        return 1
    fi
    local picker="${GRIP_HARNESS_PICKER:-$GRIP_EFFORT_LIB_DIR/grip_harness_picker.py}"
    if [[ ! -f "$picker" ]]; then
        >&2 echo "[grip-effort] harness registry not found at $picker"
        return 1
    fi
    local py="${GRIP_EFFORT_PYTHON:-python3}"
    # stderr is NOT swallowed. A missing, malformed or crashing picker is a
    # broken install, and hiding it makes that indistinguishable from the
    # ordinary "this harness takes no effort setting" case — the operator would
    # see a generic warning and never learn their tooling was broken. Only
    # stdout feeds the caller's read loop, so diagnostics reach the terminal
    # without corrupting the argv.
    "$py" "$picker" effort "$bin" "$effort"
}
