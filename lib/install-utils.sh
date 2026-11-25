#!/usr/bin/env bash
#
# GRIP Install Utilities
# Shared functions for install scripts (DRY principle)
#
# Usage: source "$CLAUDE_DIR/lib/install-utils.sh"
#

# Prevent double-sourcing
[[ -n "${_GRIP_INSTALL_UTILS_LOADED:-}" ]] && return 0
readonly _GRIP_INSTALL_UTILS_LOADED=1

# ============================================================================
# PATH SETUP
# ============================================================================

setup_shell_path() {
    local path_line='export PATH="$HOME/.claude/bin:$PATH"'
    local marker="# GRIP PATH"

    # Source logging if available
    if type log_info &>/dev/null; then
        log_info "Configuring shell PATH..."
    fi

    # Zsh
    if [[ -f "$HOME/.zshrc" ]]; then
        if ! grep -q "$marker" "$HOME/.zshrc" 2>/dev/null; then
            echo "" >> "$HOME/.zshrc"
            echo "$marker" >> "$HOME/.zshrc"
            echo "$path_line" >> "$HOME/.zshrc"
            type log_success &>/dev/null && log_success "Added GRIP to PATH in ~/.zshrc"
        fi
    fi

    # Bash - .bashrc
    if [[ -f "$HOME/.bashrc" ]]; then
        if ! grep -q "$marker" "$HOME/.bashrc" 2>/dev/null; then
            echo "" >> "$HOME/.bashrc"
            echo "$marker" >> "$HOME/.bashrc"
            echo "$path_line" >> "$HOME/.bashrc"
            type log_success &>/dev/null && log_success "Added GRIP to PATH in ~/.bashrc"
        fi
    fi

    # Bash - .bash_profile (macOS uses this)
    if [[ -f "$HOME/.bash_profile" ]]; then
        if ! grep -q "$marker" "$HOME/.bash_profile" 2>/dev/null; then
            echo "" >> "$HOME/.bash_profile"
            echo "$marker" >> "$HOME/.bash_profile"
            echo "$path_line" >> "$HOME/.bash_profile"
            type log_success &>/dev/null && log_success "Added GRIP to PATH in ~/.bash_profile"
        fi
    fi

    # If no shell config exists, create .bashrc
    if [[ ! -f "$HOME/.zshrc" && ! -f "$HOME/.bashrc" && ! -f "$HOME/.bash_profile" ]]; then
        echo "$marker" >> "$HOME/.bashrc"
        echo "$path_line" >> "$HOME/.bashrc"
        type log_success &>/dev/null && log_success "Created ~/.bashrc with GRIP PATH"
    fi
}

# ============================================================================
# NODE CONFLICT DETECTION
# ============================================================================

detect_node_conflicts() {
    local conflicts_found=false

    type log_info &>/dev/null && log_info "Checking for installation conflicts..."

    # Check for npm global claude-code installation (deprecated)
    if command -v npm &>/dev/null; then
        if npm list -g @anthropic-ai/claude-code 2>/dev/null | grep -q "claude-code"; then
            echo ""
            type log_warn &>/dev/null && log_warn "Found npm global install: @anthropic-ai/claude-code"
            echo "       This installation method is deprecated and may conflict with GRIP."
            echo "       Consider removing: npm uninstall -g @anthropic-ai/claude-code"
            conflicts_found=true
        fi
    fi

    # Check for multiple 'claude' binaries in PATH
    local claude_paths
    claude_paths=$(which -a claude 2>/dev/null | sort -u || true)
    local claude_count
    # grep -c prints 0 AND exits 1 when nothing matches; `|| echo "0"` added a
    # second 0 and broke the comparison below on a machine with no claude.
    claude_count=$(printf '%s\n' "$claude_paths" | grep -c . || true)
    if [[ $claude_count -gt 1 ]]; then
        echo ""
        type log_warn &>/dev/null && log_warn "Multiple 'claude' binaries found in PATH:"
        echo "$claude_paths" | while read -r path; do echo "       - $path"; done
        echo "       This may cause unexpected behavior."
        conflicts_found=true
    fi

    if [[ "$conflicts_found" == "false" ]]; then
        type log_success &>/dev/null && log_success "No installation conflicts detected"
    fi
}

# ============================================================================
# STATE DIRECTORY SETUP
# ============================================================================

# State directories (GRASP consolidated state management)
readonly GRIP_STATE_DIRS=(
    "state/approvals"
    "state/audit"
    "state/decisions"
    "state/features"
    "state/integrations"
    "state/memory"
    "state/rsi"
    "state/session"
)

setup_state_dirs() {
    local claude_dir="${1:-$HOME/.claude}"

    for state_dir in "${GRIP_STATE_DIRS[@]}"; do
        mkdir -p "$claude_dir/$state_dir"
    done

    type log_success &>/dev/null && log_success "State directories initialized"
}
