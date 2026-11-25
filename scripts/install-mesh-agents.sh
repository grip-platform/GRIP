#!/usr/bin/env bash
# install-mesh-agents.sh — idempotent install (and drift probe) for the three
# grip-session-mesh launchd agents: mesh-bus, mesh-client, mesh-relay.
#
# Tracked sources live in config/mesh/com.grip.mesh-{bus,client,relay}.plist
# (issue #4304: production agents must have a reviewable in-repo source, and a
# probe must show the running job matches it). This installer is the single
# substitution authority for those templates — the template and the installed
# file cannot disagree unless someone hand-edits the installed copy, which is
# exactly what --verify exists to catch.
#
# Usage:
#   scripts/install-mesh-agents.sh              # render + (re)load all three
#   scripts/install-mesh-agents.sh --verify     # no writes: diff rendered vs installed
#   MESH_NODE=blackbook scripts/install-mesh-agents.sh   # override node name
set -euo pipefail

# Templates are located relative to THIS script's repo, so the installer works
# from any checkout (worktree, alternate clone) with no env needed (AGORA
# #4376 review, concern 1). CLAUDE_DIR still overrides for cross-repo runs.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CLAUDE_DIR="${CLAUDE_DIR:-$(dirname "$SCRIPT_DIR")}"
MESH_DIR="${MESH_DIR:-$HOME/CodeTonight/grip-session-mesh}"
MESH_NODE="${MESH_NODE:-$(hostname -s)}"
NODE_BIN="${NODE_BIN:-$(command -v node || echo /opt/homebrew/bin/node)}"
# The relay runs on the GRIP venv interpreter deliberately — mesh_relay.py
# needs the venv's dependencies, so a bare `command -v python3` would install
# a plist that starts and immediately crashes on imports. Resolve through the
# physical path (pwd -P): installed plists carry the resolved form, and a
# symlinked default would make --verify report drift that is only spelling.
PYTHON_BIN="${PYTHON_BIN:-$(cd "$CLAUDE_DIR" 2>/dev/null && pwd -P || echo "$CLAUDE_DIR")/venv/bin/python}"
AGENTS=(com.grip.mesh-bus com.grip.mesh-client com.grip.mesh-relay)
VERIFY=0
[[ "${1:-}" == "--verify" ]] && VERIFY=1

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[mesh-agents] non-macOS: launchd skipped. Run the mesh services via your scheduler."
  exit 0
fi

# Fail loud BEFORE writing anything: a plist pointing at a missing binary or
# tree would install an agent that crash-loops (AGORA #4376, concern 2 class).
require() {  # require <what> <path>
  if [[ ! -e "$2" ]]; then
    echo "[mesh-agents] ERROR: $1 not found at $2 — refusing to render a broken plist" >&2
    echo "               override with the matching env var (CLAUDE_DIR/MESH_DIR/NODE_BIN/PYTHON_BIN)" >&2
    exit 2
  fi
}
require "template dir" "$CLAUDE_DIR/config/mesh"
require "mesh checkout" "$MESH_DIR"
require "node binary" "$NODE_BIN"
require "venv python" "$PYTHON_BIN"

render() {  # render <label> -> stdout (template comments stripped: installed
            # plists carry none, and --verify compares semantics, not prose)
  sed -e "s|__NODE__|$NODE_BIN|g" \
      -e "s|__PYTHON__|$PYTHON_BIN|g" \
      -e "s|__MESH_DIR__|$MESH_DIR|g" \
      -e "s|__HOME__|$HOME|g" \
      -e "s|__MESH_NODE__|$MESH_NODE|g" \
      "$CLAUDE_DIR/config/mesh/$1.plist" | sed '/<!--/,/-->/d'
}

rc=0
for label in "${AGENTS[@]}"; do
  dest="$HOME/Library/LaunchAgents/$label.plist"
  if [[ "$VERIFY" == 1 ]]; then
    if [[ ! -f "$dest" ]]; then
      echo "[mesh-agents] $label: NOT INSTALLED"
      rc=1
    elif diff -q <(render "$label") "$dest" >/dev/null 2>&1; then
      echo "[mesh-agents] $label: MATCH (installed == rendered template)"
    else
      echo "[mesh-agents] $label: DRIFT — installed file differs from template render:"
      diff <(render "$label") "$dest" | head -12 || true
      rc=1
    fi
    continue
  fi
  render "$label" > "$dest"
  chmod 600 "$dest"  # hardening, not a launchd load requirement (#4376 concern 3)
  launchctl unload "$dest" 2>/dev/null || true
  launchctl load "$dest"
  echo "[mesh-agents] $label: installed + loaded from tracked template"
done
exit "$rc"
