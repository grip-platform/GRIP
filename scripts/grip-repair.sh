#!/usr/bin/env bash
# grip-repair.sh — One-command GRIP installation repair
# Chains existing scripts in the correct order. No new logic.
#
# Usage:
#   bash scripts/grip-repair.sh              # Full repair
#   bash scripts/grip-repair.sh --skip-kono  # Skip 274MB model download
#   bash scripts/grip-repair.sh --dry-run    # Show what would be done

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SCRIPT_DIR="${CLAUDE_DIR}/scripts"

# shellcheck source=../lib/logging.sh
source "${CLAUDE_DIR}/lib/logging.sh"

# --- Flags ---
SKIP_KONO=false
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --skip-kono) SKIP_KONO=true ;;
    --dry-run)   DRY_RUN=true ;;
    -h|--help)
      echo "Usage: grip-repair.sh [--skip-kono] [--dry-run]"
      echo "  --skip-kono  Skip KONO/venv setup (saves 274MB download)"
      echo "  --dry-run    Show what would be done without executing"
      exit 0
      ;;
    *) log_warn "Unknown flag: $arg (ignored)" ;;
  esac
done

run_phase() {
  local phase="$1" desc="$2"
  shift 2
  log_step "Phase $phase: $desc"
  if $DRY_RUN; then
    log_dry "run: $*"
    return 0
  fi
  "$@"
}

# --- Phase 1: Diagnose (baseline snapshot) ---
run_phase 1 "Diagnose (baseline)" bash "${SCRIPT_DIR}/grip-health-check.sh" || true

# --- Phase 2: Code sync ---
if $DRY_RUN; then
  log_step "Phase 2: Code sync"
  log_dry "git stash (if needed) + git pull --rebase origin main + git stash pop"
else
  log_step "Phase 2: Code sync"
  cd "$CLAUDE_DIR"

  # Auto-stash uncommitted changes
  stashed=false
  if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    log_info "Stashing uncommitted changes..."
    git stash push -m "grip-repair auto-stash $(date +%Y%m%d-%H%M%S)" && stashed=true
  fi

  # Pull latest — skip if no network
  if git ls-remote --exit-code origin HEAD &>/dev/null; then
    git pull --rebase origin main && log_success "Code synced" || log_warn "Pull failed (continuing with local-only repair)"
  else
    log_warn "No network — skipping git pull (local-only repair)"
  fi

  # Restore stash
  if $stashed; then
    git stash pop && log_info "Restored stashed changes" || log_warn "Stash pop had conflicts — resolve manually after repair"
  fi
fi

# --- Phase 3: Settings ---
run_phase 3 "Settings (hooks, ACE, thinking)" bash "${SCRIPT_DIR}/grip-bootstrap-settings.sh" || true

# --- Phase 4: Permissions ---
if $DRY_RUN; then
  log_step "Phase 4: Permissions"
  log_dry "chmod +x hooks and scripts"
else
  log_step "Phase 4: Permissions"
  # Hooks
  for hook in "${CLAUDE_DIR}"/hooks/*.sh; do
    [ -f "$hook" ] && chmod +x "$hook"
  done
  # Scripts
  for script in "${SCRIPT_DIR}"/*.sh; do
    [ -f "$script" ] && chmod +x "$script"
  done
  # Bin
  for bin in "${CLAUDE_DIR}"/bin/*; do
    [ -f "$bin" ] && chmod +x "$bin"
  done
  log_success "Permissions fixed"
fi

# --- Phase 5: KONO/venv ---
if $SKIP_KONO; then
  log_step "Phase 5: KONO/venv (skipped — --skip-kono)"
else
  PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"
  if [ -z "$PYTHON" ]; then
    log_step "Phase 5: KONO/venv (skipped — no Python found)"
    log_warn "Install Python 3.9+ to enable memory subsystem"
  else
    run_phase 5 "KONO/venv (memory subsystem)" bash "${SCRIPT_DIR}/bootstrap-semantic-rl.sh" || true
  fi
fi

# --- Phase 6: Git local config ---
run_phase 6 "Git local config (skip-worktree, git-crypt, merge)" bash "${SCRIPT_DIR}/setup-local-git.sh" || true

# --- Phase 7: Verify (after snapshot) ---
echo ""
log_info "─────────────────────────────────────────"
log_info "Repair complete. Running final health check..."
log_info "─────────────────────────────────────────"
echo ""
run_phase 7 "Verify (final health check)" bash "${SCRIPT_DIR}/grip-health-check.sh" || true
