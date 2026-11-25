#!/usr/bin/env bash
# grip-tmux-install.sh -- Install or uninstall GRIP tmux integration.
#
# Usage:
#   bash ~/.claude/lib/grip-tmux-install.sh             # Install
#   bash ~/.claude/lib/grip-tmux-install.sh --uninstall  # Uninstall
#   bash ~/.claude/lib/grip-tmux-install.sh --check      # Check if installed
#   bash ~/.claude/lib/grip-tmux-install.sh --help       # Show help
#
# Idempotent: running install twice does not duplicate the source line.
# The script modifies ~/.tmux.conf (creating it if needed).

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
TMUX_CONF="${TMUX_CONF_PATH:-$HOME/.tmux.conf}"
GRIP_CONF="${CLAUDE_DIR}/config/grip.tmux.conf"
SOURCE_LINE="source-file ${HOME}/.claude/config/grip.tmux.conf"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<'USAGE'
grip-tmux-install.sh -- Install or uninstall GRIP tmux integration.

Usage:
  grip-tmux-install.sh              Install (adds source-file line to tmux.conf)
  grip-tmux-install.sh --uninstall  Remove GRIP source line from tmux.conf
  grip-tmux-install.sh --check      Check if GRIP tmux integration is installed
  grip-tmux-install.sh --help       Show this help

Environment:
  TMUX_CONF_PATH  Override tmux config path (default: ~/.tmux.conf)
  CLAUDE_DIR      Override GRIP install dir (default: ~/.claude)
USAGE
}

# ---------------------------------------------------------------------------
# Check if the source line is already present
# ---------------------------------------------------------------------------
is_installed() {
    [ -f "$TMUX_CONF" ] && grep -qF "grip.tmux.conf" "$TMUX_CONF"
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
do_install() {
    # Verify grip.tmux.conf exists
    if [ ! -f "$GRIP_CONF" ]; then
        echo "ERROR: grip.tmux.conf not found at ${GRIP_CONF}" >&2
        echo "Is GRIP installed at ${CLAUDE_DIR}?" >&2
        exit 1
    fi

    # Idempotent: skip if already present
    if is_installed; then
        echo "GRIP tmux integration is already installed in ${TMUX_CONF}"
        echo "No changes made."
        return 0
    fi

    # Create tmux.conf if it does not exist
    if [ ! -f "$TMUX_CONF" ]; then
        echo "Creating ${TMUX_CONF}"
        touch "$TMUX_CONF"
    fi

    # Append the source line with a comment
    {
        echo ""
        echo "# GRIP tmux integration (added by grip-tmux-install.sh)"
        echo "$SOURCE_LINE"
    } >> "$TMUX_CONF"

    echo "Installed GRIP tmux integration in ${TMUX_CONF}"

    # Reload tmux config if inside a tmux session
    if [ -n "${TMUX:-}" ]; then
        tmux source-file "$TMUX_CONF" 2>/dev/null && \
            echo "Reloaded tmux config." || \
            echo "Note: could not reload tmux config. Run: tmux source-file ${TMUX_CONF}"
    else
        echo "Not inside tmux. To activate, run: tmux source-file ${TMUX_CONF}"
    fi
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
do_uninstall() {
    if [ ! -f "$TMUX_CONF" ]; then
        echo "No tmux.conf found at ${TMUX_CONF}. Nothing to uninstall."
        return 0
    fi

    if ! is_installed; then
        echo "GRIP tmux integration is not installed in ${TMUX_CONF}. Nothing to do."
        return 0
    fi

    # Remove the source line and the comment above it
    # Use a temp file to avoid in-place sed portability issues (macOS vs GNU)
    local tmp_file
    tmp_file=$(mktemp)
    grep -v "grip.tmux.conf" "$TMUX_CONF" | grep -v "# GRIP tmux integration" > "$tmp_file" || true
    mv "$tmp_file" "$TMUX_CONF"

    echo "Removed GRIP tmux integration from ${TMUX_CONF}"

    # Reload if inside tmux
    if [ -n "${TMUX:-}" ]; then
        tmux source-file "$TMUX_CONF" 2>/dev/null && \
            echo "Reloaded tmux config." || \
            echo "Note: could not reload tmux config. Run: tmux source-file ${TMUX_CONF}"
    fi
}

# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------
do_check() {
    if is_installed; then
        echo "INSTALLED: GRIP tmux integration is active in ${TMUX_CONF}"
        return 0
    else
        echo "NOT INSTALLED: No GRIP source line found in ${TMUX_CONF}"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "${1:-}" in
    --uninstall)
        do_uninstall
        ;;
    --check)
        do_check
        ;;
    --help|-h)
        usage
        ;;
    "")
        do_install
        ;;
    *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
esac
