#!/usr/bin/env bash
# grip-doctor-boot.sh — Standalone GRIP readiness check at OS login.
#
# Verifies all core GRIP features are configured and warmed up before
# any Claude Code session starts. Zero-token verification.
#
# Usage:
#   grip-doctor-boot.sh          # Check only (exit 0 = all clear)
#   grip-doctor-boot.sh --fix    # Auto-repair missing dependencies
#   grip-doctor-boot.sh --quiet  # Suppress output, log only
#
# Origin: Toopie (michaeltoop), Gen 501 live session.
# Cross-platform: macOS (zsh/bash), Linux (bash), Windows (Git Bash).

set -euo pipefail

CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
LOG_FILE="$CLAUDE_DIR/data/grip-doctor-boot.log"
FIX_MODE=false
QUIET=false
FAILURES=0
WARNINGS=0

INSTALL_LAUNCHD=false

for arg in "$@"; do
    case "$arg" in
        --fix)             FIX_MODE=true ;;
        --quiet)           QUIET=true ;;
        --install-launchd) INSTALL_LAUNCHD=true ;;
    esac
done

# --- Install launchd agent (macOS only) ---
if [ "$INSTALL_LAUNCHD" = true ]; then
    PLIST_PATH="$HOME/Library/LaunchAgents/com.grip.doctor.plist"
    SCRIPT_PATH="$CLAUDE_DIR/scripts/grip-doctor-boot.sh"
    LOG_DIR="$CLAUDE_DIR/logs"
    mkdir -p "$LOG_DIR"

    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.grip.doctor</string>
    <key>ProgramArguments</key>
    <array>
        <string>bash</string>
        <string>$SCRIPT_PATH</string>
        <string>--quiet</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$CLAUDE_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>86400</integer>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/grip-doctor-boot.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/grip-doctor-boot-error.log</string>
</dict>
</plist>
PLIST

    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load "$PLIST_PATH"
    echo "Installed and loaded com.grip.doctor launchd agent."
    echo "Runs at login + daily. Logs: $LOG_DIR/grip-doctor-boot.log"
    exit 0
fi

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    local level="$1" msg="$2"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local line="[$ts] [$level] $msg"
    echo "$line" >> "$LOG_FILE"
    if [ "$QUIET" = false ]; then
        case "$level" in
            PASS) echo "  [OK] $msg" ;;
            FAIL) echo "  [!!] $msg" ;;
            WARN) echo "  [??] $msg" ;;
            FIX)  echo "  [>>] $msg" ;;
            INFO) echo "  [--] $msg" ;;
        esac
    fi
}

check_pass() { log "PASS" "$1"; }
check_fail() { log "FAIL" "$1"; FAILURES=$((FAILURES + 1)); }
check_warn() { log "WARN" "$1"; WARNINGS=$((WARNINGS + 1)); }
check_fix()  { log "FIX"  "$1"; }

# --- Header ---
echo "" >> "$LOG_FILE"
log "INFO" "=== GRIP Doctor Boot Check ==="

# --- 1. GRIP installed ---
if [ -f "$CLAUDE_DIR/CLAUDE.md" ]; then
    check_pass "GRIP installed at $CLAUDE_DIR"
else
    check_fail "GRIP not installed (no CLAUDE.md at $CLAUDE_DIR)"
    # Can't continue without GRIP
    echo "GRIP not installed. Nothing to check."
    exit 1
fi

# --- 2. Python with mcp package ---
MCP_PYTHON=""
for py in python3 python3.13 python3.12 python3.11 python; do
    if command -v "$py" &>/dev/null && \
       "$py" -c "from mcp.server.fastmcp import FastMCP" 2>/dev/null; then
        MCP_PYTHON="$py"
        break
    fi
done

if [ -n "$MCP_PYTHON" ]; then
    check_pass "Python MCP available ($MCP_PYTHON)"
else
    check_fail "No Python with 'mcp' package found"
    if [ "$FIX_MODE" = true ]; then
        check_fix "Installing mcp package..."
        if command -v pip3 &>/dev/null; then
            pip3 install mcp 2>&1 >> "$LOG_FILE" && check_pass "mcp installed via pip3" || check_fail "pip3 install mcp failed"
        elif command -v uv &>/dev/null; then
            uv pip install mcp 2>&1 >> "$LOG_FILE" && check_pass "mcp installed via uv" || check_fail "uv pip install mcp failed"
        else
            check_fail "No pip3 or uv available to install mcp"
        fi
    fi
fi

# --- 3. Node.js available ---
if command -v node &>/dev/null; then
    NODE_VER="$(node --version 2>/dev/null || echo 'unknown')"
    check_pass "Node.js available ($NODE_VER)"
else
    check_fail "Node.js not found"
fi

# --- 4. grip-apps built ---
if [ -f "$CLAUDE_DIR/mcp-servers/grip-apps/dist/index.js" ]; then
    check_pass "grip-apps MCP server built"
else
    check_fail "grip-apps not built (dist/index.js missing)"
    if [ "$FIX_MODE" = true ]; then
        check_fix "Building grip-apps..."
        cd "$CLAUDE_DIR/mcp-servers/grip-apps"
        if [ ! -d node_modules ]; then
            npm install --production=false 2>&1 >> "$LOG_FILE"
        fi
        npm run build 2>&1 >> "$LOG_FILE" && check_pass "grip-apps built" || check_fail "grip-apps build failed"
        cd "$CLAUDE_DIR"
    fi
fi

# --- 5. code-index server exists ---
if [ -f "$CLAUDE_DIR/mcp-servers/code-index/server.py" ]; then
    check_pass "code-index MCP server present"
else
    check_fail "code-index server.py missing"
fi

# --- 6. code-index start script executable ---
if [ -x "$CLAUDE_DIR/mcp-servers/code-index/start.sh" ]; then
    check_pass "code-index start.sh executable"
else
    check_warn "code-index start.sh not executable"
    if [ "$FIX_MODE" = true ] && [ -f "$CLAUDE_DIR/mcp-servers/code-index/start.sh" ]; then
        chmod +x "$CLAUDE_DIR/mcp-servers/code-index/start.sh"
        check_fix "Made code-index start.sh executable"
    fi
fi

# --- 7. Universal MCP servers reachable (any config layer) ---
# Claude Code merges three layers: user (~/.claude.json), local (~/.claude.json
# projects[path]), and project (<cwd>/.mcp.json). A server at ANY layer counts.
# NOTE: --fix MUST NOT modify ~/.claude.json (user scope). Only CLAUDE_DIR/.mcp.json.
MISSING_SERVERS=""
for server in "grip-channel" "code-index" "grip-apps"; do
    found=false
    # Check project scope (CLAUDE_DIR/.mcp.json)
    if [ -f "$CLAUDE_DIR/.mcp.json" ] && grep -q "\"$server\"" "$CLAUDE_DIR/.mcp.json" 2>/dev/null; then
        found=true
    fi
    # Check user/local scope (~/.claude.json)
    if [ "$found" = false ] && [ -f "$HOME/.claude.json" ] && grep -q "\"$server\"" "$HOME/.claude.json" 2>/dev/null; then
        found=true
    fi
    # Check HOME-level project scope (~/.mcp.json) — legacy, some users have this
    if [ "$found" = false ] && [ -f "$HOME/.mcp.json" ] && grep -q "\"$server\"" "$HOME/.mcp.json" 2>/dev/null; then
        found=true
    fi
    if [ "$found" = false ]; then
        MISSING_SERVERS="$MISSING_SERVERS $server"
    fi
done
if [ -z "$MISSING_SERVERS" ]; then
    check_pass "Universal MCP servers reachable (checked all config layers)"
else
    check_fail "Universal MCP servers not found in any config layer:$MISSING_SERVERS"
fi

# --- 8. Embeddings venv ---
if [ -f "$CLAUDE_DIR/venv/bin/python3" ] || [ -f "$CLAUDE_DIR/venv/Scripts/python.exe" ]; then
    check_pass "Embeddings venv present"
else
    check_warn "Embeddings venv missing (optional — semantic search disabled)"
    if [ "$FIX_MODE" = true ]; then
        check_fix "Creating embeddings venv..."
        if command -v python3 &>/dev/null; then
            python3 -m venv "$CLAUDE_DIR/venv" 2>&1 >> "$LOG_FILE" && check_pass "venv created" || check_fail "venv creation failed"
        fi
    fi
fi

# --- 9. grip-channel daemon (optional) ---
if command -v lsof &>/dev/null && lsof -i :3101 &>/dev/null; then
    check_pass "grip-channel daemon running (port 3101)"
elif command -v ss &>/dev/null && ss -tln | grep -q ':3101 ' 2>/dev/null; then
    check_pass "grip-channel daemon running (port 3101)"
else
    check_warn "grip-channel daemon not running (WhatsApp/Slack bridge offline)"
fi

# --- 10. MCP dependency heal + live verify (activation engine) ---
# The re-entry for the /activate-grip Install phase: install deps, self-heal, and
# VERIFY every server with a live handshake (not a filesystem check — the #3896
# lesson), then write a signed receipt (which silences the SessionStart nag).
if [ "$FIX_MODE" = true ]; then
    check_fix "Healing + verifying MCP server dependencies..."
    GRIP_PY="$CLAUDE_DIR/venv/bin/python3"; [ -x "$GRIP_PY" ] || GRIP_PY=python3
    if PYTHONPATH="$CLAUDE_DIR/lib" "$GRIP_PY" "$CLAUDE_DIR/lib/grip_activation_health.py" >> "$LOG_FILE" 2>&1; then
        check_pass "MCP servers healed + verified live (signed receipt written)"
    else
        check_warn "Some MCP servers still offline — see $LOG_FILE"
    fi
fi

# --- Summary ---
echo ""
log "INFO" "Results: $FAILURES failures, $WARNINGS warnings"

if [ "$FAILURES" -eq 0 ]; then
    if [ "$QUIET" = false ]; then
        echo ""
        echo "  GRIP ready."
    fi
    exit 0
else
    if [ "$QUIET" = false ]; then
        echo ""
        echo "  GRIP has $FAILURES issue(s). Run with --fix to auto-repair."
    fi
    exit 1
fi
