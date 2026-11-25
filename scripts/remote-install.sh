#!/usr/bin/env bash
#
# GRIP Remote Installer
# One-liner installation via: curl -fsSL https://raw.githubusercontent.com/grip-platform/GRIP/main/scripts/remote-install.sh | bash
#
# Options:
#   --fresh  Remove existing ~/.claude and install fresh
#   --merge  Preserve existing config, merge GRIP
#
set -euo pipefail

REPO="https://github.com/grip-platform/GRIP.git"
CLAUDE_HOME="${HOME}/.claude"
MODE=""

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --fresh) MODE="fresh"; shift ;;
        --merge) MODE="merge"; shift ;;
        *) shift ;;
    esac
done

echo ""
echo "GRIP Remote Installer"
echo "====================="
echo ""

# Check for git
if ! command -v git &>/dev/null; then
    log_error "Git is required but not installed."
    log_info "Install git first: https://git-scm.com/downloads"
    exit 1
fi

# Detect existing installation
if [[ -d "$CLAUDE_HOME" ]]; then
    if [[ -z "$MODE" ]]; then
        echo "Existing ~/.claude found."
        echo ""
        echo "1) Fresh install (move existing to backup)"
        echo "2) Merge install (preserve your config)"
        echo ""
        read -rp "Choose [1/2]: " choice
        case $choice in
            1) MODE="fresh" ;;
            2) MODE="merge" ;;
            *)
                log_error "Invalid choice"
                exit 1
                ;;
        esac
    fi
else
    MODE="fresh"
fi

# Fresh install - move existing to backup
if [[ "$MODE" == "fresh" && -d "$CLAUDE_HOME" ]]; then
    log_info "Moving ~/.claude to backup..."

    # Try trash command first (macOS), then gio (Linux), then fallback to mv
    if command -v trash &>/dev/null; then
        trash "$CLAUDE_HOME"
        log_success "Moved to trash"
    elif command -v gio &>/dev/null; then
        gio trash "$CLAUDE_HOME" 2>/dev/null || {
            BACKUP="${CLAUDE_HOME}-backup-$(date +%Y%m%d%H%M%S)"
            mv "$CLAUDE_HOME" "$BACKUP"
            log_success "Backed up to $BACKUP"
        }
    else
        BACKUP="${CLAUDE_HOME}-backup-$(date +%Y%m%d%H%M%S)"
        mv "$CLAUDE_HOME" "$BACKUP"
        log_success "Backed up to $BACKUP"
    fi
fi

# Clone and install
if [[ "$MODE" == "fresh" ]]; then
    log_info "Cloning GRIP to ~/.claude..."
    git clone "$REPO" "$CLAUDE_HOME"
    cd "$CLAUDE_HOME"
    ./scripts/install.sh --non-interactive 2>/dev/null || ./scripts/install.sh
elif [[ "$MODE" == "merge" ]]; then
    TEMP_DIR=$(mktemp -d)
    log_info "Cloning GRIP to temp directory..."
    git clone "$REPO" "$TEMP_DIR/grip"

    log_info "Running merge installation..."
    cd "$TEMP_DIR/grip"

    # Run install.sh which handles merge scenario
    ./scripts/install.sh
    rm -rf "$TEMP_DIR"
fi

# Check for Claude Code
echo ""
if ! command -v claude &>/dev/null; then
    log_warn "Claude Code CLI not found."
    echo ""
    read -rp "Install Claude Code? [y/N]: " install_claude
    if [[ "$install_claude" =~ ^[Yy]$ ]]; then
        log_info "Installing Claude Code..."
        curl -fsSL https://claude.ai/install.sh | bash
    else
        echo ""
        log_info "Install Claude Code later:"
        echo "  curl -fsSL https://claude.ai/install.sh | bash"
    fi
else
    log_success "Claude Code already installed"
fi

# ============================================================================
# PATH & CONFLICT DETECTION (sourced from lib/install-utils.sh)
# ============================================================================

# Source shared utilities (available after clone)
if [[ -f "$CLAUDE_HOME/lib/install-utils.sh" ]]; then
    source "$CLAUDE_HOME/lib/install-utils.sh"
    setup_shell_path
    detect_node_conflicts
else
    log_warn "install-utils.sh not found, skipping PATH setup"
fi

echo ""
log_success "GRIP installed successfully!"
echo ""
echo "Next steps:"
echo "  1. Reload your shell: source ~/.zshrc (or ~/.bashrc, or restart terminal)"
echo "  2. Run: grip"
echo "  3. GRIP will run onboarding wizard on first launch"
echo ""
echo "Troubleshooting:"
echo "  - If 'grip' not found: check ~/.zshrc contains GRIP PATH"
echo "  - If 'claude' not found: curl -fsSL https://claude.ai/install.sh | bash"
echo "  - Run 'claude doctor' to diagnose issues"
echo ""
