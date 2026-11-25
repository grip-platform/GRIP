#!/usr/bin/env bash
# GRIP Pair-Mode — One-command session monitoring and steering.
#
# Usage:
#   grip-pair.sh              Connect to session (auto-discover, auto-start server)
#   grip-pair.sh --steer      Connect with steering control
#   grip-pair.sh --list       List active sessions
#   grip-pair.sh --rich       Use Rich TUI monitor (default if available)
#   grip-pair.sh --simple     Force simple monitor (no Rich TUI)
#   grip-pair.sh --stop       Stop control server for a session
#
# Requires: python3, jq

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
LIB_DIR="$CLAUDE_DIR/lib"

# Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_DIM="\033[2m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_RED="\033[31m"
C_CYAN="\033[36m"

# Parse flags
MODE="connect"
STEER=false
USE_RICH="auto"

while [[ $# -gt 0 ]]; do
    case $1 in
        --list|-l)   MODE="list" && shift ;;
        --steer|-s)  STEER=true && shift ;;
        --rich|-r)   USE_RICH="yes" && shift ;;
        --simple)    USE_RICH="no" && shift ;;
        --stop)      MODE="stop" && shift ;;
        --help|-h)   MODE="help" && shift ;;
        *)           echo -e "${C_RED}Unknown option: $1${C_RESET}" && exit 1 ;;
    esac
done

show_help() {
    echo -e "${C_BOLD}GRIP Pair-Mode${C_RESET} — Cross-terminal session monitoring"
    echo ""
    echo "Usage:"
    echo "  grip pair              Auto-discover and connect"
    echo "  grip pair --steer      Connect with steering control"
    echo "  grip pair --list       List active sessions"
    echo "  grip pair --rich       Force Rich TUI monitor"
    echo "  grip pair --simple     Force simple monitor"
    echo "  grip pair --stop       Stop a control server"
    echo ""
    echo "Steering commands (when connected with --steer):"
    echo "  inject: <text>         Send context to the agent"
    echo ""
}

discover_sessions() {
    python3 "$LIB_DIR/session-lister.py" 2>/dev/null
}

show_picker() {
    local sessions="$1"
    local count
    count=$(echo "$sessions" | jq '. | length')

    # All display output to stderr so stdout is clean for the return value
    echo -e "\n${C_BOLD}Active Sessions${C_RESET}" >&2
    echo "────────────────────────────────────────────────" >&2

    local i=1
    while IFS='|' read -r sid project branch uptime mon; do
        local mon_icon="·"
        [[ "$mon" == "true" ]] && mon_icon="${C_GREEN}●${C_RESET}"
        printf "  ${C_BOLD}%d${C_RESET}) ${C_CYAN}%-8s${C_RESET}  %-15s  %-10s  %6s  %b\n" \
            "$i" "${sid:0:8}" "$project" "$branch" "$uptime" "$mon_icon" >&2
        i=$((i + 1))
    done < <(echo "$sessions" | jq -r '.[] | "\(.session_id)|\(.project_name)|\(.branch)|\(.uptime_display)|\(.monitoring_enabled)"')

    echo "────────────────────────────────────────────────" >&2
    echo -ne "\nSelect session (1-$count): " >&2
    read -r choice

    if [[ "$choice" =~ ^[0-9]+$ ]] && [[ $choice -ge 1 ]] && [[ $choice -le $count ]]; then
        echo "$sessions" | jq -r ".[$((choice-1))].session_id"
    else
        echo ""
    fi
}

start_server() {
    local sid="$1"
    local socket_path="${TMPDIR:-/tmp}/grip-session-${sid}.sock"

    if [[ -S "$socket_path" ]]; then
        echo -e "${C_DIM}Server already running${C_RESET}" >&2
        return 0
    fi

    echo -e "${C_YELLOW}Starting control server for ${sid:0:8}...${C_RESET}" >&2
    nohup python3 "$LIB_DIR/session-control-server.py" "$sid" \
        >> "$CLAUDE_DIR/.session-server.log" 2>&1 &

    local server_pid=$!
    echo "$server_pid" > "${TMPDIR:-/tmp}/grip-session-server-${sid}.pid"

    # Wait for socket to appear
    local waited=0
    while [[ ! -S "$socket_path" ]] && [[ $waited -lt 5 ]]; do
        sleep 0.5
        waited=$((waited + 1))
    done

    if [[ -S "$socket_path" ]]; then
        echo -e "${C_GREEN}Server ready (PID: $server_pid)${C_RESET}" >&2
    else
        echo -e "${C_RED}Server failed to start${C_RESET}" >&2
        return 1
    fi
}

stop_server() {
    local sessions
    sessions=$(discover_sessions)
    local count
    count=$(echo "$sessions" | jq '. | length')

    if [[ $count -eq 0 ]]; then
        echo -e "${C_DIM}No active sessions${C_RESET}"
        return
    fi

    local sid
    if [[ $count -eq 1 ]]; then
        sid=$(echo "$sessions" | jq -r '.[0].session_id')
    else
        sid=$(show_picker "$sessions")
    fi

    if [[ -z "$sid" ]]; then
        return
    fi

    local pid_file="${TMPDIR:-/tmp}/grip-session-server-${sid}.pid"
    if [[ -f "$pid_file" ]]; then
        local server_pid
        server_pid=$(< "$pid_file")
        if kill -0 "$server_pid" 2>/dev/null; then
            kill "$server_pid"
            echo -e "${C_GREEN}Server stopped (PID: $server_pid)${C_RESET}"
        fi
        rm -f "$pid_file"
    fi
    rm -f "${TMPDIR:-/tmp}/grip-session-${sid}.sock"
}

connect_session() {
    local sid="$1"
    local steer_flag=""
    [[ "$STEER" == "true" ]] && steer_flag="--steer"

    # Determine monitor type
    local use_rich_monitor=false
    if [[ "$USE_RICH" == "yes" ]]; then
        use_rich_monitor=true
    elif [[ "$USE_RICH" == "auto" ]]; then
        if python3 -c "from rich.console import Console" 2>/dev/null; then
            use_rich_monitor=true
        fi
    fi

    if [[ "$use_rich_monitor" == "true" ]] && [[ -f "$LIB_DIR/session-monitor-rich.py" ]]; then
        python3 "$LIB_DIR/session-monitor-rich.py" connect "$sid" $steer_flag
    else
        python3 "$LIB_DIR/session-monitor.py" connect "$sid" $steer_flag
    fi
}

# ── Main ──────────────────────────────────────────────

if [[ "$MODE" == "help" ]]; then
    show_help
    exit 0
fi

if [[ "$MODE" == "list" ]]; then
    python3 "$LIB_DIR/session-monitor.py" list
    exit 0
fi

if [[ "$MODE" == "stop" ]]; then
    stop_server
    exit 0
fi

# Connect mode
sessions=$(discover_sessions)
count=$(echo "$sessions" | jq '. | length')

if [[ $count -eq 0 ]]; then
    echo -e "${C_RED}No active sessions found${C_RESET}"
    echo -e "${C_DIM}Start a Claude Code session first, then run grip pair${C_RESET}"
    exit 1
fi

# Select session
if [[ $count -eq 1 ]]; then
    SESSION_ID=$(echo "$sessions" | jq -r '.[0].session_id')
    BRANCH=$(echo "$sessions" | jq -r '.[0].branch')
    echo -e "${C_GREEN}Auto-selected${C_RESET} session ${C_CYAN}${SESSION_ID:0:8}${C_RESET} (${BRANCH})" >&2
else
    SESSION_ID=$(show_picker "$sessions")
    if [[ -z "$SESSION_ID" ]]; then
        echo -e "${C_RED}No session selected${C_RESET}"
        exit 1
    fi
fi

# Auto-start server if needed
MONITORING=$(echo "$sessions" | jq -r --arg sid "$SESSION_ID" '.[] | select(.session_id == $sid) | .monitoring_enabled')
if [[ "$MONITORING" != "true" ]]; then
    start_server "$SESSION_ID"
fi

# Connect
connect_session "$SESSION_ID"
