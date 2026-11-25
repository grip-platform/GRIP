#!/usr/bin/env bash
# Cross-platform Python runner for GRIP hooks
# Self-healing: creates venv + installs core deps on first run
# Idempotent: safe to call repeatedly (venv check is O(1) stat)

# Honour a CLAUDE_DIR the caller already exported; fall back to ~/.claude only
# when it is unset, empty or whitespace-only -- the same contract as
# lib/claude_dir.py::get_claude_dir, and the `${CLAUDE_DIR:-$HOME/.claude}` form
# rules/path-portability.md requires. This line used to assign "${HOME}/.claude"
# unconditionally, which OVERWROTE the exported value. Where the install is not
# at ~/.claude -- Windows keeps it in ~/.grip, exported by scripts/blackbook-xp,
# and ~/.claude there is a separate stale tree -- every hook started through
# this runner then ran under the wrong tree's venv and read and wrote that
# tree's state. Where CLAUDE_DIR is unset, or is ~/.claude, nothing changes.
_claude_dir="${CLAUDE_DIR:-}"
_claude_dir="${_claude_dir#"${_claude_dir%%[![:space:]]*}"}"   # trim leading
_claude_dir="${_claude_dir%"${_claude_dir##*[![:space:]]}"}"   # trim trailing
CLAUDE_DIR="${_claude_dir:-${HOME}/.claude}"
VENV_DIR="${CLAUDE_DIR}/venv"
# VENV_PYTHON is deliberately a full PATH, never a bare `python3` command: every
# `-x` test below, and the `exec` calls they guard, need a filesystem path, and a
# bare command name would make all three silently false rather than loudly wrong.
#
# It pinned `bin/python3` alone until GRIP#5919. The full ladder --
# Scripts/python.exe, bin/python3, bin/python -- now matches
# `scripts/lib/venv-utils.sh::resolve_venv_python`, and the third rung below is
# load-bearing rather than tidiness. Without it a `bin/python`-only venv missed
# every fast path and fell through to the bootstrap-lock guard, which execs
# "$SYS_PYTHON" outright. Measured 2026-09-14, before the rung existed:
#
#   with a live lock held -- a hook ran under
#     /opt/homebrew/opt/python@3.14/bin/python3.14, outside its own venv and
#     with no stderr at all. That is the harm rules/never-system-python-for-grip.md
#     names: GRIP code under the system interpreter does not fail, it downgrades
#     the IDR signature to a forgeable shared-key HMAC and still exits 0. With
#     many concurrent sessions and launchd agents on one runner, a live lock is
#     an ordinary condition, not an exotic one.
#
#   with no lock held -- it merely rebuilt a venv that already existed, costing
#     ~6s and a false "venv not found" line per hook invocation.
#
# Anchored by tests/test_hook_runner_reaches_the_venv_interpreter.py, which
# drives this whole script with HOME (and, for the CLAUDE_DIR arms, CLAUDE_DIR)
# pointed at a fixture tree, because the script cannot be sourced and takes no
# venv argument.
VENV_PYTHON="${VENV_DIR}/bin/python3"
BOOTSTRAP_LOCK="${CLAUDE_DIR}/cache/.venv-bootstrap.lock"

# Fast path: venv exists and is executable
if [[ -x "$VENV_PYTHON" ]]; then
    exec "$VENV_PYTHON" "$@"
fi

# Windows: check Scripts/python.exe
if [[ -x "${VENV_DIR}/Scripts/python.exe" ]]; then
    exec "${VENV_DIR}/Scripts/python.exe" "$@"
fi

# POSIX fallback: a venv is not obliged to carry `bin/python3`. uv omits some
# spellings, and a Python upgrade can leave a half-updated tree. This is the
# third rung of the same ladder `venv-utils.sh::resolve_venv_python` walks; the
# comment above records what happened when it was missing (GRIP#5919).
if [[ -x "${VENV_DIR}/bin/python" ]]; then
    exec "${VENV_DIR}/bin/python" "$@"
fi

# Slow path: venv missing or broken — auto-bootstrap
# Find system Python for creating the venv
SYS_PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"
if [[ -z "$SYS_PYTHON" ]]; then
    echo '{"error": "Python not found. Install Python 3.8+"}' >&2
    exit 1
fi

# Prevent concurrent bootstrap (lock file with PID)
if [[ -f "$BOOTSTRAP_LOCK" ]]; then
    lock_pid=$(cat "$BOOTSTRAP_LOCK" 2>/dev/null)
    if kill -0 "$lock_pid" 2>/dev/null; then
        # Another process is bootstrapping — fall back to system Python
        exec "$SYS_PYTHON" "$@"
    fi
fi

# Bootstrap the venv (one-time, ~5s)
mkdir -p "$(dirname "$BOOTSTRAP_LOCK")"
echo $$ > "$BOOTSTRAP_LOCK"

_cleanup() { rm -f "$BOOTSTRAP_LOCK"; }
trap _cleanup EXIT

echo "[GRIP] venv not found — bootstrapping ${VENV_DIR}..." >&2
"$SYS_PYTHON" -m venv "$VENV_DIR" 2>/dev/null

# Install core packages needed by hooks. Driven through the interpreter rather
# than a `bin/pip` console script, which a venv is not obliged to have: uv omits
# it, and `python3 -m venv` can leave `pip3` without the unsuffixed alias. The
# old `-x "$VENV_PIP"` test failed silently in both cases, so apsw never
# installed and nothing said so (GRIP#3896 fixed the same shape in
# scripts/grip-deps-install.sh).
if [[ -x "$VENV_PYTHON" ]]; then
    "$VENV_PYTHON" -m pip install --quiet apsw 2>/dev/null
    echo "[GRIP] venv bootstrapped with core packages" >&2
fi

# Full deps install (async, non-blocking)
DEPS_SCRIPT="${CLAUDE_DIR}/scripts/grip-deps-install.sh"
if [[ -f "$DEPS_SCRIPT" ]]; then
    bash "$DEPS_SCRIPT" >/dev/null 2>&1 &
fi

# Re-check: venv should now exist
if [[ -x "$VENV_PYTHON" ]]; then
    exec "$VENV_PYTHON" "$@"
fi

# Final fallback: system Python
exec "$SYS_PYTHON" "$@"
