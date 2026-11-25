#!/usr/bin/env bash
# mac-selfdeploy.sh — SAFE auto-sync of the second Mac's ~/.claude to origin/main.
#
# The VPS has scripts/relay-selfdeploy.sh (cron */3) keeping it current. This is
# the Mac analogue (task #2173) — BUT the Mac tree is SHARED by concurrent
# interactive sessions and is git-crypt, so it is far more conservative: it
# FAST-FORWARDS *only* when the tree is provably safe, and otherwise DEFERS and
# logs. It NEVER rebases, stashes, or mutates a dirty tree — that is the
# 2026-07-10 hand-resolved-conflict lesson turned into a mechanical guard.
#
# Safety gates (ALL must hold before any FF):
#   - 0 commits ahead of origin/main        [FF-able; never auto-touch un-pushed local commits]
#   - >0 commits behind                      [something to do]
#   - working tree clean                     [no uncommitted work to disturb on the shared tree]
#   - .gitattributes clean                   [git-crypt corruption guard]
# Any gate fails -> LOG the reason + exit 0 (defer; never mutate). Fetch failure
# or an unresolvable origin/main after fetch -> defer, never a fabricated "OK".
#
# Usage:
#   mac-selfdeploy.sh                      one safe sync attempt (FF if safe, else defer)
#   mac-selfdeploy.sh --dry-run            evaluate gates + print the verdict, no FF
#   mac-selfdeploy.sh --install-launchd [--interval 300]
#   mac-selfdeploy.sh --uninstall-launchd
#
# Path-portability: rules/path-portability.md (CLAUDE_DIR override — also the test seam).
set -eo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
LOG="$CLAUDE_DIR/logs/mac-selfdeploy.log"
PLIST="$HOME/Library/LaunchAgents/com.grip.mac-selfdeploy.plist"
DRY_RUN=false
ACTION=sync
INTERVAL=300

prev=""
for a in "$@"; do
  case "$a" in
    --dry-run)           DRY_RUN=true ;;
    --install-launchd)   ACTION=install ;;
    --uninstall-launchd) ACTION=uninstall ;;
  esac
  [ "$prev" = "--interval" ] && INTERVAL="$a"
  prev="$a"
done

mkdir -p "$CLAUDE_DIR/logs"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG" 2>/dev/null || true; }
verdict() { echo "$1"; log "$1"; }

install_launchd() {
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.grip.mac-selfdeploy</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>$CLAUDE_DIR/scripts/mac-selfdeploy.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$CLAUDE_DIR</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$CLAUDE_DIR/logs/mac-selfdeploy.out.log</string>
  <key>StandardErrorPath</key><string>$CLAUDE_DIR/logs/mac-selfdeploy.err.log</string>
</dict></plist>
PL
  echo "wrote $PLIST (interval ${INTERVAL}s)"
  echo "ACTIVATE (V>>-gated): launchctl load -w $PLIST"
}

case "$ACTION" in
  install)   install_launchd; exit 0 ;;
  uninstall) launchctl unload -w "$PLIST" 2>/dev/null || true; rm -f "$PLIST"; echo "removed $PLIST"; exit 0 ;;
esac

cd "$CLAUDE_DIR"

if ! git fetch origin --quiet 2>/dev/null; then
  verdict "DEFER: git fetch failed (offline?)"; exit 0
fi

if ! counts="$(git rev-list --left-right --count HEAD...origin/main 2>/dev/null)"; then
  verdict "DEFER: origin/main unresolvable after fetch — remote branch missing; left untouched"; exit 0
fi
AHEAD="$(printf '%s' "$counts" | awk '{print $1}')"
BEHIND="$(printf '%s' "$counts" | awk '{print $2}')"
AHEAD="${AHEAD:-0}"; BEHIND="${BEHIND:-0}"

[ "$BEHIND" = "0" ] && { verdict "OK: up-to-date (0 behind)"; exit 0; }
[ "$AHEAD" != "0" ] && { verdict "DEFER: local is ${AHEAD} ahead — manual (never auto-touch un-pushed commits)"; exit 0; }
[ -n "$(git status --porcelain 2>/dev/null)" ] && { verdict "DEFER: working tree dirty — never FF a dirty shared tree (${BEHIND} behind)"; exit 0; }
[ -n "$(git status --porcelain -- .gitattributes 2>/dev/null)" ] && { verdict "DEFER: .gitattributes modified — git-crypt guard"; exit 0; }

# All gates pass: 0 ahead, >0 behind, clean tree, clean .gitattributes -> safe FF.
if [ "$DRY_RUN" = true ]; then
  verdict "WOULD-FF: clean + ${BEHIND} behind — safe to fast-forward (dry-run, no change)"; exit 0
fi

if git merge --ff-only origin/main >/dev/null 2>&1; then
  verdict "FF: fast-forwarded to $(git rev-parse --short HEAD) (${BEHIND} commits)"; exit 0
else
  verdict "DEFER: ff-only refused unexpectedly — left untouched"; exit 0
fi
