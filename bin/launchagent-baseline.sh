#!/usr/bin/env bash
# launchagent-baseline.sh — diff ~/Library/LaunchAgents against whitelist.
#
# Defends against the Shai-Hulud / TanStack persistence path:
#   ~/Library/LaunchAgents/com.user.gh-token-monitor.plist
# (or any label not in the operator-approved whitelist).
#
# Baseline file: ~/.claude/state/security/launchagents.whitelist
# Each line: a *.plist filename (basename), or `# comment` lines.
#
# Operator workflow:
#   bin/launchagent-baseline.sh             # diff vs whitelist (default)
#   bin/launchagent-baseline.sh --bootstrap # capture current state as whitelist
#   bin/launchagent-baseline.sh --accept FOO.plist
#                                           # add a single file to whitelist
#
# Hypothesis H-SECINT-2: LaunchAgent diff catches `com.user.*`/
# `com.gh-token-monitor.*` style injections within 1 cron interval.
#
# Exit codes:
#   0 — current set matches whitelist (or bootstrap requested)
#   1 — drift detected (new or removed agents)
#   2 — internal error

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
AGENTS_DIR="${LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}"
STATE_DIR="$CLAUDE_DIR/state/security"
WHITELIST="$STATE_DIR/launchagents.whitelist"
LOG_DIR="$CLAUDE_DIR/logs/security"
LOG="$LOG_DIR/launchagents-drift.log"

mkdir -p "$STATE_DIR" "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"
}

# List visible plist filenames in the agents directory (sorted, basenames).
list_current() {
  if [[ ! -d "$AGENTS_DIR" ]]; then
    return 0
  fi
  ( cd "$AGENTS_DIR" && find . -maxdepth 1 -name '*.plist' -type f -print 2>/dev/null \
      | sed 's|^\./||' | sort )
}

# Extract bare plist names from the whitelist (skip blanks and comments).
list_whitelist() {
  if [[ ! -f "$WHITELIST" ]]; then
    return 0
  fi
  awk '!/^[[:space:]]*#/ && NF { print $1 }' "$WHITELIST" | sort -u
}

bootstrap() {
  {
    printf '# launchagents.whitelist — bootstrap %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    list_current
  } >"$WHITELIST"
  log "BOOTSTRAP captured $(list_current | wc -l | tr -d ' ') agents"
  echo "Bootstrap wrote $WHITELIST" >&2
  exit 0
}

accept_one() {
  local name=$1
  if [[ -z "$name" ]]; then
    echo "ERR: --accept requires a plist filename" >&2
    exit 2
  fi
  echo "$name  # accepted $(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$WHITELIST"
  log "ACCEPT $name"
  echo "Added $name to whitelist" >&2
  exit 0
}

case "${1:-}" in
  --bootstrap) bootstrap ;;
  --accept) accept_one "${2:-}" ;;
esac

if [[ ! -f "$WHITELIST" ]]; then
  log "MISSING-WHITELIST first-run-bootstrap"
  bootstrap
fi

current_tmp=$(mktemp)
whitelist_tmp=$(mktemp)
trap 'rm -f "$current_tmp" "$whitelist_tmp"' EXIT
list_current >"$current_tmp"
list_whitelist >"$whitelist_tmp"

added=$(comm -23 "$current_tmp" "$whitelist_tmp")
removed=$(comm -13 "$current_tmp" "$whitelist_tmp")

if [[ -z "$added" && -z "$removed" ]]; then
  exit 0
fi

if [[ -n "$added" ]]; then
  log "ADDED $(echo "$added" | tr '\n' ' ' | sed 's/ $//')"
  echo "[security] new LaunchAgents (not in whitelist):" >&2
  echo "$added" | sed 's|^|[security]   + |' >&2
fi
if [[ -n "$removed" ]]; then
  log "REMOVED $(echo "$removed" | tr '\n' ' ' | sed 's/ $//')"
  echo "[security] LaunchAgents missing from disk (still whitelisted):" >&2
  echo "$removed" | sed 's|^|[security]   - |' >&2
fi
echo "[security] review and either:" >&2
echo "[security]   - approve:    $0 --accept <name>.plist" >&2
echo "[security]   - re-baseline: $0 --bootstrap" >&2
echo "[security] log: $LOG" >&2
exit 1
