#!/usr/bin/env bash
# grip-stats.sh — thin CLI shim over lib/asset_counter.py (the canonical
# inventory counter). The counting logic lives in ONE place (DRY); this script
# is kept as the stable command-line entry point existing callers already use.
#
# Usage:
#   ./scripts/grip-stats.sh          # print the canonical stats line
#   ./scripts/grip-stats.sh --fix    # rewrite the marked stats line in
#                                    # rules/system-capabilities.md
#
# ROOT RESOLUTION — a copy of this script counts the repo it LIVES IN.
#
# It previously ran `${CLAUDE_DIR:-$HOME/.claude}/lib/asset_counter.py`, so from
# a git worktree it executed the SHARED tree's counter against the SHARED tree's
# files. Running `--fix` there printed a stats line for a different filesystem
# and then said "No change needed", leaving the worktree's own line stale — a
# silent no-op on the file the operator was actually looking at. Observed
# 2026-08-26 while fixing a genuinely stale line: it reported Skills 320 /
# Hooks 110 for the shared tree while this repo held 309 / 111.
#
# An EXPLICIT CLAUDE_DIR still wins, because callers that set it mean it —
# lib/auto_fix_classifier.py suggests the shared-tree path by design, and a
# launchd agent regenerating ~/.grip must keep working. The change is only to
# the DEFAULT: the script's own repo rather than whatever ~/.claude points at.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname -- "$script_dir")"

# PRECEDENCE, and it applies to BOTH the counter and the tree it counts.
#
# An explicit CLAUDE_DIR names a whole GRIP install, so it supplies the counter
# AND the root - the pre-existing contract, unchanged. An earlier version of
# this fix resolved the counter from BASH_SOURCE first, so CLAUDE_DIR steered
# only the ROOT while the repo's counter code ran against it (AGORA #5113).
# That is a defensible design but it is not what "explicit CLAUDE_DIR wins"
# says, and a shim whose comment and behaviour disagree is worse than either.
#
# With CLAUDE_DIR unset the DEFAULT is this repo, which is the whole point: a
# worktree counts itself, with its own counter, so a change to the counting
# logic is exercised by the copy under test.
if [ -n "${CLAUDE_DIR:-}" ] && [ -f "${CLAUDE_DIR}/lib/asset_counter.py" ]; then
  root="$CLAUDE_DIR"
elif [ -f "${repo}/lib/asset_counter.py" ]; then
  root="$repo"
else
  # Not a repo layout (an installed shim, say) - fall back rather than fail, so
  # an unusual install keeps working.
  root="${CLAUDE_DIR:-$HOME/.claude}"
fi

exec env CLAUDE_DIR="$root" python3 "${root}/lib/asset_counter.py" "$@"
