#!/usr/bin/env bash
#
# Start Copilot Dashboard
# Starts both the API server and the React frontend dev server.
#
# Usage:
#   ./scripts/start-aide.sh          # Start both API + frontend
#   ./scripts/start-aide.sh --api    # API only
#   ./scripts/start-aide.sh --web    # Frontend only
#

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
API_SCRIPT="$CLAUDE_DIR/scripts/aide-api.py"
WEB_DIR="$CLAUDE_DIR/apps/grip-web"
API_PORT="${COPILOT_API_PORT:-8080}"
WEB_PORT="${COPILOT_WEB_PORT:-3000}"

# Cleanup on exit
cleanup() {
    if [[ -n "${API_PID:-}" ]]; then
        kill "$API_PID" 2>/dev/null || true
    fi
    if [[ -n "${WEB_PID:-}" ]]; then
        kill "$WEB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

start_api() {
    if ! command -v uvicorn &>/dev/null; then
        echo "ERROR: uvicorn not found. Install: pip install uvicorn fastapi pyyaml"
        return 1
    fi

    echo "Starting Copilot API on port $API_PORT..."
    python3 "$API_SCRIPT" --port "$API_PORT" &
    API_PID=$!
    echo "API PID: $API_PID"
}

start_web() {
    if [[ ! -d "$WEB_DIR/node_modules" ]]; then
        echo "Installing frontend dependencies..."
        (cd "$WEB_DIR" && npm install)
    fi

    echo "Starting Copilot frontend on port $WEB_PORT..."
    (cd "$WEB_DIR" && npm run dev) &
    WEB_PID=$!
    echo "Frontend PID: $WEB_PID"
}

case "${1:-all}" in
    --api)
        start_api
        wait "$API_PID"
        ;;
    --web)
        start_web
        wait "$WEB_PID"
        ;;
    all|*)
        start_api
        # Wait for API to be ready (poll health endpoint, max 10s)
        echo "Waiting for API..."
        for i in $(seq 1 20); do
            if curl -sf "http://localhost:$API_PORT/api/copilot/health" >/dev/null 2>&1; then
                echo "API ready."
                break
            fi
            sleep 0.5
        done
        start_web
        echo ""
        echo "Copilot Dashboard running:"
        echo "  API:      http://localhost:$API_PORT/api/copilot/health"
        echo "  Frontend: http://localhost:$WEB_PORT/copilot"
        echo ""
        echo "Press Ctrl+C to stop both."
        wait
        ;;
esac
