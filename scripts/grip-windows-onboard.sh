#!/usr/bin/env bash
# ============================================================================
#  GRIP - Windows (Git Bash) onboarding migrator   ·   ATTENDED, run LIVE
#
#  V>> runs this WITH F>> (Fabio) or A>> (Andre) during the HAPPIverse
#  induction. It migrates an existing Claude Code install into the same GRIP
#  topology V>> uses on the Mac:
#
#        ~/.claude   ->   ~/.grip     (a directory JUNCTION on Windows)
#
#  so EVERY GRIP path resolves on Windows exactly as it does on the MacBook.
#
#  It does NOT make you log in again: your Claude credentials, settings and
#  project state are carried across untouched (Layer-0 preservation is handled
#  by bin/grip-harness-link, which this script delegates to).
#
#  Idempotent + safe to re-run. On macOS/Linux it runs READ-ONLY (a preview) -
#  the real migration path is Windows Git Bash.
#
#  Two Windows homes it must handle (note the SPACE in A>>'s):
#        F>>   C:\users\user           ->  $HOME=/c/users/user          (no space)
#        A>>   C:\Users\<Your Name>   ->  $HOME=/c/Users/<Your Name>  (SPACE!)
#  => every "$HOME" and every Windows path is quoted.
# ============================================================================
set -uo pipefail

GRIP_HOME="${GRIP_HOME:-$HOME/.grip}"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
INSTALL_URL="https://your-gateway.example.com"
HARNESS_LINK="$GRIP_HOME/bin/grip-harness-link"

# ---- loud, plain-English output helpers ------------------------------------
say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '    ok   %s\n' "$*"; }
warn() { printf '    !!   %s\n' "$*"; }

IS_WINDOWS=0
case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;; esac

# /c/Users/<Your Name> -> C:\Users\<Your Name>  (cygpath ships with Git Bash)
win_path() { cygpath -w "$1" 2>/dev/null || printf '%s' "$1"; }

claude_running() {
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -x claude >/dev/null 2>&1 && return 0
  fi
  if command -v tasklist >/dev/null 2>&1; then
    tasklist 2>/dev/null | grep -qi 'claude' && return 0
  fi
  return 1
}

# ---- (a) preflight ---------------------------------------------------------
step_preflight() {
  step "(a) Preflight - checking your environment"
  if [ "$IS_WINDOWS" = "1" ]; then
    ok "Git Bash on Windows detected ($(uname -s)) - full migration path"
  else
    warn "NOT Windows Git Bash ($(uname -s 2>/dev/null || echo unknown))."
    warn "This migrator is for F>>/A>>'s Windows machines. Running READ-ONLY here."
  fi
  say "    Home directory : $HOME"
  say "    GRIP will live : $GRIP_HOME"
  say "    Harness path   : $CLAUDE_DIR  (will resolve to $GRIP_HOME)"
  if claude_running; then
    warn "A 'claude' process looks ACTIVE. Please close all Claude Code windows,"
    warn "then re-run - migrating a live harness dir can lose in-flight state."
  else
    ok "no active 'claude' process detected"
  fi
}

# ---- (b) install or update GRIP -------------------------------------------
step_install() {
  step "(b) Install or update GRIP at $GRIP_HOME"
  if [ -d "$GRIP_HOME/.git" ]; then
    if [ "$IS_WINDOWS" = "1" ]; then
      if git -C "$GRIP_HOME" pull --ff-only 2>/dev/null; then
        ok "updated to latest main"
      else
        warn "git pull --ff-only skipped (local changes / offline) - continuing"
      fi
    else
      ok "GRIP present - skipping pull (read-only preview mode)"
    fi
    return 0
  fi
  if [ "$IS_WINDOWS" != "1" ]; then
    warn "GRIP not found - on Windows this installs via: curl -fsSL $INSTALL_URL | bash"
    warn "(read-only preview: not installing on non-Windows)"
    return 1
  fi
  say "    GRIP not found - installing via the one-liner:"
  say "      curl -fsSL $INSTALL_URL | bash"
  command -v curl >/dev/null 2>&1 || { warn "curl missing - install Git for Windows, then re-run"; return 1; }
  if curl -fsSL "$INSTALL_URL" | bash; then
    ok "GRIP installed at $GRIP_HOME"
  else
    warn "install failed - check network / GitHub auth, then re-run"
    return 1
  fi
}

# ---- (c) harness link (delegates to bin/grip-harness-link) ------------------
step_link() {
  step "(c) Migrate ~/.claude and create the .claude -> .grip junction"
  if [ ! -x "$HARNESS_LINK" ]; then
    warn "helper not found / not executable: $HARNESS_LINK"
    warn "GRIP install may be incomplete - re-run step (b) or: git -C \"$GRIP_HOME\" pull"
    return 1
  fi
  if [ "$IS_WINDOWS" = "1" ]; then
    say "    Preserving your credentials + settings, backing up the rest, then"
    say "    junctioning: $CLAUDE_DIR  ->  $GRIP_HOME"
    if "$HARNESS_LINK" --grip-home "$GRIP_HOME" --claude-dir "$CLAUDE_DIR" --force-backup; then
      ok "harness linked (credentials preserved; old dir backed up as .bak-<timestamp>)"
    else
      warn "grip-harness-link refused / failed - read its message above and follow it"
      return 1
    fi
  else
    say "    READ-ONLY preview (non-Windows) - no files will change:"
    "$HARNESS_LINK" --grip-home "$GRIP_HOME" --claude-dir "$CLAUDE_DIR" --dry-run \
      || warn "dry-run reported an issue (see above)"
  fi
}

# ---- (d) verify ------------------------------------------------------------
step_verify() {
  step "(d) Verify the migration"
  if [ "$IS_WINDOWS" = "1" ]; then
    local jline
    jline="$(cmd //c "dir /AL \"$(win_path "$HOME")\"" 2>/dev/null | grep -i '\.claude' || true)"
    if [ -n "$jline" ]; then
      ok ".claude is a JUNCTION (from 'dir /AL'):"
      say "      $jline"
    else
      warn "could not confirm the .claude junction via 'dir /AL' - check manually"
    fi
  else
    local resolved grip_real
    resolved="$(readlink -f "$CLAUDE_DIR" 2>/dev/null || true)"
    grip_real="$(readlink -f "$GRIP_HOME" 2>/dev/null || true)"
    if [ -n "$resolved" ] && [ "$resolved" = "$grip_real" ]; then
      ok "$CLAUDE_DIR resolves to $GRIP_HOME"
    else
      say "    (preview) $CLAUDE_DIR would resolve to $GRIP_HOME after the live Windows run"
    fi
  fi
  if [ -r "$CLAUDE_DIR/CLAUDE.md" ]; then
    ok "$CLAUDE_DIR/CLAUDE.md is readable - the harness can see GRIP"
  else
    warn "$CLAUDE_DIR/CLAUDE.md NOT readable yet - junction/link not active"
  fi
  if [ -d "$GRIP_HOME/.git" ]; then
    say "    GRIP version: $(git -C "$GRIP_HOME" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  fi
}

# ---- (e) footer ------------------------------------------------------------
step_footer() {
  step "(e) Done - what you now have"
  cat <<'EOF'
    You are now on the GRIP topology:

      GRIP   - the reasoning + safety harness (this is what Claude Code loads)
      HAL    - the multi-provider LLM substrate GRIP talks through
      HAPPI  - the polyglot AI protocol spec HAL implements

    ~/.claude now points at ~/.grip, so every GRIP path resolves exactly like
    it does on V>>'s Mac. Your Claude login and settings were preserved.

    Next steps (V>> will walk you through these live):
      1. Open your personal Seal of Approval (your HAPPIverse induction card).
      2. Join the Tailnet so your machine is on the mesh:
           - install Tailscale, then run:  tailscale up
           - sign in in the browser; V>> approves your device in the admin console.
      3. Launch Claude Code from anywhere and confirm the GRIP warm-start banner.
EOF
}

main() {
  say "============================================================================"
  say " GRIP - Windows onboarding  ·  ATTENDED migrator (run this WITH V>>)"
  say "============================================================================"
  step_preflight
  step_install || { warn "stopping: GRIP is not installed"; exit 1; }
  step_link    || { warn "stopping: harness link not completed"; exit 1; }
  step_verify
  step_footer
  say ""
  say "============================================================================"
  say " Onboarding complete. Welcome to the HAPPIverse."
  say "============================================================================"
}

main "$@"
