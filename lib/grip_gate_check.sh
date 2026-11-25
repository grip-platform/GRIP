#!/usr/bin/env bash
# grip_gate_check.sh — run Python with GRIP lib/ on sys.path, from any CWD.
#
# The problem it solves: when invoked from a project that has its own lib/
# package (e.g. ~/.hal), Python's sys.path starts with '' (CWD), which
# resolves 'lib' to the calling project's lib/ — shadowing ~/.claude/lib/.
# Changing CWD to ~/.claude before exec makes '' resolve harmlessly.
#
# Usage (mirrors `python -c` and `python <script>`):
#   grip_gate_check.sh -c "from lib.gates.session_isolation import require_worktree_isolation; ..."
#   grip_gate_check.sh path/to/script.py [args...]
#
# Environment:
#   CLAUDE_DIR  override for ~/.claude (default: $HOME/.claude)

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

# Interpreter: the GRIP venv at $CLAUDE_DIR/venv, found by the cross-platform
# resolver, or a refusal -- never the system python3 that resolve_venv_python
# falls back to (rules/never-system-python-for-grip.md, mandate 1; bin/grip-py
# refuses the same way). The previous `exec "$CLAUDE_DIR/venv/bin/python"` died
# with a raw "No such file or directory" in a checkout, `git archive` export or
# worktree without venv/ (found on a bare CI runner 2026-09-24, re-found by
# GRIP#9509). A system fallback is not the fix: this wrapper runs any GRIP code
# it is given, not only the stdlib-only gate modules (Codex P1 on
# grip-platform/GRIP#9570). No hook calls this wrapper (checked 2026-11-25),
# so refusing cannot block the command that creates the venv.
# Guarded, not a bare `source`: under `set -e` a missing file here would
# abort with bash's own raw "No such file or directory" on the source line
# itself -- a correct refusal, but with no named cause, from exactly the
# AGORA review (grip-platform/GRIP#9570) that first fixed this file.
VENV_UTILS="$CLAUDE_DIR/scripts/lib/venv-utils.sh"
if [ ! -r "$VENV_UTILS" ]; then
    echo "grip_gate_check.sh: missing resolver $VENV_UTILS (is CLAUDE_DIR a real GRIP checkout?)" >&2
    exit 1
fi
# shellcheck source=../scripts/lib/venv-utils.sh
source "$VENV_UTILS"

cd "$CLAUDE_DIR"

grip_python="$(resolve_venv_python "$CLAUDE_DIR/venv" || true)"
if [[ "$grip_python" != "$CLAUDE_DIR/venv/"* ]]; then
    echo "grip_gate_check.sh: no GRIP venv Python at $CLAUDE_DIR/venv; refusing the system interpreter (rules/never-system-python-for-grip.md)." >&2
    echo "grip_gate_check.sh: create it with python3 -m venv \"$CLAUDE_DIR/venv\", or point CLAUDE_DIR at an install that has one." >&2
    exit 1
fi

PYTHONPATH="$CLAUDE_DIR" exec "$grip_python" "$@"
