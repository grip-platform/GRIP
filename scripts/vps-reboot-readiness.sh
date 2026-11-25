#!/usr/bin/env bash
# vps-reboot-readiness.sh -- prove every pm2 app and the hal-wire user unit
# come back after a reboot of the VPS.
#
# Three verbs (the logic lives in lib/vps_reboot_readiness.py -- this file
# is a thin, shellcheck-trivial forwarder so the logic itself is unit
# testable without a second shell layer):
#
#   gate               -- is it safe to reboot RIGHT NOW? Exit 0 = CLEAR.
#   snapshot [path]    -- capture a before fact set; prints the path written.
#   verify <snapshot>  -- re-capture the same facts and diff against it.
#
# GATE DEFINITION (rules/operational-context.md carries the full note):
# clear means zero established connections on hal-gateway's :4010 -- a
# genuine reverse proxy, so a connection there really is an upstream call in
# flight -- AND no relay turn in flight, read from state/relay/inflight/
# rather than from relay-dispatcher's own port (:8787). That port's handler
# answers in milliseconds regardless of how long the turn's background work
# takes, so reading connection state there cannot tell a turn in progress
# from an idle listener.
#
# Usage:
#   vps-reboot-readiness.sh gate
#   vps-reboot-readiness.sh snapshot [path]
#   vps-reboot-readiness.sh verify <snapshot-path>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# CLAUDE_DIR is the STATE root the Python module's defaults resolve
# against (state/relay/inflight/, state/reboot-readiness/) -- on the VPS it
# is the same directory this script lives in, but it need not be: a test
# can point it at an isolated temp directory without needing a second copy
# of lib/ there. Importability (below) is resolved from REPO_ROOT instead
# of cwd/CLAUDE_DIR, so the two concerns never collide.
export CLAUDE_DIR="${CLAUDE_DIR:-$REPO_ROOT}"

# shellcheck source=scripts/lib/venv-utils.sh
source "$SCRIPT_DIR/lib/venv-utils.sh"

# never-system-python-for-grip.md: resolve the GRIP venv's interpreter
# rather than a bare `python3` -- this script reads pm2/ss/health state and
# writes a plain JSON snapshot, so it does not itself need the dual-sign
# capability that rule is about, but every spawn site resolves the same way
# regardless of what it does once it is running.
PY="$(resolve_venv_python "$REPO_ROOT/venv" || true)"
if [ -z "$PY" ]; then
    echo "vps-reboot-readiness: no working Python interpreter found" >&2
    exit 1
fi

PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" -m lib.vps_reboot_readiness "$@"
