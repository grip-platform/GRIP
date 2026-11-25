#!/usr/bin/env bash
# install-moat-anchor.sh — idempotent install (and drift probe) for the
# com.grip.moat-anchor launchd agent: continuous OpenTimestamps anchoring of
# the moat (IDR forest, memory chain, hypothesis ledger, insights, council
# seals) onto Bitcoin.
#
# Same contract as scripts/install-mesh-agents.sh (the AGORA-converged #4376
# pattern): the tracked template in config/moat-anchor/ is the single source;
# this installer is the single substitution authority; --verify diffs the
# rendered template against the installed file so hand-edits surface as DRIFT.
#
# Usage:
#   scripts/install-moat-anchor.sh              # render + (re)load
#   scripts/install-moat-anchor.sh --verify     # no writes: diff rendered vs installed
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CLAUDE_DIR="${CLAUDE_DIR:-$(dirname "$SCRIPT_DIR")}"
# Resolve through the physical path so installed plists carry one spelling and
# --verify never reports symlink-only drift (~/.claude vs ~/.grip).
CLAUDE_DIR="$(cd "$CLAUDE_DIR" && pwd -P)"
LABEL=com.grip.moat-anchor
VERIFY=0
[[ "${1:-}" == "--verify" ]] && VERIFY=1

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[moat-anchor] non-macOS: launchd skipped. Schedule 'bin/grip-moat-anchor run' hourly via cron/systemd."
  exit 0
fi

# Fail loud BEFORE writing anything: a plist pointing at a missing binary
# would install an agent that crash-loops.
require() {  # require <what> <path>
  if [[ ! -e "$2" ]]; then
    echo "[moat-anchor] ERROR: $1 not found at $2 — refusing to render a broken plist" >&2
    exit 2
  fi
}
require "template" "$CLAUDE_DIR/config/moat-anchor/$LABEL.plist"
require "shim" "$CLAUDE_DIR/bin/grip-moat-anchor"
require "implementation" "$CLAUDE_DIR/lib/moat_anchor.py"
if ! command -v ots >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/ots" ]]; then
  echo "[moat-anchor] ERROR: ots client not found (pip install opentimestamps-client) — refusing to install an agent whose every run would defer" >&2
  exit 2
fi

render() {  # -> stdout, template comments stripped (installed plists carry
            # none; --verify compares semantics, not prose)
  sed -e "s|__CLAUDE_DIR__|$CLAUDE_DIR|g" \
      -e "s|__HOME__|$HOME|g" \
      "$CLAUDE_DIR/config/moat-anchor/$LABEL.plist" | sed '/<!--/,/-->/d'
}

dest="$HOME/Library/LaunchAgents/$LABEL.plist"
if [[ "$VERIFY" == 1 ]]; then
  if [[ ! -f "$dest" ]]; then
    echo "[moat-anchor] $LABEL: NOT INSTALLED"
    exit 1
  elif diff -q <(render) "$dest" >/dev/null 2>&1; then
    echo "[moat-anchor] $LABEL: MATCH (installed == rendered template)"
    exit 0
  else
    echo "[moat-anchor] $LABEL: DRIFT — installed file differs from template render:"
    diff <(render) "$dest" | head -12 || true
    exit 1
  fi
fi

mkdir -p "$CLAUDE_DIR/logs"
render > "$dest"
chmod 600 "$dest"
launchctl unload "$dest" 2>/dev/null || true
launchctl load "$dest"
echo "[moat-anchor] $LABEL: installed + loaded from tracked template (hourly at :43, anchor-on-change)"
