#!/usr/bin/env bash
#
# tui_links.sh — OSC 8 terminal hyperlink library for GRIP (Bash).
#
# Provides clickable file:// and https:// links in terminals that support OSC 8
# (iTerm2, WezTerm, Kitty, Ghostty). Degrades gracefully to plain text elsewhere.
#
# Usage:
#   source lib/tui_links.sh
#   file_link "/path/to/file.md" "Open file"
#   url_link "https://example.com" "Visit"
#

supports_hyperlinks() {
    # Check TERM_PROGRAM for known OSC 8 terminals
    case "${TERM_PROGRAM:-}" in
        iTerm.app|WezTerm|ghostty) return 0 ;;
    esac
    # Kitty sets TERM=xterm-kitty
    case "${TERM:-}" in
        xterm-kitty) return 0 ;;
    esac
    # VTE-based terminals (GNOME Terminal, Tilix)
    [ -n "${VTE_VERSION:-}" ] && return 0
    return 1
}

file_link() {
    # $1 = path (absolute or relative), $2 = display text (optional)
    local path="$1"
    local display="${2:-$(basename "$path")}"

    # Resolve to absolute path
    if [[ "$path" != /* ]]; then
        path="$(cd "$(dirname "$path")" 2>/dev/null && pwd)/$(basename "$path")"
    fi

    if supports_hyperlinks; then
        printf '\033]8;;file://%s\033\\%s\033]8;;\033\\' "$path" "$display"
    else
        printf '%s' "$display"
    fi
}

url_link() {
    # $1 = URL, $2 = display text (optional)
    local url="$1"
    local display="${2:-$url}"

    if supports_hyperlinks; then
        printf '\033]8;;%s\033\\%s\033]8;;\033\\' "$url" "$display"
    else
        printf '%s' "$display"
    fi
}
