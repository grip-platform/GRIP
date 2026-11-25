#!/usr/bin/env bash
# TUI Progress Library for GRIP
# Provides spinners, progress bars, and colored output
# Version: 1.0.0 | Gen: 259

# Prevent double-sourcing
[[ -n "${TUI_PROGRESS_LOADED:-}" ]] && return 0
readonly TUI_PROGRESS_LOADED=1

# Colors (ANSI escape codes)
readonly TUI_RED='\033[0;31m'
readonly TUI_GREEN='\033[0;32m'
readonly TUI_YELLOW='\033[1;33m'
readonly TUI_BLUE='\033[0;34m'
readonly TUI_CYAN='\033[0;36m'
readonly TUI_MAGENTA='\033[0;35m'
readonly TUI_NC='\033[0m'
readonly TUI_BOLD='\033[1m'
readonly TUI_DIM='\033[2m'

# Spinner characters (braille dots for smooth animation)
readonly TUI_SPINNER='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

# Alternative spinners (exported for external use)
# shellcheck disable=SC2034
readonly TUI_SPINNER_DOTS='⣾⣽⣻⢿⡿⣟⣯⣷'
# shellcheck disable=SC2034
readonly TUI_SPINNER_LINE='|/-\'
# shellcheck disable=SC2034
readonly TUI_SPINNER_ARROW='←↖↑↗→↘↓↙'

# Global spinner PID
TUI_SPINNER_PID=""

# Check if output is a terminal
tui_is_terminal() {
    [[ -t 1 ]]
}

# Start an animated spinner
# Usage: spinner_start "Loading..."
spinner_start() {
    local msg="${1:-Loading...}"
    local spinner="${2:-$TUI_SPINNER}"

    # Only show spinner if stdout is a terminal
    if ! tui_is_terminal; then
        echo "$msg"
        return 0
    fi

    (
        local i=0
        local len=${#spinner}
        while true; do
            local char="${spinner:i%len:1}"
            printf "\r${TUI_CYAN}%s${TUI_NC} %s" "$char" "$msg"
            i=$((i + 1))
            sleep 0.08
        done
    ) &
    TUI_SPINNER_PID=$!
    disown "$TUI_SPINNER_PID" 2>/dev/null || true
}

# Stop the spinner and show result
# Usage: spinner_stop [true|false] ["Custom message"]
spinner_stop() {
    local success="${1:-true}"
    local msg="${2:-}"

    if [[ -n "$TUI_SPINNER_PID" ]]; then
        kill "$TUI_SPINNER_PID" 2>/dev/null || true
        wait "$TUI_SPINNER_PID" 2>/dev/null || true
        TUI_SPINNER_PID=""
    fi

    if ! tui_is_terminal; then
        [[ -n "$msg" ]] && echo "$msg"
        return 0
    fi

    if [[ "$success" == "true" ]]; then
        printf "\r${TUI_GREEN}✓${TUI_NC} %s\033[K\n" "${msg:-Done}"
    else
        printf "\r${TUI_RED}✗${TUI_NC} %s\033[K\n" "${msg:-Failed}"
    fi
}

# Show a progress bar
# Usage: progress_bar 50 100 40 "Downloading"
progress_bar() {
    local current=$1
    local total=$2
    local width=${3:-40}
    local label=${4:-"Progress"}

    if ! tui_is_terminal; then
        echo "$label: $current/$total"
        return 0
    fi

    local percent=$((current * 100 / total))
    local filled=$((current * width / total))
    local empty=$((width - filled))

    printf "\r${TUI_CYAN}%s${TUI_NC} [" "$label"

    # Draw filled portion
    local i
    for ((i=0; i<filled; i++)); do
        printf "█"
    done

    # Draw empty portion
    for ((i=0; i<empty; i++)); do
        printf "░"
    done

    printf "] ${TUI_BOLD}%3d%%${TUI_NC}" "$percent"

    # Newline if complete
    if [[ $current -ge $total ]]; then
        printf "\n"
    fi
}

# Show a status message with icon
# Usage: status_msg info "Starting process..."
status_msg() {
    local type=$1
    local msg=$2

    if ! tui_is_terminal; then
        echo "[$type] $msg"
        return 0
    fi

    case "$type" in
        info)    printf "${TUI_BLUE}ℹ${TUI_NC} %s\n" "$msg" ;;
        success) printf "${TUI_GREEN}✓${TUI_NC} %s\n" "$msg" ;;
        warn)    printf "${TUI_YELLOW}⚠${TUI_NC} %s\n" "$msg" ;;
        error)   printf "${TUI_RED}✗${TUI_NC} %s\n" "$msg" ;;
        grip)    printf "${TUI_CYAN}◈${TUI_NC} %s\n" "$msg" ;;
        step)    printf "${TUI_MAGENTA}→${TUI_NC} %s\n" "$msg" ;;
        dim)     printf "${TUI_DIM}  %s${TUI_NC}\n" "$msg" ;;
    esac
}

# Show a header/title
# Usage: tui_header "GRIP Hot Reload"
tui_header() {
    local title=$1

    if ! tui_is_terminal; then
        echo "=== $title ==="
        return 0
    fi

    echo ""
    printf "${TUI_BOLD}${TUI_CYAN}◈ %s${TUI_NC}\n" "$title"
    echo ""
}

# Show a boxed message
# Usage: tui_box "Important notice"
tui_box() {
    local msg=$1
    local width=${2:-60}

    if ! tui_is_terminal; then
        echo "[$msg]"
        return 0
    fi

    local padding=$(( (width - ${#msg} - 2) / 2 ))
    local pad_str=""
    local i
    for ((i=0; i<padding; i++)); do
        pad_str+=" "
    done

    printf "${TUI_CYAN}┌"
    for ((i=0; i<width; i++)); do printf "─"; done
    printf "┐${TUI_NC}\n"

    printf "${TUI_CYAN}│${TUI_NC}${pad_str}${TUI_BOLD}%s${TUI_NC}${pad_str}"
    # Handle odd width
    if (( (width - ${#msg} - 2) % 2 == 1 )); then printf " "; fi
    printf "${TUI_CYAN}│${TUI_NC}\n"

    printf "${TUI_CYAN}└"
    for ((i=0; i<width; i++)); do printf "─"; done
    printf "┘${TUI_NC}\n"
}

# Countdown timer
# Usage: tui_countdown 5 "Restarting in"
tui_countdown() {
    local seconds=$1
    local msg="${2:-Starting in}"

    while [[ $seconds -gt 0 ]]; do
        printf "\r${TUI_CYAN}◈${TUI_NC} %s %d..." "$msg" "$seconds"
        sleep 1
        seconds=$((seconds - 1))
    done
    printf "\r${TUI_GREEN}✓${TUI_NC} %s now!\033[K\n" "${msg% in}"
}

# Clear the current line
tui_clear_line() {
    printf "\r\033[K"
}

# Move cursor up N lines
tui_cursor_up() {
    local lines=${1:-1}
    printf "\033[%dA" "$lines"
}

# Cleanup on script exit
_tui_cleanup() {
    if [[ -n "$TUI_SPINNER_PID" ]]; then
        kill "$TUI_SPINNER_PID" 2>/dev/null || true
        TUI_SPINNER_PID=""
    fi
}

trap '_tui_cleanup' EXIT INT TERM

# Export functions for subshells
export -f spinner_start spinner_stop progress_bar status_msg tui_header tui_box tui_countdown tui_clear_line tui_cursor_up tui_is_terminal 2>/dev/null || true
