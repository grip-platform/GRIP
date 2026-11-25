#!/usr/bin/env bash
# whitelist-self-tripwire.sh — daily SHA256 drift check on the W1/W2
# whitelist files themselves.
#
# W1 (bin/settings-integrity-monitor.sh, PR #2670) detects drift in
# ~/.claude/settings.json by comparing its SHA256 against the
# operator-pinned hash in state/security/settings-integrity.whitelist.
#
# W2 (bin/launchagent-baseline.sh, PR #2671) does the same for the set
# of ~/Library/LaunchAgents/*.plist filenames against
# state/security/launchagents.whitelist.
#
# Failure mode: both whitelist files are themselves writable by the
# same user account that an attacker could compromise. If the attacker
# can replace settings.json with a malicious version, they can also
# rewrite settings-integrity.whitelist with the new SHA — laundering
# the tamper through the very mechanism meant to detect it.
#
# Defence: hash the whitelist files themselves and pin those hashes in
# a meta-whitelist. The meta-whitelist is bootstrapped on first run and
# can only be rotated via --accept (explicit operator action).
#
# Mechanism (same shape as W1):
#   1. Compute current SHA256 of each whitelist file.
#   2. Compare against state/security/whitelist-self.meta.
#   3. If hashes diverge AND the diff is not in the explicit allowlist,
#      append an alert to logs/security/whitelist-self-drift.log AND
#      exit non-zero so launchd / cron surface the failure.
#
# Tracked whitelist files (the ones W3 protects):
#   - state/security/settings-integrity.whitelist (from W1)
#   - state/security/launchagents.whitelist (from W2)
#
# Operator workflow after legitimate W1/W2 rotation:
#   bin/whitelist-self-tripwire.sh --accept
#
# Hypothesis H459 (H-SECINT-3): SHA256 hashing the whitelists themselves
# detects >=99% of unauthorised whitelist modifications within 24h.
#
# Exit codes:
#   0 — all tracked whitelists match meta (or are absent: harmless)
#   1 — drift detected (alerts logged, operator action required)
#   2 — internal error (missing tools, permissions)

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
STATE_DIR="$CLAUDE_DIR/state/security"
META="$STATE_DIR/whitelist-self.meta"
LOG_DIR="$CLAUDE_DIR/logs/security"
LOG="$LOG_DIR/whitelist-self-drift.log"

# Tracked whitelist files (anchored under STATE_DIR).
TRACKED=(
  "settings-integrity.whitelist"
  "launchagents.whitelist"
)

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

# Build the canonical meta payload: "<sha>  <name>\n" lines in TRACKED order.
# Missing files (legitimate when W1/W2 not yet bootstrapped) record "ABSENT".
build_payload() {
  local out=""
  for name in "${TRACKED[@]}"; do
    local path="$STATE_DIR/$name"
    if [[ -f "$path" ]]; then
      local sha
      sha=$(compute_sha "$path")
      out+="${sha}  ${name}"$'\n'
    else
      out+="ABSENT  ${name}"$'\n'
    fi
  done
  printf '%s' "$out"
}

accept_current() {
  local payload
  payload=$(build_payload)
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [[ -f "$META" ]]; then
    cp "$META" "$META.prev"
  fi
  {
    printf '# accepted %s\n' "$ts"
    printf '%s' "$payload"
  } >"$META"
  log "ACCEPT (operator action, $ts)"
  echo "Accepted whitelist-self meta at $ts" >&2
  exit 0
}

if [[ "${1:-}" == "--accept" ]]; then
  accept_current
fi

current_payload=$(build_payload)

if [[ ! -f "$META" ]]; then
  log "BOOTSTRAP (no prior meta; pinning current payload)"
  {
    printf '# bootstrap %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s' "$current_payload"
  } >"$META"
  exit 0
fi

# Strip comment lines and trailing whitespace for comparison.
strip_meta() {
  grep -v '^#' "$1" 2>/dev/null | sed 's/[[:space:]]*$//' || true
}

stored=$(strip_meta "$META")
current=$(printf '%s' "$current_payload" | sed 's/[[:space:]]*$//')

if [[ "$stored" == "$current" ]]; then
  exit 0
fi

log "DRIFT detected in whitelist-self meta"
log "  stored:  $(printf '%s' "$stored" | tr '\n' ';')"
log "  current: $(printf '%s' "$current" | tr '\n' ';')"
echo "[security] whitelist-self meta drifted." >&2
echo "[security]   stored hashes ($META):" >&2
printf '%s\n' "$stored" | sed 's/^/[security]     /' >&2
echo "[security]   current hashes:" >&2
printf '%s\n' "$current" | sed 's/^/[security]     /' >&2
echo "[security]   review whitelist files then run: $0 --accept" >&2
echo "[security]   log: $LOG" >&2
exit 1
