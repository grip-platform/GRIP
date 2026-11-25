#!/usr/bin/env bash
#
# GRIP Installation Verification
# Checks that all components are properly installed and configured.
#
# Usage:
#   ./verify-install.sh          # Run all checks
#   ./verify-install.sh --quick  # Run essential checks only
#   install.sh --verify          # Also runs this script
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source logging if available, otherwise define minimal versions
if [[ -f "$CLAUDE_DIR/lib/logging.sh" ]]; then
    source "$CLAUDE_DIR/lib/logging.sh"
else
    log_info()    { echo "[INFO] $*"; }
    log_success() { echo "[OK] $*"; }
    log_error()   { echo "[ERROR] $*" >&2; }
    log_warn()    { echo "[WARN] $*"; }
fi

QUICK_MODE=false
FAILURES=0
WARNINGS=0
CHECKS=0

for arg in "$@"; do
    case "$arg" in
        --quick) QUICK_MODE=true ;;
    esac
done

# ============================================================================
# CHECK FUNCTIONS
# ============================================================================

pass() {
    CHECKS=$((CHECKS + 1))
    log_success "$1"
}

fail() {
    CHECKS=$((CHECKS + 1))
    FAILURES=$((FAILURES + 1))
    log_error "$1"
}

warn() {
    CHECKS=$((CHECKS + 1))
    WARNINGS=$((WARNINGS + 1))
    log_warn "$1"
}

# ============================================================================
# ESSENTIAL CHECKS (always run)
# ============================================================================

echo ""
echo "GRIP Installation Verification"
echo "==============================="

# Detect platform
case "$(uname -s)" in
    Darwin)  PLATFORM="macOS" ;;
    Linux)   PLATFORM="Linux" ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="Windows (Git Bash)" ;;
    *)       PLATFORM="$(uname -s)" ;;
esac
echo "Platform: $PLATFORM"
echo ""

# 1. GRIP directory exists
if [[ -d "$CLAUDE_DIR" ]]; then
    pass "GRIP directory exists at $CLAUDE_DIR"
else
    fail "GRIP directory NOT found at $CLAUDE_DIR"
    echo "  Fix: git clone https://github.com/grip-platform/GRIP.git ~/.claude"
    exit 1
fi

# 2. CLAUDE.md exists (proof it's a GRIP install, not just ~/.claude)
if [[ -f "$CLAUDE_DIR/CLAUDE.md" ]] || [[ -f "$CLAUDE_DIR/.claude/CLAUDE.md" ]]; then
    pass "CLAUDE.md found (GRIP installation confirmed)"
else
    fail "CLAUDE.md NOT found — this may not be a GRIP installation"
    echo "  Fix: git clone https://github.com/grip-platform/GRIP.git ~/.claude"
fi

# 3. Claude Code CLI installed
if command -v claude &>/dev/null; then
    local_version=$(claude --version 2>/dev/null || echo "unknown")
    pass "Claude Code CLI installed (${local_version})"
else
    fail "Claude Code CLI NOT found"
    echo "  Fix: npm install -g @anthropic-ai/claude-code"
fi

# 4. PATH includes ~/.claude/bin
if echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.claude/bin"; then
    pass "PATH includes ~/.claude/bin"
else
    fail "PATH does NOT include ~/.claude/bin"
    echo "  Fix: Run ~/.claude/scripts/install.sh or add to your shell config:"
    echo "  export PATH=\"\$HOME/.claude/bin:\$PATH\""
fi

# 5. grip CLI accessible
if command -v grip &>/dev/null; then
    pass "grip CLI accessible"
else
    fail "grip CLI NOT accessible (should be at ~/.claude/bin/grip)"
    echo "  Fix: Reload your shell: source ~/.zshrc (or source ~/.bashrc)"
fi

# 6. gg++ accessible
if command -v gg++ &>/dev/null || alias gg++ &>/dev/null 2>&1 || type gogrip &>/dev/null 2>&1; then
    pass "gg++ entry point accessible"
else
    warn "gg++ not accessible (optional but recommended)"
    echo "  Fix: Run ~/.claude/scripts/install.sh and reload your shell"
fi

if $QUICK_MODE; then
    echo ""
    echo "Quick check complete: $CHECKS checks, $FAILURES failures, $WARNINGS warnings"
    exit $((FAILURES > 0 ? 1 : 0))
fi

# ============================================================================
# EXTENDED CHECKS (skipped in --quick mode)
# ============================================================================

echo ""
echo "Extended Checks"
echo "---------------"

# 7. Shell functions sourced
SHELL_FUNCS_SOURCED=false
for rcfile in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile"; do
    if [[ -f "$rcfile" ]] && grep -q "GRIP SHELL FUNCTIONS" "$rcfile" 2>/dev/null; then
        SHELL_FUNCS_SOURCED=true
        break
    fi
done

if $SHELL_FUNCS_SOURCED; then
    pass "Shell functions sourced in shell config"
else
    warn "Shell functions not sourced (gg++ alias may not work)"
    echo "  Fix: Run ~/.claude/scripts/install.sh"
fi

# 8. Python available
PYTHON_CMD=""
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
fi

if [[ -n "$PYTHON_CMD" ]]; then
    py_version=$($PYTHON_CMD --version 2>&1)
    pass "Python available ($py_version)"
else
    warn "Python NOT found (some GRIP features require Python)"
fi

# 9. Python venv (optional)
if [[ -d "$CLAUDE_DIR/venv" ]]; then
    pass "Python virtual environment exists"
else
    warn "Python venv not found (optional — run install.sh to create)"
fi

# 10. Git repository
if [[ -d "$CLAUDE_DIR/.git" ]]; then
    pass "GRIP is a git repository (updates via git pull)"
else
    warn "GRIP is NOT a git repository (updates must be manual)"
fi

# 11. Identity file
if [[ -f "$CLAUDE_DIR/facts/identity.md" ]]; then
    pass "Identity file exists (facts/identity.md)"
else
    warn "Identity NOT configured — run /activate-grip in Claude Code"
fi

# 12. MCP servers directory
mcp_count=0
if [[ -d "$CLAUDE_DIR/mcp-servers" ]]; then
    mcp_count=$(ls -d "$CLAUDE_DIR/mcp-servers"/*/ 2>/dev/null | wc -l | tr -d ' ')
    pass "MCP servers directory exists ($mcp_count servers)"
else
    warn "MCP servers directory not found"
fi

# 13. Key directories exist
for dir in skills agents modes hooks lib rules config; do
    if [[ -d "$CLAUDE_DIR/$dir" ]]; then
        count=$(ls "$CLAUDE_DIR/$dir" 2>/dev/null | wc -l | tr -d ' ')
        pass "$dir/ exists ($count items)"
    else
        fail "$dir/ directory MISSING"
    fi
done

# 14. git-crypt status (informational)
if command -v git-crypt &>/dev/null; then
    if git-crypt status -e "$CLAUDE_DIR" &>/dev/null 2>&1; then
        pass "git-crypt available (encrypted files protected)"
    else
        log_info "git-crypt available but repo not encrypted (normal for contributors)"
    fi
else
    log_info "git-crypt not installed (encrypted files will appear as binary — this is normal)"
fi

# ============================================================================
# SUMMARY
# ============================================================================

echo ""
echo "==============================="
echo "Results: $CHECKS checks, $FAILURES failures, $WARNINGS warnings"
echo ""

if [[ $FAILURES -eq 0 && $WARNINGS -eq 0 ]]; then
    log_success "All checks passed. GRIP is ready to use."
    echo "  Launch: gg++ (or: cd ~/.claude && claude)"
elif [[ $FAILURES -eq 0 ]]; then
    log_success "Essential checks passed ($WARNINGS warnings — see above)."
    echo "  Launch: gg++ (or: cd ~/.claude && claude)"
else
    log_error "$FAILURES checks failed. Fix the issues above then re-run:"
    echo "  ~/.claude/scripts/verify-install.sh"
fi

exit $((FAILURES > 0 ? 1 : 0))
