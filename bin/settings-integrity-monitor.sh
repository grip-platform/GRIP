#!/usr/bin/env bash
# settings-integrity-monitor.sh — daily SHA256 drift check on settings.json
#
# Detects unauthorised modifications to ~/.claude/settings.json, the
# Shai-Hulud / TanStack worm's primary persistence target. settings.json
# is no longer git-tracked (commit a9aeb0bc) so git is not a tripwire.
#
# Mechanism:
#   1. Compute current SHA256 of settings.json.
#   2. Compare against the operator-whitelisted SHA in
#      ~/.claude/state/security/settings-integrity.whitelist.
#   3. If hashes diverge AND the diff is not in the explicit allowlist,
#      append an alert to ~/.claude/logs/security/settings-drift.log AND
#      exit non-zero so launchd / cron surface the failure.
#
# Whitelist format (one SHA per line, optional `# comment`):
#   abc123...   # 2026-05-13 manual edit, V>>
#
# Operator workflow after legitimate edit:
#   bin/settings-integrity-monitor.sh --accept
#     -> rewrites whitelist to current SHA, appends prior to history
#
# Hypothesis H-SECINT-1: file-integrity hash on settings.json detects
# >=99% of unauthorised modifications within 24h.
#
# Exit codes:
#   0 — settings.json matches whitelist (or is absent: harmless)
#   1 — drift detected (alerts logged, operator action required)
#   2 — internal error (missing tools, permissions)

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
STATE_DIR="$CLAUDE_DIR/state/security"
WHITELIST="$STATE_DIR/settings-integrity.whitelist"
LOG_DIR="$CLAUDE_DIR/logs/security"
LOG="$LOG_DIR/settings-drift.log"

mkdir -p "$STATE_DIR" "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"
}

compute_sha() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "ERR: no shasum or sha256sum available" >&2
    exit 2
  fi
}

accept_current() {
  if [[ ! -f "$SETTINGS" ]]; then
    echo "settings.json absent at $SETTINGS — nothing to accept" >&2
    exit 2
  fi
  local sha
  sha=$(compute_sha "$SETTINGS")
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [[ -f "$WHITELIST" ]]; then
    cp "$WHITELIST" "$WHITELIST.prev"
  fi
  printf '%s  # accepted %s\n' "$sha" "$ts" >"$WHITELIST"
  log "ACCEPT $sha (operator action)"
  echo "Accepted $sha at $ts" >&2
  exit 0
}

if [[ "${1:-}" == "--accept" ]]; then
  accept_current
fi

if [[ ! -f "$SETTINGS" ]]; then
  log "MISSING $SETTINGS (benign on daemon-only hosts)"
  exit 0
fi

current_sha=$(compute_sha "$SETTINGS")

if [[ ! -f "$WHITELIST" ]]; then
  log "BOOTSTRAP $current_sha (no prior whitelist; pinning)"
  printf '%s  # bootstrap %s\n' "$current_sha" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$WHITELIST"
  exit 0
fi

if grep -q "^${current_sha}" "$WHITELIST" 2>/dev/null; then
  exit 0
fi

log "DRIFT current=$current_sha whitelist=$(head -1 "$WHITELIST" | awk '{print $1}')"
echo "[security] settings.json SHA drifted from whitelist." >&2
echo "[security]   current:   $current_sha" >&2
echo "[security]   whitelist: $(head -1 "$WHITELIST" | awk '{print $1}')" >&2
echo "[security]   review $SETTINGS then run: $0 --accept" >&2
echo "[security]   log: $LOG" >&2
exit 1
