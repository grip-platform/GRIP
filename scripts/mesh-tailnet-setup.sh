#!/usr/bin/env bash
# mesh-tailnet-setup.sh — idempotently bring up GRIP's cross-machine session mesh.
#
# Makes every GRIP session on every Tailnet machine concurrently context-aware by
# ensuring all three grip-session-mesh layers are up on THIS machine:
#   L1  WebSocket bus     :9474   (server/dist/index.js)
#   L2  Tailscale relay   :9475   (relay/mesh_relay.py connect) — bridges peers
#   L3  presence MCP server        (presence/dist/src/index.js, via .mcp.json)
# plus the bearer token, peer list, and reboot persistence (macOS launchd /
# Windows ONLOGON Scheduled Tasks via deploy/windows/install-windows.ps1 /
# Linux systemd note). Windows nodes are NOT autodetected — wire their peers
# explicitly (GRIP_MESH_PEERS or tailnet-peers.txt; scripts/grip-windows-mesh.sh
# does exactly that for A>> and F>>).
#
# IDEMPOTENT BY CONSTRUCTION: each step probes first and only acts on what is
# missing, so re-running is a no-op. That no-op property is the Banach fixed
# point proven by lib/mesh_setup_idempotent.py — this script re-probes at the end
# and asks the kernel to confirm convergence (exit 0 == nothing left to do).
#
# Run it on each team member's GRIP machine to join them to the mesh:
#   bash scripts/mesh-tailnet-setup.sh                 # autodetect tailnet peers
#   GRIP_MESH_PEERS=100.64.0.10,100.64.0.10 bash scripts/mesh-tailnet-setup.sh
#   bash scripts/mesh-tailnet-setup.sh --dry-run       # show the plan, change nothing
#
# Peers resolve from: $GRIP_MESH_PEERS (comma-sep) -> ~/.grip-session-mesh/
# tailnet-peers.txt (one IP/line) -> autodetect (online macOS/Linux tailnet
# devices, excluding self). A relay only bridges to a peer that also runs the
# mesh; non-mesh peers just retry-backoff harmlessly.
set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true
PEERS_CHANGED=false

# --- Path resolution (CLAUDE_DIR-portable) ---------------------------------
# Default to the repo root (parent of scripts/) so the script self-locates even
# when CLAUDE_DIR is unset and ~/.claude is not the install root (e.g. ~/.grip).
CLAUDE_DIR="${CLAUDE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
MESH_HOME="$HOME/.grip-session-mesh"
MCP_DIR="$CLAUDE_DIR/mcp-servers/grip-session-mesh"
PRESENCE_ENTRY="$MCP_DIR/presence/dist/src/index.js"
REPO_URL="https://github.com/CodeTonight-SA/grip-session-mesh"

case "$(uname -s)" in
  Darwin)               PLATFORM="macos" ;;
  Linux)                PLATFORM="linux" ;;
  MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;   # Git-Bash on Windows (A>>/F>>)
  *)                    PLATFORM="linux" ;;
esac

log()  { printf '[mesh-setup] %s\n' "$*"; }
do_or_echo() { if $DRY_RUN; then printf '[mesh-setup] DRY-RUN would: %s\n' "$*"; else eval "$*"; fi; }

# Tailscale CLI (PATH, the macOS app bundle, or the Windows install dir).
TS="$(command -v tailscale 2>/dev/null || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
[[ -x "$TS" ]] || TS="/c/Program Files/Tailscale/tailscale.exe"   # Git-Bash on Windows fallback

# Python that can import websockets (the relay dependency).
resolve_py() {
  local c
  # Windows venvs put the interpreter under Scripts/python.exe, not bin/python.
  for c in "$HOME/.grip/venv/Scripts/python.exe" "$CLAUDE_DIR/venv/Scripts/python.exe" \
           "$HOME/.grip/venv/bin/python" "$CLAUDE_DIR/venv/bin/python" \
           "$(command -v python3 || true)" "$(command -v python || true)"; do
    [[ -n "$c" && -x "$c" ]] && { echo "$c"; return; }
  done
  echo python3
}
PY="$(resolve_py)"

# Locate the mesh repo: existing built clone -> populated submodule -> init/clone.
resolve_repo() {
  if [[ -f "$HOME/CodeTonight/grip-session-mesh/presence/package.json" ]]; then
    echo "$HOME/CodeTonight/grip-session-mesh"
  elif [[ -f "$MCP_DIR/presence/package.json" && ! -L "$MCP_DIR/presence" ]]; then
    echo "$MCP_DIR"
  else
    echo ""  # needs init/clone (handled in ensure_presence)
  fi
}

# --- Peer resolution -------------------------------------------------------
self_ip() { "$TS" ip -4 2>/dev/null | head -1; }

detect_peers() {
  if [[ -n "${GRIP_MESH_PEERS:-}" ]]; then
    tr ',' '\n' <<<"$GRIP_MESH_PEERS" | sed '/^$/d'
  elif [[ -f "$MESH_HOME/tailnet-peers.txt" ]]; then
    # First whitespace token per non-comment line == the IP (inline # comments ok).
    grep -vE '^\s*(#|$)' "$MESH_HOME/tailnet-peers.txt" | awk '{print $1}'
  else
    # Autodetect online macOS/Linux tailnet workstations, EXCLUDING self and any
    # infra host (name matches remote|vps|server|relay) — a relay must never
    # bridge an infra box (e.g. the VPS). Pin tailnet-peers.txt for exact control.
    local me; me="$(self_ip)"
    "$TS" status 2>/dev/null \
      | awk 'tolower($4) ~ /macos|linux/ && $0 !~ /offline/ && tolower($2) !~ /remote|vps|server|relay/ {print $1}' \
      | grep -vx "${me:-__none__}" || true
  fi
}

# --- Probes (each: exit 0 == satisfied) ------------------------------------
# Cross-platform :port listen probe — Windows Git-Bash has no lsof, so the relay
# probe below falls back to this (macOS/Linux keep using lsof and never reach it).
_port_listen() {
  "$PY" - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket(); s.settimeout(0.4)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
}
probe_websockets_dep() { "$PY" -c "import websockets" 2>/dev/null; }
probe_presence_built() { [[ -f "$PRESENCE_ENTRY" ]] && node --check "$PRESENCE_ENTRY" 2>/dev/null; }
probe_token()          { [[ -s "$MESH_HOME/token" ]]; }
probe_bus_running()    { curl -sf http://127.0.0.1:9474/health >/dev/null 2>&1; }
probe_relay_running()  { lsof -nP -iTCP:9475 -sTCP:LISTEN >/dev/null 2>&1 || _port_listen 9475; }
probe_launchd_loaded() { launchctl list 2>/dev/null | grep -q com.grip.mesh-bus; }
# Exact JSON-array membership (NOT grep substring: an IP regex has literal-dot
# any-char, and a short IP can substring-match a longer configured one — both
# false matches that would silently skip bridging a peer).
peer_in_json() {
  "$PY" -c 'import json,sys
try:
    peers=json.load(open(sys.argv[2])).get("peers",[])
except Exception:
    peers=[]
sys.exit(0 if sys.argv[1] in peers else 1)' "$1" "$MESH_HOME/peers.json"
}
probe_peers_configured() {
  local ip
  while IFS= read -r ip; do
    [[ -z "$ip" ]] && continue
    peer_in_json "$ip" || return 1
  done < <(detect_peers)
  return 0
}

# --- Remediations (only invoked when the probe fails) ----------------------
ensure_websockets_dep() {
  probe_websockets_dep && { log "websockets dep: ok (no-op)"; return; }
  log "websockets dep: installing"
  do_or_echo "\"$PY\" -m pip install --quiet websockets"
}

ensure_presence_built() {
  probe_presence_built && { log "presence MCP: ok (no-op)"; return; }
  log "presence MCP: building + wiring"
  local repo; repo="$(resolve_repo)"
  if [[ -z "$repo" ]]; then
    if git -C "$CLAUDE_DIR" submodule update --init "mcp-servers/grip-session-mesh" 2>/dev/null \
       && [[ -f "$MCP_DIR/presence/package.json" ]]; then
      repo="$MCP_DIR"
    else
      do_or_echo "git clone \"$REPO_URL\" \"$HOME/CodeTonight/grip-session-mesh\""
      repo="$HOME/CodeTonight/grip-session-mesh"
    fi
  fi
  # Build presence if its dist is missing.
  [[ -f "$repo/presence/dist/src/index.js" ]] || do_or_echo "(cd \"$repo/presence\" && npm ci && npm run build)"
  # Wire it under mcp-servers so .mcp.json's grip-presence entry resolves.
  if [[ "$repo" != "$MCP_DIR" ]]; then
    do_or_echo "mkdir -p \"$MCP_DIR\" && ln -sfn \"$repo/presence\" \"$MCP_DIR/presence\""
  fi
}

ensure_token() {
  probe_token && { log "bearer token: ok (no-op)"; return; }
  log "bearer token: generating"
  local repo; repo="$(resolve_repo)"; repo="${repo:-$HOME/CodeTonight/grip-session-mesh}"
  do_or_echo "\"$PY\" \"$repo/relay/mesh_relay.py\" init-token"
}

ensure_peers_configured() {
  probe_peers_configured && { log "peers: ok (no-op)"; return; }
  local repo ip; repo="$(resolve_repo)"; repo="${repo:-$HOME/CodeTonight/grip-session-mesh}"
  while IFS= read -r ip; do
    [[ -z "$ip" ]] && continue
    peer_in_json "$ip" && continue
    log "peers: adding $ip"
    do_or_echo "\"$PY\" \"$repo/relay/mesh_relay.py\" remote add \"$ip\""
    PEERS_CHANGED=true
  done < <(detect_peers)
}

# Reload the running relay so a newly-added peer is actually bridged — the relay
# reads peers.json only at startup. Idempotent: fires ONLY when a peer was added
# this run, so a converged re-run never touches the relay.
reload_relay_if_peers_changed() {
  $PEERS_CHANGED || return 0
  probe_relay_running || return 0
  log "peers changed: reloading relay to bridge the new peer(s)"
  if [[ "$PLATFORM" == "macos" ]] && launchctl list 2>/dev/null | grep -q com.grip.mesh-relay; then
    do_or_echo "launchctl kickstart -k \"gui/$(id -u)/com.grip.mesh-relay\""
  else
    log "relay reload: restart the relay process to pick up new peers (no launchd agent)"
  fi
}

winpath() { cygpath -w "$1" 2>/dev/null || echo "$1"; }

# Windows: ONLOGON Scheduled Tasks (bus + relay + client) via install-windows.ps1,
# mirroring the macOS launchd agents. Probe-first so a converged re-run is a no-op.
# Windows nodes are not autodetected — their peers come from tailnet-peers.txt /
# GRIP_MESH_PEERS (scripts/grip-windows-mesh.sh writes them for A>>/F>>).
ensure_persistence_windows() {
  probe_bus_running && probe_relay_running && { log "windows tasks + bus/relay: ok (no-op)"; return; }
  local repo ps1 name wrepo wps1 wpy
  repo="$(resolve_repo)"; repo="${repo:-$HOME/CodeTonight/grip-session-mesh}"
  ps1="$repo/deploy/windows/install-windows.ps1"
  # The tasks run node against server/dist — build it first if it is missing.
  [[ -f "$repo/server/dist/index.js" ]] || do_or_echo "(npm install --prefix \"$repo/server\" && npm run build --prefix \"$repo/server\")"
  if [[ -f "$ps1" ]]; then
    name="$(hostname -s 2>/dev/null | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')"
    wrepo="$(winpath "$repo")"; wps1="$(winpath "$ps1")"; wpy="$(winpath "$PY")"
    log "windows: installing ONLOGON Scheduled Tasks (bus + relay + client) via install-windows.ps1"
    do_or_echo "powershell -ExecutionPolicy Bypass -File \"$wps1\" -Repo \"$wrepo\" -Python \"$wpy\" -SessionName \"$name\""
  else
    log "windows: install-windows.ps1 not found — starting bus + relay now (nohup); add the script for reboot persistence"
    probe_bus_running   || do_or_echo "nohup node \"$repo/server/dist/index.js\" >>\"$MESH_HOME/logs/bus.out.log\" 2>&1 &"
    probe_relay_running || do_or_echo "nohup \"$PY\" \"$repo/relay/mesh_relay.py\" connect >>\"$MESH_HOME/logs/relay.out.log\" 2>&1 &"
  fi
}

ensure_persistence() {
  # macOS: launchd agents (bus + relay + client) — also brings the daemons up.
  if [[ "$PLATFORM" == "macos" ]]; then
    probe_launchd_loaded && probe_bus_running && probe_relay_running && { log "launchd + bus/relay: ok (no-op)"; return; }
    log "launchd: installing agents (starts bus + relay)"
    local repo; repo="$(resolve_repo)"; repo="${repo:-$HOME/CodeTonight/grip-session-mesh}"
    do_or_echo "bash \"$repo/deploy/launchd/install-macos.sh\""
  elif [[ "$PLATFORM" == "windows" ]]; then
    ensure_persistence_windows
  else
    # Linux/other: best-effort foreground-less start; persistence = a systemd
    # unit the operator adds (documented, not auto-installed here).
    probe_bus_running || { log "bus: starting (nohup; add a systemd unit for persistence)"; \
      local repo; repo="$(resolve_repo)"; repo="${repo:-$HOME/CodeTonight/grip-session-mesh}"; \
      do_or_echo "nohup node \"$repo/server/dist/index.js\" >>\"$MESH_HOME/logs/bus.out.log\" 2>&1 &"; }
    probe_relay_running || { log "relay: starting (nohup)"; \
      local repo2; repo2="$(resolve_repo)"; repo2="${repo2:-$HOME/CodeTonight/grip-session-mesh}"; \
      do_or_echo "nohup \"$PY\" \"$repo2/relay/mesh_relay.py\" connect >>\"$MESH_HOME/logs/relay.out.log\" 2>&1 &"; }
  fi
}

ensure_insight_consumer() {
  # Real-time cross-machine learning: the always-on consumer that ingests peer
  # insight frames (bus inbound) into this machine's learning substrate. Idempotent.
  if [[ "$PLATFORM" != "macos" ]]; then
    log "insight-consumer: non-macOS (run via scheduler: python3 $CLAUDE_DIR/lib/mesh_insight_consumer.py)"
    return
  fi
  if launchctl list 2>/dev/null | grep -q com.grip.mesh-insight-consumer; then
    log "insight-consumer: ok (no-op)"
    return
  fi
  log "insight-consumer: installing launchd agent (real-time cross-machine learning)"
  do_or_echo "bash \"$CLAUDE_DIR/scripts/install-mesh-insight-consumer.sh\""
}

# --- Observed-state JSON for the Banach kernel ------------------------------
bool() { if "$@" >/dev/null 2>&1; then echo true; else echo false; fi; }

observed_json() {
  printf '{'
  printf '"websockets_dep":%s,'   "$(bool probe_websockets_dep)"
  printf '"presence_built":%s,'   "$(bool probe_presence_built)"
  printf '"token":%s,'            "$(bool probe_token)"
  printf '"bus_running":%s,'      "$(bool probe_bus_running)"
  printf '"relay_running":%s,'    "$(bool probe_relay_running)"
  printf '"peers_configured":%s,' "$(bool probe_peers_configured)"
  printf '"launchd_loaded":%s'    "$(bool probe_launchd_loaded)"
  printf '}'
}

# --- Main ------------------------------------------------------------------
main() {
  mkdir -p "$MESH_HOME/logs"
  log "machine=$(hostname -s) platform=$PLATFORM peers=[$(detect_peers | paste -sd, -)]"
  $DRY_RUN && log "DRY-RUN — no changes will be made"

  ensure_websockets_dep
  ensure_presence_built
  ensure_token
  ensure_peers_configured
  ensure_persistence
  ensure_insight_consumer

  # The Banach fixed-point proof, live: re-probe and let the kernel decide.
  # Verified BEFORE reloading the relay so a peers-changed `kickstart -k` cannot
  # race the relay-port probe and falsely report a down relay.
  log "verifying convergence (re-probe -> idempotence kernel)"
  local rc=0
  if observed_json | "$PY" "$CLAUDE_DIR/lib/mesh_setup_idempotent.py" --platform "$PLATFORM"; then
    log "CONVERGED — mesh is up; re-running this script is a no-op (fixed point)."
  else
    log "NOT converged — see pending actions above (re-run after addressing them)."
    rc=1
  fi

  # Runtime apply: bridge any newly-added peer (the relay reads peers.json only
  # at startup). After the verdict so the kickstart never races the probe above.
  reload_relay_if_peers_changed
  return "$rc"
}

main "$@"
