#!/usr/bin/env bash
# grip-popup.sh — Launch GRIP digest viewer as tmux popup or standalone.
#
# Usage:
#   grip-popup.sh                    # Auto-detect: tmux popup or direct
#   grip-popup.sh --plain            # Force plain text mode
#   grip-popup.sh --json             # Output raw JSON
#   grip-popup.sh --width 100 --height 30  # Custom tmux popup dimensions
#   grip-popup.sh --no-tmux          # Force direct mode (skip tmux detection)
#   grip-popup.sh --help             # Show this help

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
VIEWER="${CLAUDE_DIR}/lib/grip_viewer.py"

# Defaults
POPUP_WIDTH=80
POPUP_HEIGHT=24
FORCE_NO_TMUX=false
VIEWER_ARGS=()

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<'USAGE'
grip-popup.sh — Launch GRIP digest viewer as tmux popup or standalone.

Usage:
  grip-popup.sh [options]

Options:
  --plain          Force plain text mode (passed to grip_viewer.py)
  --json           Output raw JSON (passed to grip_viewer.py)
  --width W        Tmux popup width in columns (default: 80)
  --height H       Tmux popup height in rows (default: 24)
  --no-tmux        Skip tmux detection, run viewer directly
  --help           Show this help message

When running inside tmux, the viewer opens in a centered popup overlay.
Outside tmux (or with --no-tmux), the viewer runs directly in the terminal.
USAGE
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            usage
            exit 0
            ;;
        --plain)
            VIEWER_ARGS+=("--plain")
            shift
            ;;
        --json)
            VIEWER_ARGS+=("--json")
            shift
            ;;
        --width)
            POPUP_WIDTH="${2:?--width requires a value}"
            shift 2
            ;;
        --height)
            POPUP_HEIGHT="${2:?--height requires a value}"
            shift 2
            ;;
        --no-tmux)
            FORCE_NO_TMUX=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")"

if [[ -z "$PYTHON" ]]; then
    echo "Error: python3 or python not found in PATH" >&2
    exit 1
fi

if [[ ! -f "$VIEWER" ]]; then
    echo "Error: grip_viewer.py not found at ${VIEWER}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Clean exit on SIGINT/SIGTERM
# ---------------------------------------------------------------------------
cleanup() {
    exit 0
}
trap cleanup INT TERM

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
viewer_cmd="${PYTHON} ${VIEWER} ${VIEWER_ARGS[*]:-}"

if [[ "$FORCE_NO_TMUX" == "true" ]]; then
    # User explicitly requested direct mode
    exec $viewer_cmd
elif [[ -n "${TMUX:-}" ]]; then
    # Inside tmux — use display-popup for overlay effect
    tmux display-popup \
        -E \
        -w "$POPUP_WIDTH" \
        -h "$POPUP_HEIGHT" \
        -T " GRIP Digest " \
        "$viewer_cmd"
else
    # Not in tmux — run directly
    exec $viewer_cmd
fi
